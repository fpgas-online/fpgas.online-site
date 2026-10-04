"""Transport-agnostic ingest services.

The MQTT consumer (and any future transport) calls these; they own all DB
semantics. `fingerprint` must stay byte-identical to the Pi agent's
implementation: canonical JSON (sorted keys, compact separators) → SHA-256.
"""

import datetime
import hashlib
import json
import logging

from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import BootEvent, Machine

log = logging.getLogger(__name__)

# How recently a machine's status beat must have come for the /fpgas/ pages
# to offer it: three of the fleet agent's 60 s beats.
CHECKED_IN_WITHIN = datetime.timedelta(minutes=3)


def fingerprint(doc):
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def register_document(doc, port=None):
    """Ingest a registration document. Returns (machine, changed) where
    changed means the machine's latest snapshot moved to a different
    fingerprint (a flap back to a previously seen document reuses its row).
    A registration that is refused returns (None, False) and is logged.

    A machine is a row per (port, serial): `port` is the port the broker's
    per-port listener stamped on the topic (the short hostname, already
    validated), or None for a message with no stamp, which is the row with
    no port. So what a Pi says about a serial can only ever write the rows
    of its own port, never the row another port's Pi registered, and a
    forged claim to a serial is a row of its own with its own history. A
    stamped registration must name its own port's hostname, or it is
    refused; it never adopts the unstamped row of that serial."""
    now = timezone.now()
    serial = doc["machine"]["serial"]
    connection = doc.get("connection", {})
    hostname = connection.get("hostname", "")
    if port is not None and (not isinstance(hostname, str)
                             or hostname.split(".")[0] != port):
        log.warning("registration for %r from port %s dropped: it names"
                    " host %r", serial, port, hostname)
        return None, False
    machine, _ = Machine.objects.update_or_create(
        serial=serial, verified_port=port or "",
        defaults={
            "site": connection.get("site", ""),
            "hostname": hostname,
            "last_seen": now,
        })
    snapshot, created = machine.snapshots.get_or_create(
        fingerprint=fingerprint(doc), defaults={"document": doc})
    if not created:
        snapshot.last_confirmed = now
        snapshot.save(update_fields=["last_confirmed"])
    changed = machine.latest_snapshot_id != snapshot.id
    if changed:
        machine.latest_snapshot = snapshot
        machine.save(update_fields=["latest_snapshot"])
    return machine, changed


def _machine_for(serial, port, what):
    """The machine a status or event is about, or None (logged): the row of
    this serial registered from the same port, or from none for an
    unstamped message. A stamped beat reaches nothing of any other port."""
    machine = Machine.objects.filter(serial=serial, verified_port=port or "").first()
    if machine is None:
        log.warning("%s for unknown machine %r%s dropped", what, serial,
                    f" on port {port}" if port else "")
    return machine


def status(serial, payload, port=None):
    """Apply a status-topic payload (60 s beat, LWT, or shutdown notice)."""
    machine = _machine_for(serial, port, "status")
    if machine is None:
        return None
    machine.online = bool(payload.get("online"))
    machine.last_seen = timezone.now()
    if "boot_id" in payload:
        machine.last_boot_id = payload["boot_id"]
    if "uptime_s" in payload:
        machine.last_uptime_s = payload["uptime_s"]
    machine.save(update_fields=["online", "last_seen", "last_boot_id",
                                "last_uptime_s"])
    return machine


# fpgas-verify (fpgas.online-test-designs) publishes `fpga-verifying` as the
# FPGA boot check starts and `fpga-verified` when it is done.
FPGA_STAGES = ("fpga-verifying", "fpga-verified")


