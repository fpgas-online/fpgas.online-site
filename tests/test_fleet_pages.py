import json
import re

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
def test_the_fleet_front_page_is_the_pi_dashboard(c):
    """#94: one dashboard of the Pis. /fleet/ is it; each Pi links to its own fleet page."""
    m, _ = register_document(DOC)
    html = c.get("/fleet/").content.decode()
    assert "<h1>Raspberry Pis</h1>" in html
    # the Pi by its label, linked to its own page; the port apart. This registration names a Raspberry Pi but sends
    # no revision code, so rpi-hwid cannot make its label: the serial stands, and Notes says why
    assert f'<a href="/fleet/{m.serial}/">abc123</a>' in html and ">sw2 p47</td>" in html
    assert '<td class="mgmt-reason">no revision code</td>' in html
    # registered, no status beat yet: not offered; its board as the registration names it
    assert "<td>acorn-cle-215+</td>" in html and "needs attention" in html


@pytest.mark.django_db
def test_detail_shows_history_and_events(c):
    register_document(DOC)
    register_document({**DOC, "fpga": {"boards": []}})
    m = Machine.objects.get()
    BootEvent.objects.create(machine=m, boot_id="b1", stage="ssh-up",
                             detail={}, ts=timezone.now())
    html = c.get("/fleet/abc123/").content.decode()
    # the newest registration laid out, the earlier one collapsed; this boot's events in a table
    assert html.count('<pre class="mgmt-json">') == 2 and html.count("<details") == 1
    assert "<td>ssh-up</td>" in html


@pytest.mark.django_db
def test_detail_says_what_the_labels_still_need(c):
    register_document(DOC)
    html = c.get("/fleet/abc123/").content.decode()
    assert "<h2>Labels</h2>" in html and '<th scope="col">Still needs</th>' in html
    # each field rpi-hwid still needs, and who sends it (#93 item 4)
    for field, owner in (("revision", "registration"), ("header", "pi-identified")):
        assert re.search(rf'<td><code>{field}</code></td><td title="[^"]*">{owner}</td>', html)
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
