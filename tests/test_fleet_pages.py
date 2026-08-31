import pytest
from django.test import Client
from django.utils import timezone

from fleet.models import BootEvent, Machine
from fleet.services import register_document

DOC = {"schema": 1, "machine": {"serial": "abc123", "model": "Raspberry Pi 5"},
       "connection": {"site": "welland", "hostname": "pi-sw2-p47"},
       "fpga": {"boards": [{"kind": "acorn-cle-215+"}]}}


@pytest.fixture
def c():
    return Client(HTTP_HOST="welland.fpgas.online")


@pytest.mark.django_db
def test_list_shows_machine_model_board_and_badge(c):
    register_document(DOC)
    html = c.get("/fleet/").content.decode()
    assert "pi-sw2-p47" in html and "acorn-cle-215+" in html
    assert "Raspberry Pi 5" in html and "offline" in html  # no status yet


@pytest.mark.django_db
def test_detail_shows_history_and_events(c):
    register_document(DOC)
    register_document({**DOC, "fpga": {"boards": []}})
    m = Machine.objects.get()
    BootEvent.objects.create(machine=m, boot_id="b1", stage="ssh-up",
                             detail={}, ts=timezone.now())
    html = c.get("/fleet/abc123/").content.decode()
    assert html.count("<details") >= 2 and "ssh-up" in html
