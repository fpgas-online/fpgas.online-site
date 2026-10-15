"""The switch port dashboard (#67, #130): every switch and every port, read side only: which hardware is on each port
(the machines the gateway last leased an address to on it, with the /fleet/ model, board type and State), whether the
port's network reading is as it should be, then the switch's own reading, one value a column.

The reading is done by `snmp_switch.dashboard` (fpgas.online-poe), cached for 15 s in the site's "poe" cache, so
the number of viewers does not change the number of reads. This module only lays the result out: the page
server-side (complete without JavaScript), and the same rows as JSON for the page's auto-refresh. The browser
never talks to a switch, and no community or credential is in anything built here.

A port's link change reaches an open page sooner than that cache can: the gateway's switch poller publishes it, the
fleet consumer forwards it on ws/switches/ (pistat), and switch-events.js draws it on the port's row and front-panel
mark straight from the record. The 15 s refresh stays as the fallback; it draws the record again
after each rebuild until a reading at least as new as the record has caught up with it."""

import dataclasses
import functools
import logging
import re
import socket
from datetime import datetime
from pathlib import Path

from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from fleet.services import MISSING
from fleet.timeago import ago
from ttsite.middleware import serves_ttsite

from .. import fpga, inventory_check, localtime, panel, states
from ..states import FAILED, PASSED

REFRESH_SECONDS = 15
NONE = "–"  # shown for a value that is not known
# An LLDP port ID that is a MAC address (a Pi announces its own): it says nothing the MAC column does not.
MAC_ID = re.compile(r"^[0-9A-Fa-f]{2}([:-]?[0-9A-Fa-f]{2}){5}$")
NOT_INSTALLED = "The switch reader is not installed on this site yet."
READ_FAILED = "The switches could not be read just now. This page tries again by itself."

logger = logging.getLogger(__name__)


