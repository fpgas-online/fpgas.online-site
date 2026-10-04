"""Transport-agnostic ingest services.

The MQTT consumer (and any future transport) calls these; they own all DB
semantics. `fingerprint` must stay byte-identical to the Pi agent's
implementation: canonical JSON (sorted keys, compact separators) → SHA-256.
"""

import datetime
import hashlib
import json
import logging
import re

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


def register_document(doc):
    """Ingest a registration document. Returns (machine, changed) where
    changed means the machine's latest snapshot moved to a different
    fingerprint (a flap back to a previously seen document reuses its row)."""
    now = timezone.now()
    connection = doc.get("connection", {})
    machine, _ = Machine.objects.update_or_create(
        serial=doc["machine"]["serial"],
        defaults={
            "site": connection.get("site", ""),
            "hostname": connection.get("hostname", ""),
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


def status(serial, payload):
    """Apply a status-topic payload (60 s beat, LWT, or shutdown notice)."""
    machine = Machine.objects.filter(serial=serial).first()
    if machine is None:
        log.warning("status for unknown machine %s dropped", serial)
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
    registration, so only the newest machine on a hostname speaks for it; a
    machine that registered no hostname is on no port."""
    seen_on = {}
    for serial, hostname, seen in Machine.objects.values_list("serial", "hostname", "last_seen"):
        host = hostname.split(".")[0]
        if host and (host not in seen_on or seen > seen_on[host][0]):
            seen_on[host] = (seen, serial)
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


_BOARD_KEY = re.compile(r"board(0|[1-9][0-9]*)")


def verified_boards():
    """{serial: [{"board", "variant", "result", "identity"}, ...]}: the boards
    each machine's FPGA check reported in the `fpga-verified` event of the
    boot it is running now, in the order the check lists them. `board` is the
    board's kind (`board<i>_identity_kind`, else the name in `board<i>` up to
    any "@"), `variant` is "" when none was decided, and `identity` holds the
    `board<i>_identity_*` fields. A machine whose check is running again, or
    whose event names no board, is absent.

    This, not `fpga-board-found`, says what a Pi carries: it is the one event
    always sent (a broker that does not answer stops the progress events),
    and the variant of a board that is only identified during its check (a
    Tiny Tapeout board) is not known when `fpga-board-found` goes out.

    The broker is open on the site LAN, so an entry that is not
    "<board> <variant> <result>" is left out rather than breaking every page
    that asks."""
    newest = {}
    events = BootEvent.objects.filter(
        stage__in=FPGA_STAGES, boot_id=F("machine__last_boot_id")) \
        .exclude(boot_id="").values_list("machine__serial", "stage", "detail").order_by("id")
    for serial, stage, detail in events:
        newest[serial] = detail if stage == "fpga-verified" else None
    out = {}
    for serial, detail in newest.items():
        if not isinstance(detail, dict):
            continue
        boards = []
        for index in sorted(int(m[1]) for key in detail if (m := _BOARD_KEY.fullmatch(key))):
            words = detail[f"board{index}"].split() if isinstance(detail[f"board{index}"], str) else []
            if len(words) != 3:
                continue
            name, variant, result = words
            prefix = f"board{index}_identity_"
            identity = {key[len(prefix):]: str(value) for key, value in detail.items()
                        if key.startswith(prefix)}
            boards.append({"board": identity.get("kind") or name.split("@")[0],
                           "variant": "" if variant == "-" else variant,
                           "result": result, "identity": identity})
        if boards:
            out[serial] = boards
    return out


def boot_event(serial, payload):
    """Record one boot-stage event ({"stage","boot_id","ts","detail"})."""
    machine = Machine.objects.filter(serial=serial).first()
    if machine is None:
        log.warning("boot event for unknown machine %s dropped", serial)
        return None
    ts = parse_datetime(payload.get("ts") or "") or timezone.now()
    return BootEvent.objects.create(
        machine=machine, boot_id=payload.get("boot_id", ""),
        stage=payload["stage"], detail=payload.get("detail") or {}, ts=ts)
