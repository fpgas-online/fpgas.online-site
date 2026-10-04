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


def _live(machine):
    """Whether the machine is online with a status beat inside
    CHECKED_IN_WITHIN (checked_in, for one machine)."""
    return machine.online and \
        machine.last_seen >= timezone.now() - CHECKED_IN_WITHIN


def register_document(doc, port=None):
    """Ingest a registration document. Returns (machine, changed) where
    changed means the machine's latest snapshot moved to a different
    fingerprint (a flap back to a previously seen document reuses its row).
    A registration that is refused returns (None, False) and is logged.

    `port` is the port the broker's per-port listener stamped on the topic
    (the short hostname, already validated), or None for a message with no
    stamp. A stamped registration is believed over its own payload:

    - it must name the port's own hostname, or it is refused;
    - a machine that is online on another port is not moved by it (a Pi on
      one port must not unlist the board on another by registering that
      board's serial); a machine that has gone quiet there is, as a board
      that moved.

    An unstamped registration is as it always was, except that it cannot
    touch a machine a stamped one has placed on a port."""
    now = timezone.now()
    serial = doc["machine"]["serial"]
    connection = doc.get("connection", {})
    hostname = connection.get("hostname", "")
    existing = Machine.objects.filter(serial=serial).first()
    defaults = {
        "site": connection.get("site", ""),
        "hostname": hostname,
        "last_seen": now,
    }
    if port is None:
        if existing is not None and existing.verified_port:
            log.warning("unstamped registration for %s dropped: it registered"
                        " from port %s", serial, existing.verified_port)
            return None, False
    else:
        if hostname.split(".")[0] != port:
            log.warning("registration for %s from port %s dropped: it names"
                        " host %r", serial, port, hostname)
            return None, False
        if existing is not None and existing.verified_port not in ("", port) \
                and _live(existing):
            log.warning("registration for %s from port %s dropped: it is"
                        " online on port %s", serial, port,
                        existing.verified_port)
            return None, False
        defaults["verified_port"] = port
    machine, _ = Machine.objects.update_or_create(serial=serial, defaults=defaults)
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
    """The machine a status or event is about, or None (logged). A stamped
    message (`port` set) is only about the machine that registered from that
    port; an unstamped one is not about a machine a stamped registration has
    placed on a port, whose beats and events come stamped."""
    machine = Machine.objects.filter(serial=serial).first()
    if machine is None:
        log.warning("%s for unknown machine %s dropped", what, serial)
        return None
    wrong = bool(machine.verified_port) if port is None \
        else machine.verified_port != port
    if wrong:
        log.warning("%s for %s dropped: sent %s, registered from port %s",
                    what, serial,
                    f"from port {port}" if port else "unstamped",
                    machine.verified_port or "(none)")
        return None
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
    """{serial: state} of each machine's FPGA boot check in the boot it is
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
        .exclude(boot_id="").values_list("machine__serial", "stage", "detail") \
        .order_by("id")
    for serial, stage, detail in events:
        if stage == "fpga-verifying":
            states[serial] = "verifying"
        elif isinstance(detail, dict) and detail.get("result"):
            states[serial] = str(detail["result"])
        else:
            states[serial] = "unknown"
    return states


def verified_serials():
    """The serials of the machines whose FPGA check passed in the boot they
    are running now, and that are not being checked again."""
    return {serial for serial, state in fpga_states().items()
            if state == "pass"}


def machine_hosts():
    """{hostname: serial} of the machine most recently seen with each
    registered hostname (the short name: pi-sw<s>-p<p> at a VLAN-per-port
    site, so a port). A machine that left a port keeps its last
    registration, so only the newest machine on a hostname speaks for it, the
    newest of those a stamped registration placed there when there are any
    (verified_port); a machine that registered no hostname is on no port."""
    seen_on = {}
    for serial, hostname, seen, port in Machine.objects.values_list(
            "serial", "hostname", "last_seen", "verified_port"):
        host = hostname.split(".")[0]
        # a machine a stamped registration put on a port outranks any that
        # only claimed it, whenever that claim was heard
        rank = (bool(port), seen)
        if host and (host not in seen_on or rank > seen_on[host][0]):
            seen_on[host] = (rank, serial)
    return {host: serial for host, (_, serial) in seen_on.items()}


def checked_in():
    """The serials of the machines that are online and whose last status
    beat (the Pi's fleet agent sends one every 60 s) came within
    CHECKED_IN_WITHIN: a Pi that died without its last will being heard
    stays `online`, but stops beating."""
    since = timezone.now() - CHECKED_IN_WITHIN
    return set(Machine.objects.filter(online=True, last_seen__gte=since)
               .values_list("serial", flat=True))


def offered_hosts():
    """{hostname: serial} of the machines the /fpgas/ pages offer: the newest
    machine on each hostname (machine_hosts), when it has checked in
    recently (checked_in) and its FPGA check passed in the boot it is
    running now (fpga_states)."""
    states = fpga_states()
    live = checked_in()
    return {host: serial for host, serial in machine_hosts().items()
            if serial in live and states.get(serial) == "pass"}


def found_boards():
    """{serial: [{"board", "variant", "where"}, ...]}: the boards each machine's
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
        .exclude(boot_id="").values_list("machine__serial", "detail").order_by("id")
    for serial, detail in events:
        if not isinstance(detail, dict) or not isinstance(detail.get("board"), str) \
                or not detail["board"]:
            continue
        board = {key: str(detail.get(key, "")) for key in ("board", "variant", "where")}
        boards.setdefault(serial, {})[board["where"]] = board
    return {serial: list(places.values()) for serial, places in boards.items()}


def boot_event(serial, payload, port=None):
    """Record one boot-stage event ({"stage","boot_id","ts","detail"})."""
    machine = _machine_for(serial, port, "boot event")
    if machine is None:
        return None
    ts = parse_datetime(payload.get("ts") or "") or timezone.now()
    return BootEvent.objects.create(
        machine=machine, boot_id=payload.get("boot_id", ""),
        stage=payload["stage"], detail=payload.get("detail") or {}, ts=ts)
