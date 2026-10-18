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
import time

from django.conf import settings
from django.db.models import F, Max
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import BootEvent, GatewayRecord, Machine

log = logging.getLogger(__name__)

# How recently a machine's status beat must have come for the /fpgas/ pages
# to offer it: three of the fleet agent's 60 s beats.
CHECKED_IN_WITHIN = datetime.timedelta(minutes=3)


def fingerprint(doc):
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def register_document(doc, retained=False):
    """Ingest a registration document. Returns (machine, changed) where
    changed means the machine's latest snapshot moved to a different
    fingerprint (a flap back to a previously seen document reuses its row).

    `retained`: the broker's stored copy, handed to the consumer when it
    subscribes (the Pi publishes its registration retained), not something
    the Pi said now: it sets last_seen only for a machine new to the site
    (the field is required), never moves it for a known one (site #119)."""
    now = timezone.now()
    connection = doc.get("connection", {})
    defaults = {"site": connection.get("site", ""), "hostname": connection.get("hostname", "")}
    machine, _ = Machine.objects.update_or_create(
        serial=doc["machine"]["serial"],
        defaults=defaults if retained else defaults | {"last_seen": now},
        create_defaults=defaults | {"last_seen": now})
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


def status(serial, payload, retained=False):
    """Apply a status-topic payload (60 s beat, LWT, or shutdown notice).

    `retained`: the broker's stored copy, replayed to the consumer when it
    subscribes (the Pi publishes its status and its will retained), so it
    says nothing about when the Pi last spoke: the state is applied, last_seen
    is left as it was (site #119: every consumer restart used to stamp every
    machine, the long-missing ones too, as seen at the restart)."""
    machine = Machine.objects.filter(serial=serial).first()
    if machine is None:
        log.warning("status for unknown machine %s dropped", serial)
        return None
    machine.online = bool(payload.get("online"))
    fields = ["online", "last_boot_id", "last_uptime_s"]
    if not retained:
        machine.last_seen = timezone.now()
        fields.append("last_seen")
    if "boot_id" in payload:
        machine.last_boot_id = payload["boot_id"]
    if "uptime_s" in payload:
        machine.last_uptime_s = payload["uptime_s"]
        # a live beat: it left the Pi uptime_s after its boot began, and arrived just now (an uptime that is not a
        # whole number of seconds is the broker's open LAN's: no boot start from it)
        if not retained and _is_int(payload["uptime_s"]) and payload["uptime_s"] >= 0:
            machine.booted_at = machine.last_seen - datetime.timedelta(seconds=payload["uptime_s"])
            fields.append("booted_at")
    machine.save(update_fields=fields)
    return machine


# fpgas-verify (fpgas.online-test-designs) publishes `fpga-verifying` as the
# FPGA boot check starts and `fpga-verified` when it is done.
FPGA_STAGES = ("fpga-verifying", "fpga-verified")

# A per-port machine's registered short name, pi-sw<switch>-p<port>: the name the gateway's DHCP gave it on the port
# it booted on.
_PORT_HOSTNAME = re.compile(r"pi-sw[1-9][0-9]*-p([1-9][0-9]*)")
# What a machine on the setup port is marked, whatever its own check reported, and why.
SETUP_PORT_STATE, SETUP_PORT_REASON = "fail", "setup port"


def on_setup_port(hostname):
    """Whether a machine registered as `hostname` booted on the site's setup / config port (settings.SETUP_PORT, the
    same port number on every switch): its name pi-sw<s>-p<p> names that port. The gateway served that boot every
    restriction lifted (fpgas.online-infra docs/pi/setup-port.md), so it is never offered: Tim, 18 Oct 2026, "The
    device should never be shown on the front page of fpgas.online (It should always be marked as failed)."
    """
    m = _PORT_HOSTNAME.fullmatch((hostname or "").split(".")[0])
    return settings.SETUP_PORT is not None and m is not None and int(m[1]) == settings.SETUP_PORT


def setup_port_state(state, hostname):
    """The check state a page uses for a machine: SETUP_PORT_STATE on the setup port, whatever the Pi reported (its
    check running, not started, or passed), else `state`."""
    return SETUP_PORT_STATE if on_setup_port(hostname) else state


