"""The switch port dashboard (#67): every switch and every port, read side only.

The reading is done by `snmp_switch.dashboard` (fpgas.online-poe), cached for 15 s in the site's "poe" cache, so
the number of viewers does not change the number of reads. This module only lays the result out: the page
server-side (complete without JavaScript), and the same rows as JSON for the page's auto-refresh. The browser
never talks to a switch, and no community or credential is in anything built here."""

import functools
import logging
import re
import socket
from datetime import datetime

from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .. import localtime

REFRESH_SECONDS = 15
MACS_SHOWN = 3
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


# Tim, 2026-10-09: the PoE column is "8.6W" where power flows, else one character; the word is in the cell's
# title, and the legend (POE_LEGEND) is once under each table.
POE_SYMBOLS = {
    "delivering": ("●", "delivering power"),  # only when the switch gives no watts
    "searching": ("⋯", "searching: PoE on, nothing drawing power"),
    "disabled": ("○", "PoE off"),
    "fault": ("⚠", "PoE fault"),
    "other": ("?", "PoE state not known"),
}
NO_POE = ("", "no PoE on this port")
POE_LEGEND = [(symbol, title) for symbol, title in POE_SYMBOLS.values()]


def poe_cell(state, watts):
    """(text, title) of a port's PoE cell: "8.6W" on a delivering port, else one symbol from POE_SYMBOLS."""
    if state == "delivering" and watts is not None:
        return f"{watts:.1f}W", "delivering power"
    if not state:
        return NO_POE
    return POE_SYMBOLS.get(state, POE_SYMBOLS["other"])


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


def label(text):
    """A port label (ifAlias, "<role>.<host>") for the public page: the role, and the host only when it may be shown
    ("eth-uplink.gateway", "1/0/50.sw-netgear-gsm7252ps-s1"); otherwise the role alone ("eth-uplink"). A label with no
    host part is shown unless it is one of the gateway's names."""
    if not text:
        return ""
    role, dot, host = text.partition(".")
    if _host(role) == GATEWAY:  # a host-first label ("<gateway>.eth0")
        role = GATEWAY
    if not dot:
        return role
    shown = _host(host)
    return f"{role}.{shown}" if shown else role


def switch_name(name):
    """A switch's own sysName under the same rule as a neighbour's: shown when it is a switch's name (its domain
    dropped), "gateway" for the gateway's, else "switch"."""
    if not name:
        return ""
    return _host(name) or "switch"


INFRASTRUCTURE = "infrastructure"


def _inventory():
    """{switch index: SwitchSpec} from the switches configuration the PoE views use (switches.yml); {} where there is
    none (the legacy switch) or the installed poe cannot read it."""
    try:
        from snmp_switch.switches import configured_specs

        return {spec.index: spec for spec in configured_specs()}
    except Exception:  # no inventory: every port's label is masked as a board port's
        logger.exception("switch dashboard: the switches configuration could not be read")
        return {}


def port_role(spec, port):
    """The inventory's role of a port that is not a board port ("gateway trunk", "switch trunk", "house uplink",
    "infrastructure"), or None for a board port (1..access_ports) or a switch with no inventory. The coordinator,
    2026-10-09: an infrastructure port is labelled by its role, whatever the switch's own label says (it can name a
    private device)."""
    if spec is None or port <= spec.access_ports:
        return None
    if port == spec.gateway_trunk_port:
        return "gateway trunk"
    if port in tuple(spec.downstream_trunk_ports or ()):
        return "switch trunk"
    if port == spec.house_uplink_port:
        return "house uplink"
    return INFRASTRUCTURE


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

    ports = [p.port for p in view.ports if port_role(spec, p.port) is None]
    return board_ports(request, view.index, ports) if ports else set()


def _row(request, port, hosts, spec=None, can_power=False):
    name = port.lldp_name or ""
    serial = hosts.get(name.split(".")[0]) if name else None
    macs = list(port.macs or [])
    shown = ", ".join(macs[:MACS_SHOWN]) + (f" +{len(macs) - MACS_SHOWN}" if len(macs) > MACS_SHOWN else "")
    poe, poe_title = poe_cell(port.poe_state, port.poe_watts)
    speed = f" {link_speed(port.speed_mbps)}" if port.link_up and port.speed_mbps else ""
    return {
        "port": port.port,
        "label": port_role(spec, port.port) or label(port.label),
        "link": ("up" if port.link_up else "down") + speed,
        "link_up": bool(port.link_up),
        "poe": poe,
        "poe_title": poe_title,
        "can_power": can_power,
        # what the button does; "" where there is no button (not a board port, or a state it cannot act on)
        "poe_action": POWER_ACTION.get(port.poe_state or "", "") if can_power else "",
        "lldp_name": neighbour(name),
        "lldp_url": _fleet_url(request, serial) if serial else "",
        "lldp_port": "" if MAC_ID.match(port.lldp_port or "") else (port.lldp_port or ""),
        "macs": shown,
        "rx": rate(port.rx_bps),
        "tx": rate(port.tx_bps),
        "errors": f"{_count(port.rx_errors)} / {_count(port.tx_errors)}",
    }


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


def _switch_dicts(request):
    views = sorted(_read(), key=lambda v: (v.index is None, v.index or 0))
    hosts = _fleet_hosts() if any(p.lldp_name for v in views for p in v.ports) else {}
    inventory = _inventory()
    powered = {id(v): _can_power(request, v, inventory.get(v.index)) for v in views}
    return [{
        "index": v.index,
        "title": title(v),
        "name": switch_name(v.name),
        "model": v.model,
        "reachable": bool(v.reachable),
        "status": _status(v),
        "read_at": when(v.read_at),
        "ports": [_row(request, p, hosts, inventory.get(v.index), p.port in powered[id(v)]) for p in v.ports],
    } for v in views]


@require_GET
@never_cache
def switches(request):
    try:
        data, problem = _switches(request), ""
    except _Unavailable as exc:
        data, problem = [], exc.message
    return render(request, "management/switches.html", {
        "switches": data, "problem": problem, "refresh_seconds": REFRESH_SECONDS, "zone": localtime.zone_name(), "poe_legend": POE_LEGEND,
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