class _Unavailable(Exception):
    """The switches cannot be shown; `message` says why."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _reader():
    """`cached_read_all`, or None when the installed poe package does not have the dashboard module yet."""
    try:
        from snmp_switch.dashboard import cached_read_all
    except ImportError:
        return None
    return cached_read_all


def _config_error():
    try:
        from snmp_switch.switches import PoeConfigError
    except ImportError:
        return ()
    return (PoeConfigError,)


def _read():
    """The switch views, or raises _Unavailable with the text to show instead."""
    read = _reader()
    if read is None:
        raise _Unavailable(NOT_INSTALLED)
    try:
        return read(caches["poe"])
    except _config_error() as exc:
        raise _Unavailable(str(exc)) from exc
    except Exception as exc:  # any other failure of the reader or the cache: a message, never a 500
        logger.exception("switch dashboard: the reader failed")
        raise _Unavailable(READ_FAILED) from exc


def rate(bps):
    """A rate in bits per second as whole kbit/s, one unit for the column (#130: one form per column); NONE when the
    switch gave none."""
    if bps is None:
        return NONE
    return f"{bps / 1000:.0f}"


def when(iso):
    """An ISO 8601 time from the reader as a date and time in the current zone (the page names it once); "" when
    not given."""
    if not iso:
        return ""
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if timezone.is_naive(moment):
        return iso
    return timezone.localtime(moment).strftime("%Y-%m-%d %H:%M:%S")


# The PoE column (#130): the port's PoE state as one character, its words on hover and for a screen reader; the watts
# are a column of their own. (mark, css class, words, sort key: power flowing first, a fault before the rest.)
POE_MARKS = {
    "delivering": ("⚡", "mgmt-poe-on", "PoE delivering power", 1),
    "fault": ("!", "mgmt-state-attention", "PoE fault", 0),
    # a magnifying glass: the port is looking for a device to power (Tim, 16 Oct 2026, site #139: "The searching PoE
    # state should be a magnifying glass"; ○ was also /fleet/'s "missing")
    "searching": ("🔍", "mgmt-poe-idle", "PoE on, searching: nothing drawing power", 2),
    "disabled": ("⊗", "mgmt-poe-off", "PoE off", 3),
}
NO_POE = ("–", "mgmt-test-none", "no PoE on this port", 4)


def poe_cell(state, has_poe=True):
    """(text, css class, title, sort key) of a port's PoE cell. A state this page does not know is shown as the switch
    gave it, and a PoE port the switch gave no state for says "not read" (fail loud, in the cell: both are a Net ✗
    too); a port without PoE is –."""
    if state in POE_MARKS:
        return POE_MARKS[state]
    if state is None:
        if has_poe:
            return "not read", "mgmt-state-attention", "the switch gave no PoE state for this PoE port", 0
        return NO_POE
    return str(state)[:20], "mgmt-state-attention", f"the switch gave PoE state {str(state)[:40]!r}, which this page " \
        "does not know", 0


def watts(state, value):
    """The watts a delivering port draws ("8.6"); "" for any other port, or where the switch gives none."""
    return f"{value:.1f}" if state == "delivering" and value is not None else ""


def link_speed(mbps):
    """A link speed as Tim asked (2026-10-09): "10M", "100M", "1G", "10G"; "2.5G" for a speed between."""
    if mbps >= 1000:
        return f"{mbps / 1000:g}G"
    return f"{mbps}M"


def _count(n):
    return NONE if n is None else str(n)


def _fleet_url(request, serial):
    try:
        return reverse("fleet-detail", args=[serial], urlconf=getattr(request, "urlconf", None))
    except NoReverseMatch:  # no fleet pages on this host
        return ""


# --- the hardware on each port (#130): the machines the gateway last leased an address to on it ---

# the speed a model's Ethernet links at (the Pi 3B+'s gigabit port sits behind USB 2, but links at 1G); a model not
# here is not compared (its Net title says so)
LINK_SPEEDS = {"Pi 3B+": 1000, "Pi 3B": 100, "Pi 4B": 1000, "Pi 5": 1000, "Pi CM4": 1000, "Pi 400": 1000,
               "OPi PC": 100}
UNREGISTERED = "?"
UNREGISTERED_WORDS = "not registered"


def _machine(request, host, observed):
    """One machine as a port row shows it: the /fleet/ table's values (model, board type, State mark and its words)
    and the gateway's observation that puts it on this port, with its time."""
    from .fpgas import SEVERITY  # not at import: views.fpgas imports views.index, which imports this module

    fields = fpga.pi_fields(host.machine)
    word = states.WORDS[host.condition]
    return {
        "serial": fields.serial,
        "url": _fleet_url(request, fields.serial),
        "label": fpga.pi_label(host.machine),
        # the model in the /fleet/ short form; a host with no label says what keeps it from one (fail loud)
        "model": fields.short or fields.note,
        "model_title": fields.label if not fields.why else fields.why,
        # the board's type alone, as /fleet/'s Board column ("Acorn"; "Acorn + TT FPGA" for two); its variant on hover
        "board": " + ".join(b["title"] for b in host.boards) or host.board_type,
        "board_title": " + ".join(b["type"] for b in host.boards) or host.board_type,
        "condition": host.condition,
        "mark": states.MARKS[host.condition],
        "state_title": word + (f": {host.why}" if host.why else ""),
        "severity": SEVERITY[host.condition],
        "lease_ago": _ago_moment(observed.at),
        "lease_at": timezone.localtime(observed.at).strftime("%Y-%m-%d %H:%M:%S"),
        "lease_sort": int(observed.at.timestamp()),
    }


def _ago_moment(moment):
    try:
        return ago(moment)
    except ValueError as exc:  # a lease stamped ahead of this clock: said in the cell
        return str(exc)


def on_ports(request):
    """{(switch, port): [machine]} of every machine the gateway has leased an address to on a port (fpga.seen_ports:
    its newest lease record that names a port), newest lease first. An observation with its time, never a remembered
    table: a machine moved to another port is shown there once the gateway leases it an address there."""
    seen = fpga.seen_ports()
    if not seen:
        return {}
    out = {}
    for host in fpga.hosts():
        observed = seen.get(host.machine.pk)
        if observed is not None:
            out.setdefault((observed.switch, observed.port), []).append((observed.at, _machine(request, host, observed)))
    return {key: [m for _, m in sorted(found, key=lambda f: f[0], reverse=True)] for key, found in out.items()}


def net_check(port, has_poe, role, machines, failures, known, vlans_checked, errors=None):
    """(ok, title) of a port's Net cell: is the network reading as it should be? Not ok for an inventory failure with
    no known cause (management.inventory_check), a PoE fault or a PoE state the page cannot read, a link down under a
    machine that is not missing, a link slower than the model on it links at, an error counter that rose since the
    previous reading (`errors`: error_change's). A failure of a known cause (poe #16, #18) is not a ✗: it is one line
    above the switch's drawing, and the title says the port is as it should be apart from it."""
    wrong, compared = list(failures), []
    if vlans_checked and not known:
        compared.append("VLANs against the inventory")
    poe = port.poe_state
    compared.append("PoE")
    if poe == "fault":
        wrong.append("PoE fault")
    elif poe is None and has_poe:
        wrong.append("PoE state not read")
    elif poe is not None and poe not in POE_MARKS:
        wrong.append(f"PoE state {str(poe)[:40]!r} not known")
    if machines and machines[0]["condition"] != MISSING:
        newest = machines[0]
        compared.append(f"link and speed for the {newest['model'] or newest['serial']} on it")
        if not port.link_up:
            wrong.append(f"link down under {newest['label']} ({states.WORDS[newest['condition']]})")
        else:
            want = LINK_SPEEDS.get(newest["model"])
            if want is None:
                compared[-1] += " (no speed known for this model)"
            elif port.speed_mbps is not None and port.speed_mbps < want:
                wrong.append(f"link {link_speed(port.speed_mbps)}; a {newest['model']} links at {link_speed(want)}")
    rose, note = errors if errors is not None else ([], "")
    wrong.extend(rose)
    if note:
        compared.append(note)
    issues = ", ".join(dict.fromkeys(k.rsplit("(", 1)[-1].rstrip(")") for k in known))
    if wrong:
        title = "not as it should be: " + "; ".join(wrong) + (f"; and the known issue above ({issues})" if known else "")
    elif known:
        title = f"as it should be apart from the known issue above ({issues}): " + ", ".join(compared)
    else:
        title = "as it should be: " + ", ".join(compared) + (" (an infrastructure port)" if role else "")
    return not wrong, title


# The error counters (the review of #137): the switch counts errors from its own start, so a total alone says nothing
# of now. Each switch's previous reading's counters are kept in the "poe" cache, beside the reading itself, for as
# long as a page is likely to be open; a counter that rose since then is a Net ✗.
ERRORS_KEY = "switch-dashboard-errors-{}"
ERRORS_KEEP_SECONDS = 3600


def error_baselines(view):
    """{port: (rx, tx)} of the previous reading of switch `view` and that reading's time ("" and {} when there is
    none yet), keeping this reading as the next one's previous. A reading is known by its time (good_at): the many
    requests within one reading all compare with the same previous one."""
    if not view.reachable or view.index is None:
        return "", {}
    cache = caches["poe"]
    key = ERRORS_KEY.format(view.index)
    at = getattr(view, "good_at", None) or view.read_at or ""
    counts = {p.port: (p.rx_errors, p.tx_errors) for p in view.ports}
    stored = cache.get(key)
    if stored and stored["current"]["at"] == at:
        previous = stored.get("previous")
    else:
        previous = stored["current"] if stored else None
        cache.set(key, {"current": {"at": at, "counts": counts}, "previous": previous}, ERRORS_KEEP_SECONDS)
    if not previous:
        return "", {}
    return previous["at"], previous["counts"]


def error_change(port, since, before):
    """([what rose], a note for the title) of a port's error counters against the previous reading (`before`: its
    (rx, tx), taken at `since`)."""
    if not since or before is None:
        return [], "error counters (no earlier reading to compare with yet)"
    when_ = when(since)
    rose, notes = [], []
    for word, now, then in (("rx", port.rx_errors, before[0]), ("tx", port.tx_errors, before[1])):
        if now is None or then is None:
            continue
        if now > then:
            rose.append(f"{word} errors +{now - then} since {when_}")
        elif now < then:
            notes.append(f"{word} error count went back since {when_} (the switch restarted its count)")
    return rose, "; ".join(notes) or f"error counters (none rose since {when_})"


def _fleet_hosts():
    """{short hostname: serial} of the newest machine on each hostname: ONE query for every port."""
    from fleet.services import machine_hosts

    return machine_hosts()


GATEWAY = "gateway"
# Every LLDP neighbour is shown by the name it announces (Tim, 16 Oct 2026, site #148: "Why is the device's LLDP
# information hidden for port 11 on switch 2?"): an unregistered device on a port is the one a reader needs to name.
# Two names stay off the public page, each said by its role: the gateway's own host names ("gateway"), and the device
# beyond the house uplink ("house uplink"), which is not fpgas.online's (RULES: fpgas.online names no upstream host).
# The VLAN names the page shows (vlan_cell): a board's or a switch's name; every other VLAN name is its number.
SHOWN_NAME = re.compile(r"^(pi-sw\d+-p\d+|sw-[\w-]+)$", re.IGNORECASE)


@functools.cache
def _gateway_names():
    """The host names this site's gateway goes by: its fully qualified name and its short name. The site runs on the
    gateway, so they are its own. Read once per process (a resolver call). With none, the page cannot keep the
    gateway's name off it, so it refuses rather than guess."""
    names = {socket.getfqdn(), socket.gethostname()}
    names |= {n.split(".")[0] for n in names}
    names = frozenset(n.lower() for n in names if n and not n.startswith("localhost"))
    if not names:
        raise ImproperlyConfigured("switch dashboard: this host reports no name of its own, so the gateway's name "
                                   "cannot be kept off the public page")
    return names


def _host(name, house=()):
    """A host name as the page shows it: its domain dropped, "gateway" for the gateway's own names, the house uplink's
    device as "house uplink"."""
    short = name.split(".")[0]
    if name.lower() in _gateway_names() or short.lower() in _gateway_names():
        return GATEWAY
    return label(short, house)


def neighbour(name, house=()):
    """An LLDP neighbour's system name for the public page: the host name it announces, its domain dropped, whoever it
    is (#148); "gateway" and "house uplink" for those two."""
    if not name:
        return ""
    return _host(name, house)


HOUSE_UPLINK = "house uplink"


def _token(name):
    """A pattern for `name` as a whole part of a label: not inside a longer word ("gwhost" is not in "gwhost2"), but
    joined by any other character ("gwhost-eth0", "eth0_gwhost")."""
    return rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])"