def fpga_states():
    """{serial: state} of each machine's FPGA boot check in the boot it is
    running now: "verifying" from `fpga-verifying` until an `fpga-verified`
    follows, then that event's detail["result"] ("pass", "fail",
    "missing", ...); on the setup port, SETUP_PORT_STATE (setup_port_state).
    A machine whose check has not started this boot (or that has none) is
    absent: an earlier boot's result says nothing about the board now.

    The newest event wins, newest by arrival (id), not by the Pi's
    timestamp: `fpga-verifying` goes out early in the boot, often before the
    Pi's clock is set. The broker is open on the site LAN, so a detail that
    is not a dict (or has no result) is "unknown" rather than breaking every
    page that asks."""
    states = {}
    events = BootEvent.objects.filter(
        stage__in=FPGA_STAGES, boot_id=F("machine__last_boot_id")) \
        .exclude(boot_id="").values_list("machine__serial", "machine__hostname", "stage", "detail") \
        .order_by("id")
    for serial, hostname, stage, detail in events:
        if stage == "fpga-verifying":
            state = "verifying"
        elif isinstance(detail, dict) and detail.get("result"):
            state = str(detail["result"])
        else:
            state = "unknown"
        states[serial] = setup_port_state(state, hostname)
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


def is_offered(state, has_checked_in):
    """Whether a machine is offered to visitors: it has checked in recently
    (checked_in) and its FPGA check passed in the boot it is running now
    (`state` as fpga_states and fpga_reports give it). The one rule for every
    page that offers a board: the /fpgas/ pages (offered_hosts,
    offered_boards) and tinytapeout.fpgas.online's (reporting_machines'
    "offered"). A check that failed, could not finish, is running again or
    has not started this boot offers nothing."""
    return bool(has_checked_in) and state == "pass"


# A Pi not heard from for longer than this is missing, not merely offline: it is not coming back by itself.
MISSING_AFTER = datetime.timedelta(hours=6)
OPERATIONAL, ATTENTION, MISSING, UNKNOWN = "operational", "attention", "missing", "unknown"
CONDITIONS = (OPERATIONAL, ATTENTION, MISSING, UNKNOWN)


def condition(state, has_checked_in, last_seen, now=None):
    """How a registered machine is doing, for the management pages (#68), by the one rule that offers it
    (is_offered): OPERATIONAL when it is offered; MISSING when it has not checked in for MISSING_AFTER;
    UNKNOWN when it checks in and its FPGA check has not finished this boot (not started, or running);
    otherwise ATTENTION: it checks in and its check failed, could not finish or sent what cannot be read, or it
    has stopped checking in within MISSING_AFTER. So a machine that needs attention is one that is not offered
    and should be."""
    if is_offered(state, has_checked_in):
        return OPERATIONAL
    now = now or timezone.now()
    if not has_checked_in and (last_seen is None or now - last_seen > MISSING_AFTER):
        return MISSING
    if has_checked_in and state in ("", "verifying"):
        return UNKNOWN
    return ATTENTION


def offered_hosts():
    """{hostname: serial} of the machines the /fpgas/ pages offer: the newest
    machine on each hostname (machine_hosts) that is_offered."""
    states = fpga_states()
    live = checked_in()
    return {host: serial for host, serial in machine_hosts().items()
            if is_offered(states.get(serial, ""), serial in live)}


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
UNREADABLE = {"board": "", "variant": "", "result": "", "reason": "", "identity": {}}


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
        reason = detail.get(f"board{index}_reason", "")
        boards.append({"board": name, "variant": "" if variant == "-" else variant,
                       "result": result, "reason": reason if isinstance(reason, str) else "",
                       "identity": identity})
    return boards


