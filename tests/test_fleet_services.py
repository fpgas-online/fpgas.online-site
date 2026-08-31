import pytest

from fleet.models import Machine
from fleet.services import boot_event, fingerprint, register_document, status

DOC = {"schema": 1, "machine": {"serial": "c36b093f773d46b8"},
       "connection": {"site": "welland", "hostname": "pi-sw2-p47"},
       "peripherals": {"usb": []}}


def test_fingerprint_stable_and_order_insensitive():
    assert fingerprint({"x": 1, "y": [2]}) == fingerprint({"y": [2], "x": 1})
    assert len(fingerprint(DOC)) == 64


@pytest.mark.django_db
def test_register_dedupes_and_appends_only_on_change():
    m, changed = register_document(DOC)
    assert changed and m.snapshots.count() == 1
    _, changed = register_document(DOC)
    assert not changed and m.snapshots.count() == 1
    doc2 = {**DOC, "peripherals": {"usb": [{"vid": "0403", "pid": "6010"}]}}
    m, changed = register_document(doc2)
    assert changed and m.snapshots.count() == 2
    register_document(DOC)                       # flap back reuses the row
    assert Machine.objects.get().snapshots.count() == 2


@pytest.mark.django_db
def test_status_drives_online_flag_and_ignores_unknown():
    register_document(DOC)
    m = status("c36b093f773d46b8", {"online": True, "boot_id": "b1",
                                    "uptime_s": 61})
    assert m.online and m.last_boot_id == "b1"
    m = status("c36b093f773d46b8", {"online": False, "reason": "connection-lost"})
    assert m.online is False
    assert status("nope", {"online": True}) is None


@pytest.mark.django_db
def test_boot_event_recorded_with_stage_and_boot_id():
    register_document(DOC)
    ev = boot_event("c36b093f773d46b8",
                    {"stage": "ssh-up", "boot_id": "b1",
                     "ts": "2026-08-31T07:00:00Z", "detail": {}})
    assert ev.stage == "ssh-up"
    assert boot_event("nope", {"stage": "x", "boot_id": "b"}) is None
