"""The switch port dashboard (#67): every switch and every port, read side only.

The reading is done by `snmp_switch.dashboard` (fpgas.online-poe), cached for 15 s in the site's "poe" cache, so
the number of viewers does not change the number of reads. This module only lays the result out: the page
server-side (complete without JavaScript), and the same rows as JSON for the page's auto-refresh. The browser
never talks to a switch, and no community or credential is in anything built here.

A port's link change reaches an open page sooner than that cache can: the gateway's switch poller publishes it, the
fleet consumer forwards it on ws/switches/ (pistat), and switch-events.js draws it on the port's row and front-panel
mark straight from the record. The 15 s refresh stays as the fallback, and corrects anything drawn from a record."""

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
from fleet.timeago import ago

from .. import inventory_check, localtime, panel

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
    """A rate in bits per second as kbit/s or Mbit/s; NONE when not known."""
    if bps is None:
        return NONE
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f} Mbit/s"
    return f"{bps / 1000:.0f} kbit/s"


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


# The PoE column: the watts where power flows, else the state as a word (#89; the coordinator, 11 Oct 2026: one
# form per column, and a glyph that needs a key is a word). The longer meaning is the cell's title.
POE_WORDS = {
    "delivering": ("on", "delivering power; the switch gives no watts"),  # only when the switch gives no watts
    "searching": ("searching", "PoE on, nothing drawing power"),
    "disabled": ("off", "PoE off"),
    "fault": ("fault", "PoE fault"),
    "other": ("not known", "the switch reports a PoE state this page does not know"),
}
NO_POE = ("no PoE", "this port has no PoE")


def poe_cell(state, watts):
    """(text, title) of a port's PoE cell: "8.6W" on a delivering port, else its state's word (POE_WORDS)."""
    if state == "delivering" and watts is not None:
        return f"{watts:.1f}W", "delivering power"
    if not state:
        return NO_POE
    return POE_WORDS.get(state, POE_WORDS["other"])


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


def _fleet_hosts():
    """{short hostname: serial} of the newest machine on each hostname: ONE query for every port."""
    from fleet.services import machine_hosts

    return machine_hosts()


GATEWAY = "gateway"
DEVICE = "device"
# Names the public page may show (the coordinator, 2026-10-09): a fleet Pi's hostname, and a switch's own sysName
# (welland's switches are sw-<make>-<model>-<n>). Every other host name is masked by its role word: the page never
# names the gateway, the upstream host or any other machine.
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


def _host(name):
    """A host name as the page may show it: kept when SHOWN_NAME allows it (its domain dropped), "gateway" for the
    gateway's own names, else None."""
    short = name.split(".")[0]
    if name.lower() in _gateway_names() or short.lower() in _gateway_names():
        return GATEWAY
    return short if SHOWN_NAME.match(short) else None


def neighbour(name):
    """An LLDP neighbour's system name for the public page: a fleet Pi or a switch by name, the gateway as "gateway",
    any other machine as "device"."""
    if not name:
        return ""
    return _host(name) or DEVICE


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
    """A switch's own sysName under the same rule as a neighbour's: shown when it is a switch's name (its domain
    dropped), "gateway" for the gateway's, else "switch"."""
    if not name:
        return ""
    return _host(name) or "switch"


INFRASTRUCTURE = "infrastructure"


# A switch the inventory should describe but does not (the configuration could not be read, or has no entry for
# it): its ports are shown with no label and no PoE button, rather than guess which are boards (fail closed).
NO_INVENTORY = object()
NO_INVENTORY_NOTE = "the switch inventory could not be read: port labels and PoE buttons are hidden"


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


# The PoE button's action by the port's state: off while it delivers or searches, on while it is disabled. A port in
# any other state (fault, not known, no PoE) gets no button.
POWER_ACTION = {"delivering": "off", "searching": "off", "disabled": "on"}
# Both urlconfs include snmp_switch.urls under snmp/; its paths have no names to reverse, as the board pages' own
# "/snmp/toggle" has none.
POWER_URL = "/snmp/power"


def _can_power(request, view, spec):
    """{port numbers of `view` that get a PoE button}: the board ports, as the site's PoE policy (the one /snmp/power
    enforces, pibfpgas.poe.board_port) says. ONE pass over the registry per switch, not one per port. A port the
    inventory names as an uplink, trunk or other infrastructure is not asked at all."""
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