def house_names(spec, ports):
    """The names the house uplink's own label gives the device beyond it ("uplink.<device>" gives "<device>"): a
    private device's name, masked wherever a label of this switch repeats it."""
    if spec is None or spec is NO_INVENTORY or spec.house_uplink_port is None:
        return ()
    text = next((p.label or "" for p in ports if p.port == spec.house_uplink_port), "")
    _, dot, host = text.partition(".")
    return (host, host.split(".")[0]) if dot and host else ()


def label(text, house=()):
    """A port label (ifAlias) for the public page, in full (Tim, #89: "report the full name"; rule 20), with two
    names replaced: the gateway's own host names by "gateway", and the house uplink's device by "house uplink"
    (the coordinator's ruling, 11 Oct 2026). The switch's stale names are fpgas-online/fpgas.online-poe#14."""
    if not text:
        return ""
    for name in sorted(_gateway_names(), key=len, reverse=True):
        text = re.sub(_token(name), GATEWAY, text, flags=re.IGNORECASE)
    for name in sorted((n for n in house if n), key=len, reverse=True):
        text = re.sub(_token(name), HOUSE_UPLINK, text, flags=re.IGNORECASE)
    return text


def switch_name(name):
    """A switch's own sysName under the same rule as a neighbour's: its domain dropped, "gateway" for the gateway's."""
    if not name:
        return ""
    return _host(name)


