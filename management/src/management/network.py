"""A Pi's uplink as it last reported it: the newest "network-state" BootEvent of each machine.

The Pi publishes one at every systemd-networkd state change of its uplink (fpgas/<site>/pi/<serial>/event, stage
"network-state"); fleet.services.boot_event stores it as it came. Its detail is this document (schema 1), the same keys
every time, null where there is no value:

  {"schema": 1, "serial", "mac", "hostname", "switch" | null, "port" | null, "at": UTC ISO 8601, "interface",
   "trigger", "operational_state", "administrative_state",
   "addresses": [{"family": 4 | 6, "address", "prefixlen", "scope", "valid_lifetime_s" | null,
                  "preferred_lifetime_s" | null}],            (a null lifetime is forever)
   "default_routes": [{"family": 4 | 6, "gateway", "metric", "protocol"}],
   "lease": {"address" | null, "server" | null, "lifetime_s" | null, "acked_at" | null, "expires" | null}}

"Newest" follows the boot, not the Pi's clock (a Pi's clock starts from an older restored time until NTP syncs, so
BootEvent.ts can put an old boot ahead of the current one, or a far-future ts ahead of everything forever): the
record of the machine's current boot (boot_id == Machine.last_boot_id) that arrived last (highest id; the Pi sends
spooled records oldest first, so arrival order is the Pi's order). Only when the current boot has sent none, the
one that arrived last of any boot, said so by current_boot false.

A record that is not this document is a ValueError saying what is wrong with it (every key required, only a JSON
null is "no value"): the documents come from the Pis, and nothing here guesses. An error is logged at warning level
at every read that meets it; that repeats, which is acceptable because the record stays bad until a new one arrives
and the log is the only place the Pi's bad document is named besides the page that shows it."""

import datetime
import logging
import re
from dataclasses import dataclass, field

from django.db.models import OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils.dateparse import parse_datetime
from fleet.models import BootEvent, Machine

log = logging.getLogger(__name__)
STAGE = "network-state"
SHOWN = 80  # characters of a Pi-supplied value in an error message or a log line
MAX_ROWS = 64  # addresses, and default routes
MAX_TEXT = 256  # characters of any string of the document
STATE = re.compile(r"^[a-z][a-z-]{0,31}$")
KEYS = ("schema", "serial", "mac", "hostname", "switch", "port", "at", "interface", "trigger", "operational_state",
        "administrative_state", "addresses", "default_routes", "lease")
ADDRESS_KEYS = ("family", "address", "prefixlen", "scope", "valid_lifetime_s", "preferred_lifetime_s")
ROUTE_KEYS = ("family", "gateway", "metric", "protocol")
ENTRY_KEYS = ("state", "administrative_state", "changed", "last_ack", "last_ack_source", "boot_id", "current_boot")
LEASE_KEYS = ("address", "server", "lifetime_s", "acked_at", "expires")


@dataclass
class Network:
    """The newest network-state record of one machine, checked."""
    event: BootEvent
    doc: dict
    current_boot: bool  # the record is of the boot the machine is running
    addresses: list = field(default_factory=list)
    default_routes: list = field(default_factory=list)
    lease: dict = field(default_factory=dict)
    last_ack: object = None  # datetime | None
    expires: object = None  # datetime | None

    @property
    def operational_state(self):
        return self.doc["operational_state"]

    @property
    def administrative_state(self):
        return self.doc["administrative_state"]


def _r(value):
    """A Pi-supplied value for a message, never more than SHOWN characters and never a repr of something large: a
    string is cut before it is repr'd, a container is named by its type and size."""
    if isinstance(value, str):
        text = repr(value[:SHOWN + 1])
        return text if len(value) <= SHOWN else text[:SHOWN - 1] + "…"
    if isinstance(value, (list, dict)):
        return f"a {type(value).__name__} of {len(value)}"
    if _is_int(value) and value.bit_length() > 64:
        return f"an integer of {value.bit_length()} bits"
    return repr(value)[:SHOWN]


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value):
    return isinstance(value, str) and value != ""