def _row(request, port, hosts, spec=None, can_power=False, vlan_names=None, netdevs=None, switches=None,
         house=()):
    name = port.lldp_name or ""
    serial = hosts.get(name.split(".")[0]) if name else None
    macs = list(port.macs or [])
    # one MAC and how many more: the column is as wide as one value (#89); every MAC is in the title
    shown = (macs[0] + (f" +{len(macs) - 1}" if len(macs) > 1 else "")) if macs else ""
    role = port_role(spec, port.port) if spec is not NO_INVENTORY else None
    neighbour_name = neighbour(name)
    # a neighbour that is one of this installation's switches by its number, as the page titles it; the sysName in
    # the title
    short = (switches or {}).get(name.split(".")[0].lower()) if name else None
    poe, poe_title = poe_cell(port.poe_state, port.poe_watts)
    speed = f" {link_speed(port.speed_mbps)}" if port.link_up and port.speed_mbps else ""
    vlan, vlan_title = vlan_cell(port, vlan_names)
    # the gateway's interface for the port's VLAN belongs to a board port; an infrastructure port carries many
    # VLANs (all in the VLAN cell's title), and where the inventory is unknown a trunk cannot be told from a board
    board = spec is not NO_INVENTORY and port_role(spec, port.port) is None
    gw, gw_title = gw_cell(getattr(port, "pvid", None), netdevs) if board else (NONE, "not a board port")
    row = {
        "port": port.port,
        # fail closed where the inventory cannot say which port is the house uplink: no label at all
        "label": (NONE if spec is NO_INVENTORY else
                  HOUSE_UPLINK if role == HOUSE_UPLINK else label(port.label, house) or NONE),
        "label_title": ("labels hidden: the switch inventory could not be read" if spec is NO_INVENTORY else
                        role or ("" if port.label else "the switch has no label for this port")),
        # the label's dot-separated parts: the page may break a long label after a dot, and only there
        "label_parts": [],
        "link": ("up" if port.link_up else "down") + speed,
        "link_up": bool(port.link_up),
        "vlan": vlan,
        "vlan_title": vlan_title,
        "gw": gw,
        "gw_title": gw_title,
        "poe": poe,
        "poe_title": poe_title,
        "can_power": can_power,
        # what the button does; "" where there is no button (not a board port, or a state it cannot act on)
        "poe_action": POWER_ACTION.get(port.poe_state or "", "") if can_power else "",
        "lldp_name": short or neighbour_name,
        "lldp_title": neighbour_name if short else "",
        "lldp_url": _fleet_url(request, serial) if serial else "",
        "lldp_port": lldp_port(port, house),
        "macs": shown,
        "macs_title": ", ".join(macs) if len(macs) > 1 else "",
        "rx": rate(port.rx_bps),
        "tx": rate(port.tx_bps),
        "errors": f"{_count(port.rx_errors)} / {_count(port.tx_errors)}",
    }
    parts = row["label"].split(".")
    row["label_parts"] = [f"{part}." for part in parts[:-1]] + parts[-1:] if row["label"] else []
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
    """(text, title) of a port's VLAN cell: its PVID ("2110"), "+21" for other untagged VLANs, "+88t" for 88 tagged
    ones. NONE when the reader gave no VLAN data. The title lists them all (tagged cut at TAGGED_SHOWN)."""
    names = names or {}
    pvid = getattr(port, "pvid", None)
    untagged = list(getattr(port, "untagged_vlans", None) or [])
    tagged = list(getattr(port, "tagged_vlans", None) or [])
    if pvid is None and not untagged and not tagged:
        return NONE, "no VLAN data for this port"
    extra = [v for v in untagged if v != pvid]
    parts = [str(pvid)] if pvid is not None else []
    if extra:
        parts.append("+" + ",".join(map(str, extra)))
    if tagged:
        parts.append(f"+{len(tagged)}t")
    shown = _vlan_list(tagged[:TAGGED_SHOWN], names) + (f", … (+{len(tagged) - TAGGED_SHOWN})" if len(tagged) > TAGGED_SHOWN else "")
    title = "; ".join([
        f"PVID {_vlan_list([pvid], names)}" if pvid is not None else "no PVID",
        f"untagged {_vlan_list(untagged, names)}" if untagged else "untagged none",
        f"tagged {shown}" if tagged else "tagged none",
    ])
    return " ".join(parts), title


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


