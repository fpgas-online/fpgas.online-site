"""A host's own page (#93), /fleet/<serial>/: the host by its label, every board its check found with its tests and
docs, the check with its history, who owes what the labels still need, and the registration as laid-out JSON."""

import datetime
import re

import pytest
from django.test import Client
from django.utils import timezone
from fleet.models import BootEvent, HardwareSnapshot

from fleet import jsonview
from tests.test_management_fpgas import REAL, pi

WELLAND = "welland.fpgas.online"


@pytest.fixture(autouse=True)
def site_address(settings):
    settings.DOMAIN_NAME = WELLAND
    settings.MANAGEMENT_TIME_ZONE = "Australia/Adelaide"


def page(serial):
    response = Client(HTTP_HOST=WELLAND).get(f"/fleet/{serial}/")
    assert response.status_code == 200
    return response.content.decode()


# --- the JSON ------------------------------------------------------------------------------------------------------


def test_a_document_is_laid_out_highlighted_and_escaped():
    html = str(jsonview.render({"b": 1, "a": "<script>", "c": [True, None], "k": "x" * 200}))
    assert html.index('j-key">&quot;a&quot;') < html.index('j-key">&quot;b&quot;')  # keys sorted
    assert '<span class="j-key">&quot;a&quot;</span>' in html and "&lt;script&gt;" in html and "<script>" not in html
    assert '<span class="j-num">1</span>' in html and '<span class="j-bool">true</span>' in html
    assert '<span class="j-null">null</span>' in html
    # a long string is cut, the whole of it in the title
    long = re.search(r'<span class="j-str" title="(x+)">&quot;(x+)…&quot;</span>', html)
    assert long and len(long[1]) == 200 and len(long[2]) == jsonview.LONG
    assert "\n  " in html  # indented


def test_a_compact_document_is_one_line():
    assert jsonview.compact({"b": 1, "a": [1, 2]}) == '{"a": [1, 2], "b": 1}'


# --- the page ------------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_page_names_the_host_by_its_label_and_says_how_it_is():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"], serial="285df3f84af242d0")
    m.latest_snapshot = HardwareSnapshot.objects.create(machine=m, fingerprint="a", document={
        "machine": {"serial": m.serial, "revision_code": "c04170", "model": "Raspberry Pi 5 Model B Rev 1.0"}})
    m.save()
    html = page(m.serial)
    assert "<h1>Pi 5 4 GB 285df3f84af242d0</h1>" in html
    assert re.search(r'<span class="badge mgmt-operational"[^>]*>operational</span>', html)
    assert ">sw2 p46</td>" in html and "<td>Raspberry Pi 5 Model B Rev 1.0</td>" in html
    # under the Pi dashboard in the crumbs
    assert '&rsaquo; <a href="/fleet/">Raspberry Pis</a> &rsaquo; Pi 5 4 GB 285df3f84af242d0' in html


@pytest.mark.django_db
def test_every_board_the_check_found_is_shown_with_its_tests_and_docs_label_or_not():
    """#93 item 3: a Fomu gets no rpi-hwid label, but the check found it; it is on the page."""
    detail = {"result": "pass", "board0": "acorn - pass", "board0_identity_dna": "0x00200c8664b04854",
              "board0_tests": "pin-id=pass ddr=fail sdk=pass", "board1": "fomu evt pass", "board1_tests": "uart=pass"}
    m = pi("pi-sw1-p17", detail, serial="00000000cc479fd1")
    html = page(m.serial)
    boards = html.split("<h2 id=\"boards\">Boards</h2>", 1)[1].split("</table>", 1)[0]
    assert "acorn-holly 0x00200c8664b04854" in boards and "<td>Fomu</td>" in boards
    docs = "https://docs.fpgas.online/en/latest/boards/"
    assert f'<a href="{docs}acorn/checks/pin-id.html">pin-id</a>: pass' in boards
    assert f'<a href="{docs}acorn/checks/ddr-memory.html">ddr</a>: fail' in boards
    assert f'<a href="{docs}acorn/checks/">sdk</a>: pass' in boards  # no page of its own: the board's checks
    assert 'class="mgmt-test mgmt-test-fail"' in boards
    assert f'<a href="{docs}fomu-evt/">board</a>' in boards


