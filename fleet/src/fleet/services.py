"""Transport-agnostic ingest services.

The MQTT consumer (and any future transport) calls these; they own all DB
semantics. `fingerprint` must stay byte-identical to the Pi agent's
implementation: canonical JSON (sorted keys, compact separators) → SHA-256.
"""

import hashlib
import json
import logging

from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import BootEvent, Machine

log = logging.getLogger(__name__)


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


def verified_serials():
    """The serials of the machines whose FPGA check passed in the boot
    they are running now.

    fpgas-verify (fpgas.online-test-designs) publishes the stage
    `fpga-verified` once per boot, its result in detail["result"]. A machine
    counts only if the newest such event of its last boot says "pass": an
    earlier boot's pass says nothing about the board now, and a boot still
    checking (or whose check never ran) has no event yet. The broker is open
    on the site LAN, so a detail that is not a dict counts as no result
    rather than breaking every page that asks."""
    latest = {}
    events = BootEvent.objects.filter(
        stage="fpga-verified", boot_id=F("machine__last_boot_id")) \
        .exclude(boot_id="").values_list("machine__serial", "detail") \
        .order_by("ts")
    for serial, detail in events:
        latest[serial] = detail.get("result") if isinstance(detail, dict) else None
    return {serial for serial, result in latest.items() if result == "pass"}


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