def _switch_dicts(request):
    views = sorted(_read(), key=lambda v: (v.index is None, v.index or 0))
    hosts = _fleet_hosts() if any(p.lldp_name for v in views for p in v.ports) else {}
    inventory = _inventory()
    specs = {id(v): _spec(inventory, v.index) for v in views}
    vlan_names = _vlan_names(views)
    netdevs = gateway_vlan_netdevs()  # once per request, not per port
    powered = {id(v): _can_power(request, v, specs[id(v)]) for v in views}
    # the house uplink's device, masked wherever any switch's label names it (not only its own switch's)
    house = tuple(dict.fromkeys(n for v in views for n in house_names(specs[id(v)], v.ports)))
    # {a switch's own name, lower case: "switch N"}: a neighbour that is one of these is shown by its number
    switches = {(v.name or "").split(".")[0].lower(): f"switch {v.index}" for v in views if v.name and v.index is not None}
    out = []
    for position, v in enumerate(views):
        rows = [_row(request, p, hosts, specs[id(v)], p.port in powered[id(v)], vlan_names, netdevs, switches, house)
                for p in v.ports]
        out.append({
            "index": v.index,
            "title": title(v),
            "name": switch_name(v.name),
            "model": v.model,
            "reachable": bool(v.reachable),
            "status": _status(v) + (f" ({NO_INVENTORY_NOTE})" if specs[id(v)] is NO_INVENTORY else ""),
            "read_at": when(v.read_at),
            "read_ago": _ago(v.read_at),
            "ports": rows,
            **_panel(v, specs[id(v)], rows, position),
        })
    return out


def _panel(view, spec, rows, position):
    """{"panel": [port dicts], "panel_width", "panel_height", "panel_label", "panel_problem"} of a switch that answers
    (#90): its front panel, laid out by its model in the switch registry. Each port marked where the inventory check
    (management.inventory_check) says it is not as the inventory says; with no inventory for the switch, nothing is
    marked (the page says the inventory could not be read). A model the registry does not know, or a reading that does
    not fit the model, draws no panel and says why in `panel_problem`."""
    out = {"panel": [], "panel_width": 0, "panel_height": 0, "panel_label": "", "panel_problem": ""}
    if not view.reachable or not view.ports:
        return out
    from netgear_switch import UnknownModelError, get_model

    try:
        model = get_model(view.model)
    except UnknownModelError as exc:
        return {**out, "panel_problem": f"No front panel: {exc}."}
    failures, causes = {}, {}
    if spec is not None and spec is not NO_INVENTORY:
        for failure in inventory_check.check(spec, view):
            failures.setdefault(failure.port, []).append(failure.text)
            cause = inventory_check.KNOWN_CAUSES.get(failure.vlan)
            causes.setdefault(failure.port, []).append(cause[0] if cause else None)
    # a port whose every failure has a known cause (poe #16, #18): marked apart, so a new fault stands out
    known = {port: ", ".join(dict.fromkeys(c)) for port, c in causes.items() if all(c)}
    # the section and row anchors use the switch index, or the list position for the legacy single switch
    anchor = f"switch-{view.index if view.index is not None else position}"
    try:
        ports = panel.panel(view, {r["port"]: r for r in rows}, failures, anchor, model.port_count,
                            model.poe_port_count, known)
    except panel.LayoutError as exc:
        return {**out, "panel_problem": f"No front panel: the reading does not fit the {model.display_name}: {exc}."}
    width, height = panel.size(ports)
    return {**out, "panel": [dataclasses.asdict(p) for p in ports], "panel_width": width, "panel_height": height,
            "panel_label": f"Front panel of {title(view)}: {panel.summary(ports)}"}


@require_GET
@never_cache
def switches(request):
    try:
        data, problem = _switches(request), ""
    except _Unavailable as exc:
        data, problem = [], exc.message
    return render(request, "management/switches.html", {
        "switches": data, "problem": problem, "refresh_seconds": REFRESH_SECONDS, "zone": localtime.zone_name(),
        "panel_states": panel.STATES,
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