INFRASTRUCTURE = "infrastructure"


# A switch the inventory should describe but does not (the configuration could not be read, or has no entry for
# it): its ports are shown with no label and no PoE button, rather than guess which are boards (fail closed).
NO_INVENTORY = object()
NO_INVENTORY_NOTE = "the switch inventory could not be read: port labels are hidden and no PoE button is live"


def _inventory():
    """{switch index: SwitchSpec} from the switches configuration the PoE views use (switches.yml): {} where there is
    none by design (the legacy single switch), None when it is configured but cannot be read."""
    try:
        from snmp_switch.switches import configured_specs

        return {spec.index: spec for spec in configured_specs()}
    except Exception:
        logger.exception("switch dashboard: the switches configuration could not be read")
        return None


def _spec(inventory, index):
    """The SwitchSpec of switch `index`; None for the legacy switch (no index, no inventory by design); NO_INVENTORY
    when the inventory should describe the switch and cannot."""
    if index is None:
        return None
    if inventory is None or index not in inventory:
        return NO_INVENTORY
    return inventory[index]


def port_role(spec, port):
    """The inventory's role of a port that is not a board port ("gateway trunk", "switch trunk", "house uplink",
    "infrastructure"), or None for a board port (1..access_ports) or a switch with no inventory. The coordinator,
    2026-10-09: an infrastructure port is labelled by its role, whatever the switch's own label says (it can name a
    private device)."""
    if spec is None or spec is NO_INVENTORY:
        return None
    # the named ports first: a trunk or uplink can sit inside 1..access_ports, and is never a board port (poe's own
    # bound, is_access_port, excludes them the same way)
    if port == spec.gateway_trunk_port:
        return "gateway trunk"
    if port in tuple(spec.downstream_trunk_ports or ()):
        return "switch trunk"
    if port == spec.house_uplink_port:
        return HOUSE_UPLINK
    return None if port <= spec.access_ports else INFRASTRUCTURE


# The PoE button's action by the port's state: on while it is disabled, off in every other state, a fault or a state
# the switch did not give included (Tim, 16 Oct 2026: "All access ports should *always* be switchable": a port in a
# fault is the one to switch off and on again).
POWER_ACTION = {"disabled": "on"}
POWER_DEFAULT = "off"
# The power control is the power symbol on every PoE port (Tim, 16 Oct 2026, site #139: "The power button should be a
# power button icon, not text", "Why does the PoE power button only appear on some switch ports?"): a button where a
# press would be accepted, else the same symbol greyed with the reason on hover and for a screen reader.
POWER_ICON = "⏻"
# Both urlconfs include snmp_switch.urls under snmp/; its paths have no names to reverse, as the board pages' own
# "/snmp/toggle" has none.
POWER_URL = "/snmp/power"


def _can_power(request, view, spec):
    """{port numbers of `view` that get a PoE button}: every access port, as the site's PoE policy (the one /snmp/power
    enforces, pibfpgas.poe.board_port) says; on a flat site, the registered ones, from ONE pass over the registry. A
    port the inventory names as an uplink, trunk or other infrastructure is not asked at all."""
    from pibfpgas.poe import board_ports

    if spec is NO_INVENTORY:
        return set()
    ports = [p.port for p in view.ports if port_role(spec, p.port) is None]
    return board_ports(request, view.index, ports) if ports else set()


def lldp_port(port, house=()):
    """The port the neighbour reports: its port id, or, where that is its MAC (lldpd's default on the Pis, which
    the MACs column already shows), its port description, the interface name (fpgas-online/fpgas.online-infra#268
    makes the Pis announce the name as the id itself)."""
    ident = port.lldp_port or ""
    if not MAC_ID.match(ident):
        return label(ident, house)
    # a poe older than fpgas-online/fpgas.online-poe#19 has no lldp_port_desc: the cell is empty, as before #89
    return label(getattr(port, "lldp_port_desc", None) or "", house)


def power_cell(has_poe, can_power, action, role, no_inventory, poe_title, tt_site=False):
    """(icon, words) of a port's power control: words are what a press does where there is a button (`action`), else
    why there is none. The reasons are the policy the server enforces on /snmp/power (pibfpgas.poe.board_port: every
    access port of a site with a switches file; on a flat site, a port a board registered on), not this page's."""
    if not has_poe:
        return "", "no PoE on this port"
    if can_power and action:
        return POWER_ICON, f"turn PoE {action}"
    if no_inventory:
        return POWER_ICON, "not switched: the switch inventory could not be read, so no port is known to be a board's"
    if role:
        return POWER_ICON, f"not switched from this page: the port's role is {role}"
    if tt_site:  # pibfpgas.poe.board_ports on the Tiny Tapeout site: a port a Pi reported a Tiny Tapeout board on
        return POWER_ICON, ("not switched: no Tiny Tapeout board was reported on this port, and this site switches "
                            "only a port one was reported on")
    return POWER_ICON, ("not switched: no board has registered on this port, and the site switches only a port a "
                        "board has registered on")