def fpga_reports():
    """{serial: (state, boards)} of each machine's FPGA boot check in the boot
    it is running now, from one read of its events: `state` as fpga_states()
    gives it, and `boards` the boards its `fpga-verified` event reported
    ([] while the check is running again, or when it named none).

    Each board is {"board", "variant", "result", "reason", "identity"}:
    `reason` is the check's `board<i>_reason` ("" when it sent none), and `board` is the
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
        .exclude(boot_id="").values_list("machine__serial", "machine__hostname", "stage", "detail").order_by("id")
    for serial, hostname, stage, detail in events:
        if stage == "fpga-verifying":
            state, boards = "verifying", []
        elif isinstance(detail, dict) and detail.get("result"):
            state, boards = str(detail["result"]), _reported_boards(detail)
        else:
            state, boards = "unknown", []
        reports[serial] = (setup_port_state(state, hostname), boards)
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
            if is_offered(reports.get(serial, ("", []))[0], serial in live)}


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
    {"serial", "hostname", "checked_in", "last_seen", "state", "offered", "boards",
    "last_boards", "last_report"}.

    `offered` is is_offered's word for the machine: the same rule the /fpgas/
    pages offer a Pi by.

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
        state = setup_port_state(state, hostname)
        last_report, last_boards = last.get(serial, (0, []))
        machines.append({"serial": serial, "hostname": hostname.split(".")[0], "checked_in": serial in live,
                         "last_seen": last_seen, "state": state, "offered": is_offered(state, serial in live),
                         "boards": boards,
                         "last_boards": last_boards, "last_report": last_report})
    return machines


# --- the gateway's records (switch poller: fpgas/<site>/gw/port; dnsmasq script: fpgas/<site>/gw/lease) ---

PORT_LINKS = ("up", "down", "bounced")
LEASE_EVENTS = ("add", "old", "del", "released")
LEASE_CAUSES = ("link-down", "power-cycle", "power-off", "down-port")
_GATEWAY_HOSTNAME = re.compile(r"pi-sw[0-9]+-p[0-9]+")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_aware_iso(value):
    """acked_at: an ISO 8601 time with its offset (as fpgas-lease-event writes it), or None. The registry's
    network.last_ack (site #112) compares it with the Pi's own ack, so no other shape is stored."""
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    parsed = parse_datetime(value)
    return parsed is not None and not timezone.is_naive(parsed)


def _is_epoch(value):
    """expires: seconds since the epoch, as dnsmasq gives it (DNSMASQ_LEASE_EXPIRES), or None."""
    return value is None or _is_int(value) and value >= 0


def parse_gateway_record(kind, doc):
    """The validated `doc` of a gateway record of `kind` ("port" or "lease") and its time, as (doc, at). Raises
    ValueError saying what is wrong: the record is strict (schema 1, each field its type and values), so one that is
    not is dropped by the caller, never stored in part."""
    if not _is_int(doc.get("schema")) or doc["schema"] != 1:
        raise ValueError(f"schema {doc.get('schema')!r} is not 1")
    at = parse_datetime(doc["at"]) if isinstance(doc.get("at"), str) else None
    if at is None or timezone.is_naive(at):
        raise ValueError(f"at {doc.get('at')!r} is not an ISO 8601 time with a zone")
    host = doc.get("hostname")
    if host is not None and not (isinstance(host, str) and _GATEWAY_HOSTNAME.fullmatch(host)):
        raise ValueError(f"hostname {host!r} is not pi-sw<s>-p<p>")
    for name in ("switch", "port"):
        value = doc.get(name)
        if not (_is_int(value) and value >= 0 or value is None and kind == "lease"):
            raise ValueError(f"{name} {value!r} is not a whole number")
    if host is not None and doc.get("switch") is not None and doc.get("port") is not None \
            and host != f"pi-sw{doc['switch']}-p{doc['port']}":
        raise ValueError(f"hostname {host!r} is not pi-sw{doc['switch']}-p{doc['port']}, the record's own switch "
                         f"and port")
    if kind == "port":
        if doc.get("link") not in PORT_LINKS:
            raise ValueError(f"link {doc.get('link')!r} is not one of {PORT_LINKS}")
        if host is None:
            raise ValueError("a port record needs a hostname")
        return doc, at
    if doc.get("event") not in LEASE_EVENTS:
        raise ValueError(f"event {doc.get('event')!r} is not one of {LEASE_EVENTS}")
    if doc.get("family") not in (4, 6) or not _is_int(doc.get("family")):
        raise ValueError(f"family {doc.get('family')!r} is not 4 or 6")
    if not isinstance(doc.get("address"), str) or not doc["address"]:
        raise ValueError("address is not a string")
    for name in ("mac", "server", "interface"):
        if not (doc.get(name) is None or isinstance(doc[name], str)):
            raise ValueError(f"{name} {doc[name]!r} is not a string")
    if not (doc.get("lifetime_s") is None or _is_int(doc["lifetime_s"])):
        raise ValueError(f"lifetime_s {doc['lifetime_s']!r} is not a whole number")
    if not _is_aware_iso(doc.get("acked_at")):
        raise ValueError(f"acked_at {doc.get('acked_at')!r} is not an ISO 8601 time with an offset")
    if not _is_epoch(doc.get("expires")):
        raise ValueError(f"expires {doc.get('expires')!r} is not seconds since the epoch")
    if "cause" in doc and doc["cause"] not in LEASE_CAUSES:
        raise ValueError(f"cause {doc['cause']!r} is not one of {LEASE_CAUSES}")
    return doc, at


def _mac_key(mac):
    return mac.strip().lower().replace("-", ":")


def _machine_macs():
    """{mac: Machine} from each machine's latest registration document (`machine.macs`: {interface: mac})."""
    found = {}
    for machine in Machine.objects.exclude(latest_snapshot=None).select_related("latest_snapshot"):
        macs = (machine.latest_snapshot.document.get("machine") or {}).get("macs")
        if isinstance(macs, dict):
            for mac in macs.values():
                if isinstance(mac, str):
                    found[_mac_key(mac)] = machine
    return found


def match_machine(mac, hostname):
    """The Machine a gateway record is about: the one whose registration has this MAC. A record with no MAC (the
    released records of a v6 lease) falls back to the newest machine registered on its hostname (machine_hosts); a
    record WITH a MAC that no machine has is somebody else's (a visitor's laptop on the port) and matches none, so
    its ack is never credited to the Pi registered on that port."""
    if mac:
        return _machine_macs().get(_mac_key(mac))
    serial = machine_hosts().get(hostname) if hostname else None
    return Machine.objects.filter(serial=serial).first() if serial else None


# A lease `old` comes on every renewal (~11,500 rows a day on a 20-board site), so the table is pruned as records
# arrive, at most this often per process.
PRUNE_EVERY_SECONDS = 600
_last_prune = None  # time.monotonic() of this process's last prune


def prune_gateway_records():
    """Delete the gateway records older than settings.GATEWAY_RECORD_DAYS, except the newest (last stored) of each
    (site, kind, switch, port): two queries, whatever the number of rows."""
    cutoff = timezone.now() - datetime.timedelta(days=settings.GATEWAY_RECORD_DAYS)
    newest = GatewayRecord.objects.values("site", "kind", "switch", "port").annotate(newest=Max("id")).values("newest")
    deleted, _ = GatewayRecord.objects.filter(at__lt=cutoff).exclude(id__in=newest).delete()
    return deleted


def gateway_record(site, kind, doc):
    """Validate and store one gateway record, matched to its machine; then prune the old ones (at most once per
    PRUNE_EVERY_SECONDS per process). Returns the GatewayRecord; ValueError when the record is not valid (nothing is
    stored)."""
    global _last_prune
    doc, at = parse_gateway_record(kind, doc)
    mac = doc.get("mac") or None
    hostname = doc.get("hostname")
    record = GatewayRecord.objects.create(
        site=site, kind=kind, switch=doc.get("switch"), port=doc.get("port"), hostname=hostname or "", mac=mac,
        at=at, doc=doc, machine=match_machine(mac, hostname))
    if _last_prune is None or time.monotonic() - _last_prune >= PRUNE_EVERY_SECONDS:
        _last_prune = time.monotonic()
        try:
            prune_gateway_records()
        except Exception:  # the record is stored: a failed prune must not stop it being pushed to the pages
            log.exception("the gateway records could not be pruned")
    return record


def latest_gateway_lease(machine):
    """The newest lease record the gateway published for `machine`, or None. `old` comes on every renewal, so the
    record's doc["acked_at"] is the gateway's last DHCPACK to the machine unless a later `del` or `released` ended
    the lease (doc["event"] says)."""
    return machine.gateway_records.filter(kind="lease").order_by("-at", "-id").first()


def latest_gateway_ack(machine):
    """The newest lease record that is a DHCPACK from the gateway to `machine` (doc["acked_at"] set: a new lease or a
    renewal, not a del, a release, or dnsmasq's re-read at its own start), or None. Its doc["acked_at"] is the
    gateway's half of the registry's network.last_ack."""
    return (machine.gateway_records.filter(kind="lease", doc__acked_at__isnull=False)
            .exclude(doc__acked_at=None).order_by("-at", "-id").first())