def fpga_states():
    """{machine id: state} of each machine's FPGA boot check in the boot it is
    running now: "verifying" from `fpga-verifying` until an `fpga-verified`
    follows, then that event's detail["result"] ("pass", "fail",
    "missing", ...). A machine whose check has not started this boot (or
    that has none) is absent: an earlier boot's result says nothing about the
    board now.

    The newest event wins, newest by arrival (id), not by the Pi's
    timestamp: `fpga-verifying` goes out early in the boot, often before the
    Pi's clock is set. The broker is open on the site LAN, so a detail that
    is not a dict (or has no result) is "unknown" rather than breaking every
    page that asks."""
    states = {}
    events = BootEvent.objects.filter(
        stage__in=FPGA_STAGES, boot_id=F("machine__last_boot_id")) \
        .exclude(boot_id="").values_list("machine_id", "stage", "detail") \
        .order_by("id")
    for machine_id, stage, detail in events:
        if stage == "fpga-verifying":
            states[machine_id] = "verifying"
        elif isinstance(detail, dict) and detail.get("result"):
            states[machine_id] = str(detail["result"])
        else:
            states[machine_id] = "unknown"
    return states


def machine_hosts():
    """{hostname: machine id} of the machine most recently seen with each
    registered hostname (the short name: pi-sw<s>-p<p> at a VLAN-per-port
    site, so a port). A machine that left a port keeps its last
    registration, so only the newest machine on a hostname speaks for it. A
    machine registered from a stamped port (verified_port) outranks any that
    only claimed the hostname, whenever that claim was heard, and speaks
    only for its own port: a row stamped with one port that names another
    hostname cannot exist, and is skipped if it does. A machine that
    registered no hostname is on no port."""
    seen_on = {}
    for pk, hostname, seen, port in Machine.objects.values_list(
            "pk", "hostname", "last_seen", "verified_port"):
        host = hostname.split(".")[0]
        if port and port != host:
            continue
        rank = (bool(port), seen)
        if host and (host not in seen_on or rank > seen_on[host][0]):
            seen_on[host] = (rank, pk)
    return {host: pk for host, (_, pk) in seen_on.items()}


def checked_in():
    """The ids of the machines that are online and whose last status
    beat (the Pi's fleet agent sends one every 60 s) came within
    CHECKED_IN_WITHIN: a Pi that died without its last will being heard
    stays `online`, but stops beating."""
    since = timezone.now() - CHECKED_IN_WITHIN
    return set(Machine.objects.filter(online=True, last_seen__gte=since)
               .values_list("pk", flat=True))


def offered_hosts():
    """{hostname: machine id} of the machines the /fpgas/ pages offer: the newest
    machine on each hostname (machine_hosts), when it has checked in
    recently (checked_in) and its FPGA check passed in the boot it is
    running now (fpga_states)."""
    states = fpga_states()
    live = checked_in()
    return {host: pk for host, pk in machine_hosts().items()
            if pk in live and states.get(pk) == "pass"}


def found_boards():
    """{machine id: [{"board", "variant", "where"}, ...]}: the boards each machine's
    FPGA check found in the boot it is running now (`fpga-board-found`, one
    per board, before any test), in the order they were found. A board seen
    again in the same place (the check run again) is the newest sighting. A
    machine that found none this boot is absent: an earlier boot's boards may
    have been unplugged since.

    The broker is open on the site LAN, so a detail that does not name a
    board as a string is left out rather than breaking every page that asks."""
    boards = {}
    events = BootEvent.objects.filter(
        stage="fpga-board-found", boot_id=F("machine__last_boot_id")) \
        .exclude(boot_id="").values_list("machine_id", "detail").order_by("id")
    for machine_id, detail in events:
        if not isinstance(detail, dict) or not isinstance(detail.get("board"), str) \
                or not detail["board"]:
            continue
        board = {key: str(detail.get(key, "")) for key in ("board", "variant", "where")}
        boards.setdefault(machine_id, {})[board["where"]] = board
    return {pk: list(places.values()) for pk, places in boards.items()}


def boot_event(serial, payload, port=None):
    """Record one boot-stage event ({"stage","boot_id","ts","detail"})."""
    machine = _machine_for(serial, port, "boot event")
    if machine is None:
        return None
    ts = parse_datetime(payload.get("ts") or "") or timezone.now()
    return BootEvent.objects.create(
        machine=machine, boot_id=payload.get("boot_id", ""),
        stage=payload["stage"], detail=payload.get("detail") or {}, ts=ts)