def _row(request, port, hosts, spec=None, can_power=False, vlan_names=None, netdevs=None, house=(), machines=(),
         has_poe=True, failures=(), known=(), errors_since="", errors_before=None):
    """A port's row: the hardware the gateway last saw on it (`machines`, newest first), whether its network reading is
    as it should be (`failures`: the inventory check's for this port with no known cause; `known`: those with one;
    `errors_since`, `errors_before`: the previous reading's time and this port's error counters then), then the
    switch's reading, one value a column (#130). `has_poe`: the switch's model gives the port PoE."""
    name = port.lldp_name or ""
    serial = hosts.get(name.split(".")[0]) if name else None
    macs = list(port.macs or [])
    role = port_role(spec, port.port) if spec is not NO_INVENTORY else None
    neighbour_name = neighbour(name, house)
    poe, poe_cls, poe_title, poe_sort = poe_cell(port.poe_state, has_poe)
    vlan, vlan_title = vlan_cell(port, vlan_names)
    # the gateway's interface for the port's VLAN belongs to a board port; an infrastructure port carries many
    # VLANs (all in the VLAN cell's title), and where the inventory is unknown a trunk cannot be told from a board
    board = spec is not NO_INVENTORY and port_role(spec, port.port) is None
    gw, gw_title = gw_cell(getattr(port, "pvid", None), netdevs) if board else (NONE, "not a board port")
    machines = list(machines)
    # something on a board port (a link, or power drawn) where the gateway has leased no registered machine an address
    powered = port.poe_state == "delivering"
    unregistered = UNREGISTERED if not machines and not role and (port.link_up or powered) else ""
    vlans_checked = spec is not None and spec is not NO_INVENTORY and (board or role == "gateway trunk")
    errors = error_change(port, errors_since, errors_before)
    net_ok, net_title = net_check(port, has_poe, role, machines, failures, known, vlans_checked, errors)
    speed = link_speed(port.speed_mbps) if port.link_up and port.speed_mbps else ""
    link_title = "link up" if port.link_up else "link down"
    # the switch's own label for the port (its configuration, an observation of the switch, stale in places: poe #14)
    # is on the port number's title for a board port; an infrastructure port's can name a private device, so it is
    # not shown; with no inventory, no label at all (fail closed)
    own = "" if spec is NO_INVENTORY or role else label(port.label, house)
    action = POWER_ACTION.get(port.poe_state or "", POWER_DEFAULT) if can_power and has_poe else ""
    power_icon, power_title = power_cell(has_poe, can_power, action, role, spec is NO_INVENTORY, poe_title,
                                         serves_ttsite(request))
    row = {
        "port": port.port,
        "port_title": f"the switch labels this port {own}" if own else "",
        "machines": machines,
        # the hardware columns sort the ports with a machine before the empty ones (data-sort "0 ..." before "1")
        "hw_sort": "0" if machines else "1",
        "role": role or "",
        "unregistered": unregistered,
        "unregistered_title": ("not registered: " + " and ".join(w for w, on in (("a link up", port.link_up),
                                                                                ("PoE power drawn", powered)) if on)
                               + ", and the gateway has leased no registered machine an address on this port")
                              if unregistered else "",
        # the State column sorts as its first machine (needs attention first), then an unregistered device, then none
        "state_sort": machines[0]["severity"] if machines else (5 if unregistered else 9),
        "lease_sort": machines[0]["lease_sort"] if machines else 9_999_999_999,
        "net_ok": net_ok,
        "net": PASSED[0] if net_ok else FAILED[0],
        "net_cls": PASSED[1] if net_ok else FAILED[1],
        "net_title": net_title,
        "link": "↑" if port.link_up else "↓",
        "link_up": bool(port.link_up),
        "link_title": link_title,
        # the MAC addresses the switch saw on the port: in the Link mark's title (the review of #137: the machine is
        # named by the hardware columns, so the MACs earn no column of their own)
        "link_cell_title": link_title + (f"; MACs seen: {', '.join(macs)}" if macs else "; no MAC seen"),
        "macs": macs,
        "speed": speed,
        "speed_sort": port.speed_mbps if port.link_up and port.speed_mbps else 0,
        "vlan": vlan,
        "vlan_sort": getattr(port, "pvid", None) or 0,
        "vlan_title": vlan_title,
        "gw": gw,
        "gw_title": gw_title,
        "poe": poe,
        "poe_cls": poe_cls,
        "poe_title": poe_title,
        "poe_sort": poe_sort,
        "poe_state": port.poe_state or "",
        "watts": watts(port.poe_state, port.poe_watts),
        "can_power": can_power,
        # what the button does; "" where there is no button (not a board port, or a state it cannot act on)
        "poe_action": action,
        # the power symbol on every PoE port; its words say what a press does, or why this port has no button
        "power_icon": power_icon,
        "power_title": power_title,
        # what the button's confirm() names as powered: the newest machine on the port, else the LLDP neighbour
        "powers": machines[0]["label"] if machines else neighbour_name,
        "lldp_name": neighbour_name,
        # what LLDP says of the neighbour, on hover: whose name it is and its chassis id (#148); its port has a column
        "lldp_title": "; ".join(t for t in (
            "the gateway" if neighbour_name == GATEWAY else HOUSE_UPLINK if neighbour_name == HOUSE_UPLINK
            else "the name it announces" if name else "",
            f"chassis {label(getattr(port, 'lldp_chassis', None) or '', house)}"
            if name and getattr(port, "lldp_chassis", None) else "") if t),
        "lldp_url": _fleet_url(request, serial) if serial else "",
        "lldp_port": lldp_port(port, house),
        "rx": rate(port.rx_bps),
        "tx": rate(port.tx_bps),
        "rx_errors": _count(port.rx_errors),
        "tx_errors": _count(port.tx_errors),
    }
    return row


