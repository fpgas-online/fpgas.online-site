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
    assert re.search(r'<span class="mgmt-state-operational">●</span> operational</td>', html)  # one set (#131)
    # the same names and forms as the Pi's row on /fleet/ (#132): the port the gateway saw, the model short with the
    # registered one on hover, the boot check as one character
    assert 'title="the gateway leased it an address on this port at ' in html and ">sw2 p46</td>" in html
    assert 'title="registered as Raspberry Pi 5 Model B Rev 1.0">Pi 5</td>' in html
    assert re.search(r'Boot check</th><td title="passed, [^"]*"><span class="mgmt-test-pass">✓</span>', html)
    # under Raspberry Pis in the one navigation (#131); the page's heading names the host by its label
    assert '<a href="/fleet/" aria-current="page">Raspberry Pis</a>' in html and "Pi 5 4 GB 285df3f84af242d0" in html


@pytest.mark.django_db
def test_every_board_the_check_found_is_shown_with_its_tests_and_docs_label_or_not():
    """#93 item 3: a Fomu gets no rpi-hwid label, but the check found it; it is on the page."""
    detail = {"result": "pass", "board0": "acorn - pass", "board0_identity_dna": "0x00200c8664b04854",
              "board0_tests": "pin-id=pass ddr=fail sdk=pass", "board1": "fomu evt pass", "board1_tests": "uart=pass"}
    m = pi("pi-sw1-p17", detail, serial="00000000cc479fd1")
    html = page(m.serial)
    boards = html.split("<h2 id=\"boards\">Boards</h2>", 1)[1].split("</table>", 1)[0]
    # a row a board: its type, the id on its label (its name on hover), its result and a mark per check, grouped as on
    # /fleet/ (#132: "suffers from many of the issues I told you about on other pages")
    assert '<td class="mgmt-c-id" data-label="ID" title="acorn-holly 0x00200c8664b04854">0x00200c8664b04854</td>' in boards
    assert '<td class="mgmt-c-board" data-label="Board">Acorn</td>' in boards
    assert '<td class="mgmt-c-board" data-label="Board" title="Fomu EVT">Fomu</td>' in boards
    # on a phone each cell says its column (the review of #135: a row of bare marks at 390 px)
    assert re.search(r'<td class="mgmt-mark" data-label="jtag" data-sort="\d"', boards)
    docs = "https://docs.fpgas.online/en/latest/boards/"
    # each mark links its check's docs page (by the name the check sent: pin-id), else the board's checks page
    assert re.search(rf'title="wiring passed"><span class="mgmt-test-pass"><a href="{docs}acorn/checks/pin-id.html">✓</a>',
                     boards)
    assert re.search(rf'title="ddr fail"><span class="mgmt-test-fail"><a href="{docs}acorn/checks/ddr-memory.html">✗</a>',
                     boards)
    assert re.search(rf'title="sdk passed"><span class="mgmt-test-pass"><a href="{docs}acorn/checks/">✓</a>', boards)
    # a check the Acorn gets and did not report is ⊘; one that does not apply is –, with no link
    assert re.search(r'title="jtag was not reported[^"]*"><span class="mgmt-test-notrun"><a ', boards)
    assert re.search(r'title="uart not applicable to Acorn: [^"]+"><span class="mgmt-test-none">–</span>', boards)
    assert f'<a href="{docs}fomu-evt/">board</a>' in boards
    assert "mgmt-fit" in html.split('<h2 id="boards">', 1)[1].split("<table", 2)[1][:80]  # as wide as its values


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
    rows = re.findall(r"<tr[^>]*>\s*<td[^>]*>.*?</tr>", history.split("<tbody>", 1)[1], re.S)
    # a row a boot (the review of 15 Oct: a "running" row and a result row of one boot were two), its result a mark
    assert len(rows) == 2
    assert 'title="the boot it is running">●' in rows[0] and 'title="failed"><span class="mgmt-test-fail">✗' in rows[0]
    assert 'title="an earlier boot">–' in rows[1] and 'title="passed"><span class="mgmt-test-pass">✓' in rows[1]
    assert '<td class="mgmt-mono" title="b0">b0</td>' in rows[1]
    # the tests of each boot, a column a check, as on /fleet/ (Tim, 16 Oct 2026: "Why doesn't the boot checks table
    # show the tests for that boot?"): b1's failed test is ✗ in its column, b0's TT board passed its check
    heads = re.findall(r'<th scope="col" class="mgmt-checkhead" title="([a-z0-9-]+): ', history)
    assert heads and len(re.findall(r'<td class="mgmt-mark" data-label="', rows[0])) == len(heads)
    assert "mgmt-test-fail" in "".join(re.findall(r'<td class="mgmt-mark" data-label=".*?</td>', rows[0]))


