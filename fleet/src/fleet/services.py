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

from django.db.models import F, Max
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


# `board<i>` in an fpga-verified detail. At most three digits: the broker is
# open, and int() of a key with thousands of digits raises.
_BOARD_KEY = re.compile(r"board(0|[1-9][0-9]{0,2})")
# No Pi carries more boards than this; further entries are not read.
MAX_BOARDS = 16
# A `board<i>` entry that is not "<board> <variant> <result>". It is kept, so
# that a Pi whose report cannot be read is not mistaken for one that
# reported no board.
UNREADABLE = {"board": "", "variant": "", "result": "", "identity": {}}


def _reported_boards(detail):
    """The boards in one fpga-verified detail, in the check's order."""
    boards = []
    indices = sorted(int(m[1]) for key in detail if isinstance(key, str) and (m := _BOARD_KEY.fullmatch(key)))
    for index in indices[:MAX_BOARDS]:
        entry = detail[f"board{index}"]
        words = entry.split() if isinstance(entry, str) else []
        if len(words) != 3:
            boards.append(dict(UNREADABLE))
            continue
        name, variant, result = words
        prefix = f"board{index}_identity_"
        identity = {key[len(prefix):]: str(value) for key, value in detail.items()
                    if isinstance(key, str) and key.startswith(prefix)}
        boards.append({"board": name, "variant": "" if variant == "-" else variant,
                       "result": result, "identity": identity})
    return boards


def fpga_reports():
    """{serial: (state, boards)} of each machine's FPGA boot check in the boot
    it is running now, from one read of its events: `state` as fpga_states()
    gives it, and `boards` the boards its `fpga-verified` event reported
    ([] while the check is running again, or when it named none).

    Each board is {"board", "variant", "result", "identity"}: `board` is the
    name in `board<i>` (fpgas-verify's board module: "acorn", "tt", ...),
    `variant` is "" when none was decided, and `identity` holds the
    `board<i>_identity_*` fields. The identity's own `kind` is not used for
    `board`: it is rpi-hwid's name for the design found ("pcileech" on an
    Acorn card), not which module checked it. An entry that is not
    "<board> <variant> <result>" is UNREADABLE.

    This, not `fpga-board-found`, says what a Pi carries: it is the one event
    always sent (a broker that does not answer stops the progress events),
    and the variant of a board that is only identified during its check (a
    Tiny Tapeout board) is not known when `fpga-board-found` goes out.

    State and boards come from the same event, so a check that starts again
    between two reads cannot leave a Pi "passed" with no boards."""
    reports = {}
    events = BootEvent.objects.filter(
        stage__in=FPGA_STAGES, boot_id=F("machine__last_boot_id")) \
        .exclude(boot_id="").values_list("machine__serial", "stage", "detail").order_by("id")
    for serial, stage, detail in events:
        if stage == "fpga-verifying":
            reports[serial] = ("verifying", [])
        elif isinstance(detail, dict) and detail.get("result"):
            reports[serial] = (str(detail["result"]), _reported_boards(detail))
        else:
            reports[serial] = ("unknown", [])
    return reports


def verified_boards():
    """{serial: boards} of the machines whose `fpga-verified` event of this
    boot reported boards (fpga_reports)."""
    return {serial: boards for serial, (_, boards) in fpga_reports().items() if boards}


def offered_boards():
    """{hostname: boards} of the machines the /fpgas/ pages offer
    (offered_hosts), each with the boards its check reported, from the same
    read of the events that says it passed."""
    reports = fpga_reports()
    live = checked_in()
    return {host: reports[serial][1] for host, serial in machine_hosts().items()
            if serial in live and reports.get(serial, ("", []))[0] == "pass"}


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


def reporting_machines():
    """What each registered machine's FPGA boot check reported, for the pages
    that follow the device the check found: a list of
    {"serial", "hostname", "checked_in", "last_seen", "state", "boards",
    "last_boards", "last_report"}.

    `hostname` is the short name the machine registered (pi-sw<s>-p<p> at a
    VLAN-per-port site: where it is now, by its own word; nothing here or in
    any catalogue says where a machine should be). `state` and `boards` are
    fpga_reports()'s for the boot the machine is running: "" and [] before
    its check has started, "verifying" and [] while it runs.

    `last_boards` are the boards of the newest `fpga-verified` event of the
    machine that named any board, from whichever boot, and `last_report` is
    that event's id (0 when there is none): arrival order across the fleet,
    so of two machines that named the same board the higher one named it
    last. A machine keeps them while it restarts, while its check runs again
    and after a check that found nothing, because a board that has hung or
    dropped off its USB port is still on that machine and is the one that
    needs its Reset. A machine that has stopped checking in is still here,
    with `checked_in` False, for the same reason.

    Only the machine most recently seen with each hostname is given
    (machine_hosts): one that left a port keeps its last registration. A
    machine that registered no hostname is left out: it cannot be reached."""
    reports = fpga_reports()
    live = checked_in()
    newest = set(machine_hosts().values())
    named = BootEvent.objects.filter(stage="fpga-verified", detail__has_key="board0", machine__serial__in=newest) \
        .values("machine__serial").annotate(newest=Max("id")).values_list("newest", flat=True)
    last = {serial: (event_id, _reported_boards(detail) if isinstance(detail, dict) else [])
            for serial, event_id, detail in BootEvent.objects.filter(id__in=list(named))
            .values_list("machine__serial", "id", "detail")}
    machines = []
    rows = Machine.objects.filter(serial__in=newest).values_list("serial", "hostname", "last_seen")
    for serial, hostname, last_seen in rows:
        state, boards = reports.get(serial, ("", []))
        last_report, last_boards = last.get(serial, (0, []))
        machines.append({"serial": serial, "hostname": hostname.split(".")[0], "checked_in": serial in live,
                         "last_seen": last_seen, "state": state, "boards": boards,
                         "last_boards": last_boards, "last_report": last_report})
    return machines
