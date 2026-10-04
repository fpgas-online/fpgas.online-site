import json

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


@pytest.mark.django_db
def test_detail_says_what_the_labels_still_need(c):
    register_document(DOC)
    html = c.get("/fleet/abc123/").content.decode()
    assert "<h2>Labels</h2>" in html and "Not enough for full labels yet" in html
    assert "<td>board</td><td class=\"missing\">revision, header, fan, rtc_battery</td>" in html
    assert "no usable pi-identified event from this Pi" in html
    assert 'href="/fleet/abc123/rpi-hwid.json"' in html


@pytest.mark.django_db
def test_label_input_download_is_named_for_the_host(c):
    register_document(DOC)
    r = c.get("/fleet/abc123/rpi-hwid.json")
    assert r.status_code == 200 and r["Content-Type"] == "application/json"
    assert r["Content-Disposition"] == 'attachment; filename="pi-sw2-p47.json"'
    doc = json.loads(r.content)
    assert doc["schema"] == "rpi-hwid/label-input" and doc["host"] == "pi-sw2-p47"
    assert doc["summary"]["model"] == "Raspberry Pi 5"


@pytest.mark.django_db
def test_label_input_of_unknown_machine_is_404(c):
    assert c.get("/fleet/nope/rpi-hwid.json").status_code == 404