@pytest.mark.django_db
def test_a_boot_with_a_running_check_and_its_report_is_one_row():
    m = pi("pi-sw2-p33")
    BootEvent.objects.create(machine=m, boot_id="b1", stage="fpga-verifying", ts=timezone.now(), detail={})
    BootEvent.objects.create(machine=m, boot_id="b1", stage="fpga-verified", ts=timezone.now(), detail=REAL["pi-sw2-p33"])
    BootEvent.objects.create(machine=m, boot_id="b2", stage="fpga-verifying", ts=timezone.now(), detail={})
    history = page(m.serial).split("<h2>Boot checks</h2>", 1)[1].split("</table>", 1)[0]
    rows = re.findall(r"<tr[^>]*>\s*<td[^>]*>.*?</tr>", history.split("<tbody>", 1)[1], re.S)
    assert len(rows) == 2  # b2 running, b1 its report
    # a running boot's test cells say it is running, never a stale result
    assert 'title="the check of this boot is running: no test has reported yet"' in rows[0]
    assert 'title="running"><span class="mgmt-test-running">⟳' in rows[0] and "mgmt-test-fail" in rows[1]


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
    assert 'title="sent no result"><span class="mgmt-test-fail">✗' in history and "unknown" not in history
    assert html.count("mgmt-sortable") == 4 and "management/table.js" in html  # boards, history, labels, events


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


@pytest.mark.django_db
def test_a_host_whose_check_finds_no_board_has_the_same_verdict_in_its_history_and_facts():
    """Tim, 16 Oct 2026 (coord-65, site #144): an Orange Pi whose check finds no board needs attention: ✗ "found no
    board" in its history, as in its facts and on /fleet/."""
    from tests.test_management_fpgas import _orange_pi

    m = _orange_pi("pi-sw2-p18", "02c000810b122044", {"result": "missing"})
    html = page(m.serial)
    history = html.split("<h2>Boot checks</h2>", 1)[1].split("</table>", 1)[0]
    assert 'title="found no board"><span class="mgmt-test-fail">✗' in history
    assert "mgmt-test-pass" not in history
    assert "needs attention: no board found" in html and "not offered: boot check found no board" in html


@pytest.mark.django_db
def test_what_keeps_rpi_hwid_from_a_label_is_the_event_it_lacks_with_the_sentence_on_hover():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    labels = page(m.serial).split("<h2>Labels</h2>", 1)[1].split("</table>", 1)[0]
    assert ('<td title="no usable pi-identified event from this Pi"><code>pi-identified</code></td>' in labels)
    assert ">no usable pi-identified event from this Pi<" not in labels  # never as a cell's text


@pytest.mark.django_db
def test_the_new_fpgas_verify_report_reads_the_same_on_the_pis_own_page():
    from tests.test_management_fpgas import NEW_VERIFY_TT_FPGA

    m = pi("pi-sw2-p34", NEW_VERIFY_TT_FPGA, serial="1000000085948b10")
    boards = page(m.serial).split('<h2 id="boards">Boards</h2>', 1)[1].split("</table>", 1)[0]
    assert re.search(r'data-label="spram" data-sort="1" title="spram did not run: no boot-check test yet: '
                     r'fpgas.online-test-designs #343"><span class="mgmt-test-notrun">', boards)
    # the Pi's own page has a column per check its boards' types get: none for jtag on a TT FPGA
    assert 'data-label="jtag"' not in boards and 'data-label="wiring"' in boards


