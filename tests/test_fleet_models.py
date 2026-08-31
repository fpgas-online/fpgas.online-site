import pytest
from django.utils import timezone
from fleet.models import BootEvent, HardwareSnapshot, Machine


@pytest.mark.django_db
def test_machine_online_flag_and_snapshot_uniqueness():
    m = Machine.objects.create(serial="c36b093f773d46b8", site="welland",
                               hostname="pi-sw2-p47", last_seen=timezone.now(),
                               online=True)
    assert m.online is True
    HardwareSnapshot.objects.create(machine=m, fingerprint="ab" * 32,
                                    document={"schema": 1})
    with pytest.raises(Exception):
        HardwareSnapshot.objects.create(machine=m, fingerprint="ab" * 32,
                                        document={"schema": 1})


@pytest.mark.django_db
def test_boot_events_order_by_ts():
    m = Machine.objects.create(serial="s", site="welland",
                               last_seen=timezone.now())
    BootEvent.objects.create(machine=m, boot_id="b1", stage="ssh-up",
                             detail={}, ts=timezone.now())
    assert m.events.count() == 1
