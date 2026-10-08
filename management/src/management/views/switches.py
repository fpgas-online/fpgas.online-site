"""The switch port dashboard (#67): every switch and every port, read side only.

The reading is done by `snmp_switch.dashboard` (fpgas.online-poe), cached for 15 s in the site's "poe" cache, so
the number of viewers does not change the number of reads. This module only lays the result out: the page
server-side (complete without JavaScript), and the same rows as JSON for the page's auto-refresh. The browser
never talks to a switch, and no community or credential is in anything built here."""

import logging
from datetime import datetime

from django.core.cache import caches
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


def _row(request, port, hosts):
    name = port.lldp_name or ""
    serial = hosts.get(name.split(".")[0]) if name else None
    macs = list(port.macs or [])
    shown = ", ".join(macs[:MACS_SHOWN]) + (f" +{len(macs) - MACS_SHOWN}" if len(macs) > MACS_SHOWN else "")
    watts = "" if port.poe_watts is None else f" {port.poe_watts:.1f} W"
    speed = f" {port.speed_mbps} Mbit/s" if port.link_up and port.speed_mbps else ""
    return {
        "port": port.port,
        "label": port.label or "",
        "link": ("up" if port.link_up else "down") + speed,
        "link_up": bool(port.link_up),
        "poe": (port.poe_state + watts) if port.poe_state else NONE,
        "lldp_name": name,
        "lldp_url": _fleet_url(request, serial) if serial else "",
        "lldp_port": port.lldp_port or "",
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


def _switch_dicts(request):
    views = _read()
    hosts = _fleet_hosts() if any(p.lldp_name for v in views for p in v.ports) else {}
    return [{
        "name": v.name,
        "model": v.model,
        "reachable": bool(v.reachable),
        "status": _status(v),
        "read_at": when(v.read_at),
        "ports": [_row(request, p, hosts) for p in v.ports],
    } for v in views]


@require_GET
@never_cache
def switches(request):
    try:
        data, problem = _switches(request), ""
    except _Unavailable as exc:
        data, problem = [], exc.message
    return render(request, "management/switches.html", {
        "switches": data, "problem": problem, "refresh_seconds": REFRESH_SECONDS, "zone": localtime.zone_name(),
        "json_url": reverse("management-switches-json", urlconf=getattr(request, "urlconf", None)),
    })


@require_GET
@never_cache
def switches_json(request):
    try:
        return JsonResponse({"switches": _switches(request)})
    except _Unavailable as exc:
        return JsonResponse({"error": exc.message, "switches": []}, status=503)