# --- the labels, made on the server (#141) ------------------------------------------------------------------------

DATA = __import__("pathlib").Path(__file__).parent / "data"


def _with_label_input(monkeypatch, name):
    """A Pi whose label input is one welland served on 16 Oct 2026 (/fleet/<serial>/rpi-hwid.json)."""
    import json

    from fleet import hwid

    document = json.loads((DATA / name).read_text())
    monkeypatch.setattr(hwid, "build", lambda machine: hwid.Built(document=document))
    return pi("pi-sw2-p41", REAL["pi-sw2-p46"], serial=document["summary"]["serial"])


@pytest.mark.django_db
def test_a_label_rpi_hwid_can_make_is_on_the_page_as_an_image_linking_its_pdf(monkeypatch):
    """Tim, 16 Oct 2026: "Why are labels still not being rendered on the device page?" The Acorn acorn-linden's
    label is made from what it sent; its Pi's label still needs fields, said in the table below it."""
    m = _with_label_input(monkeypatch, "label-input-acorn-linden.json")
    html = page(m.serial)
    labels = html.split("<h2>Labels</h2>", 1)[1].split("<h2>", 1)[0]
    ident = "id=pi-sw2-p41%2Facorn%2Facorn-linden+0x0044a48664b0485c"
    assert (f'<figure><a href="/fleet/{m.serial}/label.pdf?{ident}"><img src="/fleet/{m.serial}/label.png?{ident}" '
            'width="240" height="144" alt="the label acorn-linden 0x0044a48664b0485c"></a>'
            "<figcaption>acorn-linden 0x0044a48664b0485c</figcaption></figure>") in labels
    assert "<code>header</code>" in labels  # the Pi label's gap, a row
    c = Client(HTTP_HOST=WELLAND)
    png = c.get(f"/fleet/{m.serial}/label.png?{ident}")
    assert png.status_code == 200 and png["Content-Type"] == "image/png"
    assert png.content.startswith(b"\x89PNG") and (int.from_bytes(png.content[16:20]), int.from_bytes(
        png.content[20:24])) == (720, 432)  # 63.5 x 38.1 mm at 4 px a point
    pdf = c.get(f"/fleet/{m.serial}/label.pdf?{ident}")
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert pdf["Content-Disposition"] == 'inline; filename="rpi-hwid.pdf"'  # a title with a space is not a file name


@pytest.mark.django_db
@pytest.mark.parametrize("ident", ["", "id=pi-sw2-p41%2Frpi%2Fanything", "id=../../etc"])
def test_a_label_rpi_hwid_does_not_make_is_404(monkeypatch, ident):
    m = _with_label_input(monkeypatch, "label-input-acorn-linden.json")
    assert Client(HTTP_HOST=WELLAND).get(f"/fleet/{m.serial}/label.png?{ident}").status_code == 404


@pytest.mark.django_db
def test_a_host_none_of_whose_labels_can_be_made_shows_no_image_and_every_gap(monkeypatch):
    """The Arty on 0000000080863963: its board label needs the flash facts its check does not read, the Pi label its
    header (fpgas.online-test-designs#76): no image, a row for each field."""
    m = _with_label_input(monkeypatch, "label-input-arty-no-flash.json")
    labels = page(m.serial).split("<h2>Labels</h2>", 1)[1].split("<h2>", 1)[0]
    assert "<img" not in labels
    for field in ("header", "flash_jedec", "flash_uid_state"):
        assert f"<code>{field}</code>" in labels