@pytest.mark.django_db
def test_the_check_is_shown_with_its_history_across_boots():
    # an earlier boot's check arrived first, then this boot's (b1): newest by arrival, as the site orders them
    m = pi("pi-sw2-p33")
    BootEvent.objects.create(machine=m, boot_id="b0", stage="fpga-verified",
                             ts=timezone.now() - datetime.timedelta(days=1),
                             detail={"result": "pass", "board0": "tt tt-fpga pass"})
    BootEvent.objects.create(machine=m, boot_id="b1", stage="fpga-verified", ts=timezone.now(),
                             detail=REAL["pi-sw2-p33"])
    html = page(m.serial)
    history = html.split("<h2>Boot checks</h2>", 1)[1].split("</table>", 1)[0]
    rows = re.findall(r"<tr[^>]*>\s*<td[^>]*>.*?</tr>", history, re.S)
    assert len(rows) == 2 and "(this boot)" in rows[0] and "failed" in rows[0] and "passed" in rows[1]
    assert "<code>b0</code>" in rows[1]


@pytest.mark.django_db
def test_each_field_the_labels_still_need_says_who_sends_it():
    def labels(serial):
        return page(serial).split("<h2>Labels</h2>", 1)[1].split("<h2>", 1)[0]

    # no registration: rpi-hwid needs its model first, which the registration sends
    bare = pi("pi-sw2-p47", REAL["pi-sw2-p47"], serial="10000000eeee0047")
    assert '<td><code>model</code></td><td title="the Pi&#x27;s registration (its fleet agent)">registration</td>' in labels(
        bare.serial)
    # registered, but no pi-identified event: the header, which that event sends
    m = pi("pi-sw2-p48", REAL["pi-sw2-p47"], serial="10000000eeee0048")
    m.latest_snapshot = HardwareSnapshot.objects.create(machine=m, fingerprint="h", document={
        "machine": {"serial": m.serial, "model": "Raspberry Pi 5 Model B Rev 1.0", "revision_code": "c04170",
                    "mem_total_kb": 4000000}})
    m.save()
    text = labels(m.serial)
    assert '<td>board</td><td><code>header</code></td><td title="the Pi&#x27;s pi-identified event' in text
    assert ">pi-identified</td>" in text
    assert "no usable pi-identified event from this Pi" in text  # the site's own note, kept


@pytest.mark.django_db
def test_the_registration_is_json_not_a_python_repr():
    m = pi("pi-sw2-p10", REAL["pi-sw2-p47"])
    m.latest_snapshot = HardwareSnapshot.objects.create(machine=m, fingerprint="r", document={
        "machine": {"serial": m.serial}, "ssh": {"host_key": "ssh-ed25519 " + "A" * 100}, "ok": True})
    m.save()
    html = page(m.serial)
    record = html.split('<pre class="mgmt-json">', 1)[1].split("</pre>", 1)[0]
    assert '<span class="j-key">&quot;host_key&quot;</span>' in record and "True" not in record
    assert '<span class="j-bool">true</span>' in record and "…" in record


@pytest.mark.django_db
def test_a_time_from_the_future_is_said_in_its_cell_and_the_page_stands():
    m = pi("pi-sw2-p4", REAL["pi-sw2-p46"])
    BootEvent.objects.filter(machine=m).update(ts=timezone.now() + datetime.timedelta(hours=3))
    html = page(m.serial)
    assert "future" in html.split("<h2>Boot checks</h2>", 1)[1]


@pytest.mark.django_db
def test_an_unknown_serial_is_404():
    assert Client(HTTP_HOST=WELLAND).get("/fleet/nope/").status_code == 404



@pytest.mark.django_db
def test_the_page_carries_what_infras_verify_pi_reads():
    """fpgas.online-infra ansible/verify-pi.yml ("Server has registered this Pi in the fleet") reads this page and
    needs 'badge online' and the boot id of the boot running now in it."""
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    html = page(m.serial)
    assert 'badge online' in html and m.last_boot_id in html



@pytest.mark.django_db
def test_a_check_report_with_no_result_says_so_and_every_table_sorts():
    m = pi("pi-sw2-p21", REAL["pi-sw2-p46"])
    BootEvent.objects.create(machine=m, boot_id="b0", stage="fpga-verified", ts=timezone.now(), detail={"x": 1})
    html = page(m.serial)
    history = html.split("<h2>Boot checks</h2>", 1)[1].split("</table>", 1)[0]
    assert "<td>sent no result</td>" in history and "unknown" not in history
    assert html.count("mgmt-sortable") == 4 and "management/table.js" in html


def test_a_label_field_no_known_event_sends_is_said_loud():
    from management.views.machine import _owner

    assert _owner("board", "model") == ("registration", "the Pi's registration (its fleet agent)")
    short, why = _owner("board", "warp_drive")
    assert short == "" and "warp_drive" in why and "behind rpi-hwid" in why



@pytest.mark.django_db
def test_a_host_with_no_check_and_no_events_says_so_outside_the_tables():
    m = pi("pi-sw2-p22")  # registered, nothing reported
    html = page(m.serial)
    assert "<p>No boot check has reported.</p>" in html and "<p>No events recorded for this boot.</p>" in html
    assert "colspan" not in html  # no sentence rows inside a sortable table