# --- VLANs (Tim, 2026-10-09): the port's VLAN, and the gateway's interface that carries it ---

SYS_CLASS_NET = Path("/sys/class/net")  # the gateway's network devices (a constant so tests can point it elsewhere)
VLAN_NETDEV = re.compile(r"^v(\d+)$")  # the gateway names its VLAN interfaces v<VLAN id>
TAGGED_SHOWN = 12  # tagged VLANs listed in a title before "… (+N)"
_netdev_failed = False  # whether the failure to read SYS_CLASS_NET has been logged (once per run of failures)


def gateway_vlan_netdevs():
    """{VLAN id: operstate} of the gateway's VLAN interfaces, read from SYS_CLASS_NET (the site runs on the gateway,
    and /proc/net/vlan is root-only). A device is a VLAN interface when it is named v<id> and its uevent says
    DEVTYPE=vlan. None when the directory cannot be read (logged once until it can be)."""
    global _netdev_failed
    try:
        found = {}
        for entry in SYS_CLASS_NET.iterdir():
            match = VLAN_NETDEV.match(entry.name)
            if not match:
                continue
            try:
                lines = (entry / "uevent").read_text().split()
                state = (entry / "operstate").read_text().strip()
            except OSError:  # a device that went away between the listing and the read
                continue
            if "DEVTYPE=vlan" in lines:
                found[int(match.group(1))] = state or "unknown"
    except OSError:
        if not _netdev_failed:
            logger.exception("switch dashboard: the gateway's network devices could not be read")
            _netdev_failed = True
        return None
    _netdev_failed = False
    return found


def _vlan_names(views):
    """{VLAN id: name} for the names the page may show (SHOWN_NAME: a board's or a switch's name); every other
    VLAN name is left out, as every other host name is masked."""
    names = {}
    for view in views:
        for vlan in getattr(view, "vlans", None) or []:
            name = vlan.get("name") or ""
            if SHOWN_NAME.match(name):
                names[vlan["vlan_id"]] = name
    return names


def _vlan_list(ids, names):
    return ", ".join(f"{v} ({names[v]})" if v in names else str(v) for v in ids)


def vlan_cell(port, names=None):
    """(text, title) of a port's VLAN cell: its PVID alone ("2110"; the review of #137: one form a column); the other
    untagged and the tagged VLANs in the title (tagged cut at TAGGED_SHOWN). NONE when the reader gave no VLAN data."""
    names = names or {}
    pvid = getattr(port, "pvid", None)
    untagged = list(getattr(port, "untagged_vlans", None) or [])
    tagged = list(getattr(port, "tagged_vlans", None) or [])
    if pvid is None and not untagged and not tagged:
        return NONE, "no VLAN data for this port"
    shown = _vlan_list(tagged[:TAGGED_SHOWN], names) + (f", … (+{len(tagged) - TAGGED_SHOWN})" if len(tagged) > TAGGED_SHOWN else "")
    title = "; ".join([
        f"PVID {_vlan_list([pvid], names)}" if pvid is not None else "no PVID",
        f"untagged {_vlan_list(untagged, names)}" if untagged else "untagged none",
        f"tagged {shown}" if tagged else "tagged none",
    ])
    return (str(pvid) if pvid is not None else NONE), title


def gw_cell(pvid, netdevs):
    """(text, title) of a board port's GW cell: the gateway's interface for its PVID with its state ("v2110 ↑"),
    "none" when the gateway has no such interface. NONE where the port has no PVID or the devices could not be
    read."""
    if pvid is None or netdevs is None:
        return NONE, "not known"
    if pvid not in netdevs:
        return "none", f"the gateway has no interface for VLAN {pvid}"
    state = netdevs[pvid]
    return f"v{pvid} {'↑' if state == 'up' else '↓'}", f"gateway interface v{pvid}, {state}"


def _status(view):
    if view.reachable:
        return "answering" + (f" ({view.error})" if view.error else "")
    return view.error or "not answering"