def _need(row, keys, what):
    if not isinstance(row, dict):
        raise ValueError(f"{what} is {_r(row)}, not an object")
    missing = [k for k in keys if k not in row]
    if missing:
        raise ValueError(f"{what} lacks {', '.join(missing)}")


def _check(ok, value, what, wanted):
    if not ok:
        raise ValueError(f"{what} is {_r(value)}, not {wanted}")


def _moment(value, what):
    """An ISO 8601 string with its offset as a datetime, None for null; anything else is a ValueError."""
    if value is None:
        return None
    parsed = parse_datetime(value) if isinstance(value, str) else None
    if parsed is None or parsed.tzinfo is None:
        raise ValueError(f"{what} is {_r(value)}, not an ISO 8601 time with its offset")
    return parsed


def utc(moment):
    """A moment as ISO 8601 in UTC, None for none."""
    return moment.astimezone(datetime.timezone.utc).isoformat() if moment else None


def _rows(doc, key, keys):
    rows = doc[key]
    _check(isinstance(rows, list), rows, key, "a list")
    for i, row in enumerate(rows):
        _need(row, keys, f"{key}[{i}]")
    return rows


def _bounded(value, what):
    """The document is from a Pi on the open LAN: no string over MAX_TEXT, no object over MAX_ROWS keys."""
    if isinstance(value, str):
        _check(len(value) <= MAX_TEXT, value, what, f"a string of at most {MAX_TEXT} characters")
    elif isinstance(value, list):
        for item in value:
            _bounded(item, what)
    elif isinstance(value, dict):
        _check(len(value) <= MAX_ROWS, value, what, f"an object of at most {MAX_ROWS} keys")
        for key, item in value.items():
            _bounded(key, what)
            _bounded(item, f"{what}.{key}" if len(str(key)) <= SHOWN else what)


def _check_doc(doc):
    _need(doc, KEYS, "the record")
    for key in ("addresses", "default_routes"):
        _check(not isinstance(doc[key], list) or len(doc[key]) <= MAX_ROWS, doc[key], key,
               f"a list of at most {MAX_ROWS}")
    _bounded(doc, "the record")
    _check(_text(doc["serial"]), doc["serial"], "serial", "a non-empty string")
    _check(doc["hostname"] is None or isinstance(doc["hostname"], str), doc["hostname"], "hostname",
           "a string or null")
    for key in ("switch", "port"):
        _check(doc[key] is None or (_is_int(doc[key]) and doc[key] >= 0), doc[key], key,
               "an integer of 0 or more, or null")
    _check(_moment(doc["at"], "at") is not None, doc["at"], "at", "an ISO 8601 time with its offset")
    _check(isinstance(doc["trigger"], str) and STATE.match(doc["trigger"]), doc["trigger"], "trigger", "a state")
    _check(doc["schema"] == 1 and _is_int(doc["schema"]), doc["schema"], "schema", "1")
    for key in ("interface", "mac"):
        _check(_text(doc[key]), doc[key], key, "a non-empty string")
    for key in ("operational_state", "administrative_state"):
        _check(isinstance(doc[key], str) and STATE.match(doc[key]), doc[key], key, "a state")
    addresses = _rows(doc, "addresses", ADDRESS_KEYS)
    for i, a in enumerate(addresses):
        what = f"addresses[{i}]"
        _check(a["family"] in (4, 6) and _is_int(a["family"]), a["family"], f"{what}.family", "4 or 6")
        _check(_text(a["address"]), a["address"], f"{what}.address", "a non-empty string")
        _check(_is_int(a["prefixlen"]) and 0 <= a["prefixlen"] <= (32 if a["family"] == 4 else 128), a["prefixlen"],
               f"{what}.prefixlen", f"0 to {32 if a['family'] == 4 else 128} for IPv{a['family']}")
        _check(a["scope"] is None or isinstance(a["scope"], str), a["scope"], f"{what}.scope", "a string or null")
        for key in ("valid_lifetime_s", "preferred_lifetime_s"):
            _check(a[key] is None or (_is_int(a[key]) and a[key] >= 0), a[key], f"{what}.{key}",
                   "an integer of 0 or more, or null")
    routes = _rows(doc, "default_routes", ROUTE_KEYS)
    for i, r in enumerate(routes):
        what = f"default_routes[{i}]"
        _check(r["family"] in (4, 6) and _is_int(r["family"]), r["family"], f"{what}.family", "4 or 6")
        _check(r["gateway"] is None or isinstance(r["gateway"], str), r["gateway"], f"{what}.gateway",
               "a string or null")
        _check(_is_int(r["metric"]), r["metric"], f"{what}.metric", "an integer")
        _check(isinstance(r["protocol"], str), r["protocol"], f"{what}.protocol", "a string")
    lease = doc["lease"]
    _need(lease, LEASE_KEYS, "lease")
    _check(lease["lifetime_s"] is None or _is_int(lease["lifetime_s"]), lease["lifetime_s"], "lease.lifetime_s",
           "an integer or null")
    for key in ("address", "server"):
        _check(lease[key] is None or isinstance(lease[key], str), lease[key], f"lease.{key}", "a string or null")
    return addresses, routes, lease