def test_a_board_rpi_hwid_refuses_keeps_no_other_boards_label_off():
    """A forged second board with a DNA rpi-hwid cannot print (the broker takes events from anything on the LAN):
    the Acorn's label is still made and drawn; the bad board is a refusal in rpi-hwid's words."""
    import json

    from fleet import labelart

    document = json.loads((DATA / "label-input-acorn-linden.json").read_text())
    good = document["summary"]["fpga"][0]
    document["summary"]["fpga"].append({**good, "dna": "nan", "serial": None})
    made, refused = labelart.made(document)
    assert [m.title for m in made] == ["acorn-linden 0x0044a48664b0485c"]
    assert any(kind == "fpga" and "nan" in str(exc) for kind, exc in refused)
    assert labelart.png(document, made[0].id).startswith(b"\x89PNG")


@pytest.mark.django_db
def test_registrations_and_boots_are_named_apart_with_what_each_is():
    """Tim, 16 Oct 2026: "Are registrations on the device page the same as the boot checks `Boot` value?" No: a
    registration is the Pi's document, kept once per different document; a boot is one power-on. The page says so
    on both names, and lists the registrations as a table, the one it sends now marked."""
    from fleet.models import HardwareSnapshot

    m = pi("pi-sw2-p33")
    old = HardwareSnapshot.objects.create(machine=m, fingerprint="a" * 64, document={"machine": {"serial": m.serial}})
    m.latest_snapshot = HardwareSnapshot.objects.create(machine=m, fingerprint="b" * 64,
                                                        document={"machine": {"serial": m.serial, "model": "x"}})
    m.save()
    html = page(m.serial)
    assert "a registration: what the Pi says it is" in html and "a boot: one power-on of the Pi" in html
    section = html.split("Registrations</h2>", 1)[1].split("</table>", 1)[0]
    rows = re.findall(r"<tr><td.*?</tr>", section, re.S)
    assert len(rows) == 2 and old.pk
    assert any('title="' + "b" * 64 + '">bbbbbbbb</td><td class="mgmt-mark" title="the one it sends now">●' in r
               for r in rows)
    assert any('title="' + "a" * 64 + '">aaaaaaaa</td><td class="mgmt-mark" title="an earlier one">–' in r for r in rows)
    assert "<summary>JSON</summary>" in section


@pytest.mark.django_db
def test_each_event_has_its_kind_by_colour_and_name_and_a_tests_result_is_a_mark():
    m = pi("pi-sw2-p33")
    for stage, detail in (("network-online", {}), ("ssh-up", {}), ("fpga-board-identified", {}),
                          ("fpga-test-finished", {"board": "acorn", "test": "ddr", "result": "fail", "reason": "x"}),
                          ("fpga-test-finished", {"board": "acorn", "test": "jtag", "result": "pass"}),
                          ("something-new", {})):
        BootEvent.objects.create(machine=m, boot_id=m.last_boot_id, stage=stage, ts=timezone.now(), detail=detail)
    events = page(m.serial).split("<h2>Events", 1)[1].split("</table>", 1)[0]
    for kind, stage in (("network", "network-online"), ("service", "ssh-up"), ("identify", "fpga-board-identified"),
                        ("test", "fpga-test-finished"), ("other", "something-new")):
        assert re.search(rf'<tr class="mgmt-ev-{kind}"><td>[^<]*</td><td title="[^"]+"><span class="mgmt-ev">{kind}'
                         rf'</span></td><td>{stage}</td>', events), kind
    assert '<td>ddr</td><td class="mgmt-mark" title="failed: x"><span class="mgmt-test-fail">✗' in events
    assert '<td>jtag</td><td class="mgmt-mark" title="passed"><span class="mgmt-test-pass">✓' in events
    css = (__import__("pathlib").Path(__file__).parents[1] / "management" / "src" / "management" / "static" /
           "management" / "management.css").read_text()
    assert all(f".mgmt-ev-{k} .mgmt-ev {{ background:" in css
               for k in ("network", "service", "identify", "check", "test", "other"))