def _switches(request):
    """The page's data, one dict per switch with display-ready rows: the template and the JSON both use it. Times
    are in the installation's own zone (override, not activate: the worker's next request keeps the site's)."""
    with timezone.override(localtime.zone()):
        return _switch_dicts(request)


def title(view):
    """"Switch 1: sw-netgear-gsm7252ps-s2 (GSM7252PS)". The number is the installation's switch index, from the
    switches configuration the PoE views use: the number in every Pi's name (pi-sw1-p10) and port. The sysName alone
    can mislead (welland's switch 1 calls itself "...-s2")."""
    number = f"Switch {view.index}" if view.index is not None else "Switch"
    return f"{number}: {switch_name(view.name)} ({(view.model or '').upper()})"


def _ago(iso):
    """"12 s ago" for the reader's read time (the exact time is read_at, the title); a time the page cannot show is
    said instead (fail loud, in the cell)."""
    if not iso:
        return ""
    try:
        moment = datetime.fromisoformat(iso)
        if timezone.is_naive(moment):
            raise ValueError(f"the read time {iso!r} has no offset")
        return ago(moment)
    except ValueError as exc:
        return str(exc)


def _model(view):
    """The switch's model from the switch registry (netgear_switch), or (None, why not)."""
    from netgear_switch import UnknownModelError, get_model

    try:
        return get_model(view.model), ""
    except UnknownModelError as exc:
        return None, f"No front panel: {exc}."


def _failures(view, spec):
    """({port: [text]} of the inventory check's failures with no known cause, {port: [known cause]}, [the switch's
    known issues, one line each]) of a reachable switch the inventory describes; nothing for any other."""
    failures, known, issues = {}, {}, {}
    if not view.reachable or spec is None or spec is NO_INVENTORY:
        return failures, known, []
    for failure in inventory_check.check(spec, view):
        cause = inventory_check.KNOWN_CAUSES.get(failure.vlan)
        if cause is None:
            failures.setdefault(failure.port, []).append(failure.text)
            continue
        short = cause[0]
        known.setdefault(failure.port, []).append(f"also untagged in VLAN {failure.vlan} ({short})")
        issue = issues.setdefault(short, {"ports": set(), "vlans": set()})
        issue["ports"].add(failure.port)
        issue["vlans"].add(failure.vlan)
    # a switch-wide known issue is one line above the drawing (#130), not a mark on every port: how many board ports,
    # which VLANs, the issue linked
    lines = [{"text": f"{len(i['ports'])} board ports also untagged in VLAN {', '.join(map(str, sorted(i['vlans'])))}",
              "issue": short, "url": inventory_check.POE_ISSUES + short.split("#")[1]}
             for short, i in sorted(issues.items())]
    return failures, known, lines


def _switch_dicts(request):
    views = sorted(_read(), key=lambda v: (v.index is None, v.index or 0))
    hosts = _fleet_hosts() if any(p.lldp_name for v in views for p in v.ports) else {}
    inventory = _inventory()
    specs = {id(v): _spec(inventory, v.index) for v in views}
    vlan_names = _vlan_names(views)
    netdevs = gateway_vlan_netdevs()  # once per request, not per port
    powered = {id(v): _can_power(request, v, specs[id(v)]) for v in views}
    machines = on_ports(request)  # once per request: the registry and the gateway's lease records
    # the house uplink's device, masked wherever any switch's label names it (not only its own switch's)
    house = tuple(dict.fromkeys(n for v in views for n in house_names(specs[id(v)], v.ports)))
    out = []
    for position, v in enumerate(views):
        model, problem = _model(v)
        poe_ports = model.poe_port_count if model is not None else 0
        failures, known, issues = _failures(v, specs[id(v)])
        since, before = error_baselines(v)
        rows = [_row(request, p, hosts, specs[id(v)], p.port in powered[id(v)], vlan_names, netdevs, house,
                     machines.get((v.index, p.port), ()) if v.index is not None else (), p.port <= poe_ports,
                     failures.get(p.port, ()), known.get(p.port, ()), since, before.get(p.port))
                for p in v.ports]
        for r in rows:
            # an empty board port: nothing registered, no link, no power, and as it should be; a phone leaves it out
            # and the switch's heading line counts them (the review of #137)
            r["empty"] = not r["machines"] and not r["role"] and not r["unregistered"] and r["net_ok"] \
                and not r["link_up"] and r["poe_state"] != "delivering"
        out.append({
            "index": v.index,
            "title": title(v),
            "name": switch_name(v.name),
            "model": v.model,
            "reachable": bool(v.reachable),
            "status": _status(v) + (f" ({NO_INVENTORY_NOTE})" if specs[id(v)] is NO_INVENTORY else ""),
            "read_at": when(v.read_at),
            "read_ago": _ago(v.read_at),
            # when the ports below were last read well, as an ISO 8601 time with its offset: switch-events.js keeps a
            # pushed link change until a reading at least this new has caught up with it (a failed read keeps the
            # old ports, so good_at, not read_at)
            "good_at_iso": getattr(v, "good_at", None) or v.read_at or "",
            "known_issues": issues,
            "empty_ports": sum(r["empty"] for r in rows),
            "ports": rows,
            **_panel(v, model, problem, rows, position),
        })
    return out