def parse(event, current_boot):
    """The Network a network-state BootEvent says; ValueError (naming the event) for a document that is not one."""
    try:
        addresses, routes, lease = _check_doc(event.detail)
        return Network(event, event.detail, current_boot, addresses, routes, lease,
                       _moment(lease["acked_at"], "lease.acked_at"), _moment(lease["expires"], "lease.expires"))
    except ValueError as exc:
        error = ValueError(f"network-state event {event.pk} of machine {event.machine_id}: {exc}")
        log.warning("%s", error)  # at every read that meets it: the record stays bad until a new one arrives
        raise error from exc


def _kept(event, boot_id):
    """(Network, or the ValueError the record is) of one machine's newest event."""
    current = bool(boot_id) and event.boot_id == boot_id
    try:
        return parse(event, current)
    except ValueError as exc:
        return exc


def of(machine):
    """The newest record of one machine (Network, or its ValueError), None if it has sent none: the one machine's
    page reads it in one query for the current boot, one more only when that boot has none."""
    events = BootEvent.objects.filter(machine=machine, stage=STAGE).order_by("-id")
    event = (events.filter(boot_id=machine.last_boot_id).first() if machine.last_boot_id else None) or events.first()
    return None if event is None else _kept(event, machine.last_boot_id)


def newest():
    """{serial: Network, or the ValueError its record is} of each machine that has sent a record: two queries
    however many machines there are."""
    events = BootEvent.objects.filter(machine=OuterRef("pk"), stage=STAGE).order_by("-id").values("pk")
    current = events.filter(boot_id=OuterRef("last_boot_id")).exclude(boot_id="")[:1]
    ids = [i for i in Machine.objects.annotate(
        newest_id=Coalesce(Subquery(current), Subquery(events[:1]))).values_list("newest_id", flat=True) if i is not None]
    return {e.machine.serial: _kept(e, e.machine.last_boot_id)
            for e in BootEvent.objects.filter(pk__in=ids).select_related("machine")}


def summary(net):
    """The registry.json "network" of one machine, in one of three forms (see views.fpgas.registry_json): null when
    it has sent no record; the record's entry; or, for a bad record, every key of the entry null plus "error"."""
    if net is None:
        return None
    if isinstance(net, ValueError):
        return dict.fromkeys(ENTRY_KEYS) | {"error": str(net)}
    return {"state": net.operational_state, "administrative_state": net.administrative_state,
            "changed": utc(net.event.ts), "last_ack": utc(net.last_ack), "last_ack_source": "pi",
            "boot_id": net.event.boot_id, "current_boot": net.current_boot, "error": None}
