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

"Newest" is the highest ts, ties by id, of any boot. A record that is not this document is a ValueError saying what
is wrong with it: the documents come from the Pis, and nothing here guesses."""

from dataclasses import dataclass, field

from django.db.models import OuterRef, Subquery
from django.utils.dateparse import parse_datetime
from fleet.models import BootEvent, Machine

STAGE = "network-state"
LEASE_KEYS = ("address", "server", "lifetime_s", "acked_at", "expires")


@dataclass
class Network:
    """The newest network-state record of one machine, checked."""
    event: BootEvent
    doc: dict
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


def _moment(value, what):
    """An ISO 8601 string with its offset as a datetime, None for null; anything else is a ValueError."""
    if value is None:
        return None
    parsed = parse_datetime(value) if isinstance(value, str) else None
    if parsed is None or parsed.tzinfo is None:
        raise ValueError(f"{what} is {value!r}, not an ISO 8601 time with its offset")
    return parsed


def _rows(doc, key):
    rows = doc.get(key)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{key} is {rows!r}, not a list of objects")
    return rows


def parse(event):
    """The Network a network-state BootEvent says; ValueError (naming the event) for a document that is not one."""
    doc = event.detail
    try:
        if not isinstance(doc, dict):
            raise ValueError(f"the record is {doc!r}, not an object")
        for key in ("operational_state", "administrative_state"):
            if not isinstance(doc.get(key), str) or not doc[key]:
                raise ValueError(f"{key} is {doc.get(key)!r}, not a state")
        lease = doc.get("lease")
        if not isinstance(lease, dict) or set(lease) != set(LEASE_KEYS):
            raise ValueError(f"lease is {lease!r}, not an object with {', '.join(LEASE_KEYS)}")
        return Network(event, doc, _rows(doc, "addresses"), _rows(doc, "default_routes"), lease,
                       _moment(lease["acked_at"], "lease.acked_at"), _moment(lease["expires"], "lease.expires"))
    except ValueError as exc:
        raise ValueError(f"network-state event {event.pk} of machine {event.machine_id}: {exc}") from exc


def newest(serial=None):
    """{serial: Network} of the newest network-state event of each machine that has sent one (or of `serial`
    alone): two queries however many machines there are."""
    machines = Machine.objects.all()
    if serial is not None:
        machines = machines.filter(serial=serial)
    latest = BootEvent.objects.filter(machine=OuterRef("pk"), stage=STAGE).order_by("-ts", "-id").values("pk")[:1]
    ids = [i for i in machines.annotate(latest_network=Subquery(latest)).values_list("latest_network", flat=True)
           if i is not None]
    return {e.machine.serial: parse(e) for e in BootEvent.objects.filter(pk__in=ids).select_related("machine")}


def summary(net):
    """The registry.json "network" of one machine: null when it has sent no record."""
    if net is None:
        return None
    return {"state": net.operational_state, "administrative_state": net.administrative_state,
            "changed": net.event.ts.isoformat(), "last_ack": net.last_ack.isoformat() if net.last_ack else None,
            "last_ack_source": "pi"}