def _panel(view, model, problem, rows, position):
    """{"panel": [port dicts], "panel_width", "panel_height", "narrow_width", "narrow_height", "panel_label",
    "panel_problem"} of a switch that answers
    (#90, #130): its front panel, laid out by its model in the switch registry, each port drawn from its table row. A
    model the registry does not know, or a reading that does not fit the model, draws no panel and says why in
    `panel_problem`."""
    out = {"panel": [], "panel_width": 0, "panel_height": 0, "narrow_width": 0, "narrow_height": 0, "panel_label": "",
           "panel_problem": ""}
    if not view.reachable or not view.ports:
        return out
    if model is None:
        return {**out, "panel_problem": problem}
    # the section and row anchors use the switch index, or the list position for the legacy single switch
    anchor = f"switch-{view.index if view.index is not None else position}"
    try:
        ports = panel.panel(rows, anchor, model.port_count, model.poe_port_count)
    except panel.LayoutError as exc:
        return {**out, "panel_problem": f"No front panel: the reading does not fit the {model.display_name}: {exc}."}
    width, height = panel.size(ports)
    narrow_width, narrow_height = panel.size(ports, narrow=True)
    return {**out, "panel": [{**dataclasses.asdict(p), "net": p.net} for p in ports], "panel_width": width,
            "panel_height": height, "narrow_width": narrow_width, "narrow_height": narrow_height,
            "panel_label": f"Front panel of {title(view)}: {panel.summary(ports)}"}


def head_titles():
    """Each column header's title: what the column is, said once (the /fleet/ rule, #129). The State column has the
    /fleet/ definition word for word, and the one mark this page adds."""
    from .fpgas import HEAD_TITLES  # not at import: views.fpgas imports views.index, which imports this module

    return {
        "port": "the switch port; the switch's own label for a board port on hover (its configuration text)",
        "model": "the Pi's model, as its label says, of each machine the gateway last leased an address on this "
                 "port, newest first; its label on hover",
        "board": "the FPGA board's type on that machine; its variant on hover",
        "state": "the State of each machine, as the Raspberry Pis page has it: "
                 + ", ".join(f"{states.MARKS[c]} {w}" for c, w in states.WORDS.items()) + f". {HEAD_TITLES['state']}. "
                 f"On this page also {UNREGISTERED} {UNREGISTERED_WORDS}: a link up or PoE power drawn on a board port "
                 "where the gateway has leased no registered machine an address",
        "serial": "the machine's serial number; links its own page",
        "lease": "when the gateway last leased that machine an address on this port: the observation that puts it here",
        "net": "the port's network reading against what it should be: ✓ as it should be, ✗ not, why on hover "
               "(VLANs as the inventory says, PoE read and not at fault, the link up and as fast as the Pi on it, no "
               "error counter risen since the previous reading)",
        "link": "↑ link up, ↓ link down (in the drawing above: the port's light, green while the link is up); the MAC "
                "addresses the switch saw on the port on hover",
        "speed": "the link's speed",
        "poe": "⚡ PoE delivering power, 🔍 PoE on and searching (nothing drawing power), ⊗ PoE off, ! PoE fault, – no PoE on "
               "this port",
        "watts": "the watts the port delivers",
        "power": "Power: a filled green ⏻ (PoE on, or in a fault) turns its PoE off, a hollow red ⏻ (PoE off) "
                 "turns it on; greyed where the port is not an access port (an uplink, a trunk, infrastructure), "
                 "the reason on hover",
        "vlan": "the port's VLAN (its PVID); its other untagged and its tagged VLANs on hover",
        "gw": "the gateway's interface for the port's VLAN: ↑ up, ↓ down",
        "lldp": "the name the LLDP neighbour announces (its domain dropped); where the page may not show it, its role "
                "instead: gateway, or device for a machine that is not a fleet Pi or a switch",
        "lldp_port": "the port the LLDP neighbour reports",
        "rx": "traffic in, kbit/s",
        "tx": "traffic out, kbit/s",
        "rx_errors": "receive errors the switch has counted on the port, a total since its count began (Net is ✗ when "
                     "it rose since the previous reading)",
        "tx_errors": "transmit errors the switch has counted on the port, a total since its count began (Net is ✗ "
                     "when it rose since the previous reading)",
        "role": "the port's role in the inventory where it is not a board port (gateway trunk, switch trunk, house "
                "uplink, infrastructure); empty on a board port",
    }


@require_GET
@never_cache
def switches(request):
    try:
        data, problem = _switches(request), ""
    except _Unavailable as exc:
        data, problem = [], exc.message
    return render(request, "management/switches.html", {
        "switches": data, "problem": problem, "refresh_seconds": REFRESH_SECONDS, "zone": localtime.zone_name(),
        "head_titles": head_titles(),
        "power_url": POWER_URL,
        "json_url": reverse("management-switches-json", urlconf=getattr(request, "urlconf", None)),
    })


@require_GET
@never_cache
def switches_json(request):
    try:
        return JsonResponse({"switches": _switches(request)})
    except _Unavailable as exc:
        return JsonResponse({"error": exc.message, "switches": []}, status=503)
