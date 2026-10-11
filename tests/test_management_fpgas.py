"""The FPGA dashboard (#68): every registered Pi with its port, board, boot check and check-in, and a summary by
board type, from the fleet registry, by the one rule that offers a Pi (fleet.services.is_offered)."""

import datetime
import json
import pathlib

import pytest
from django.test import Client
from django.utils import timezone
from fleet.models import BootEvent, Machine
from fleet.services import ATTENTION, MISSING, OPERATIONAL, UNKNOWN, condition

from management import fpga

# Three Pis' fpga-verified events as welland's fleet registry received them on 4 October 2026: two Acorns that
# passed, and a TT FPGA demo board that failed its pin-id and spiflash tests.
REAL = json.loads((pathlib.Path(__file__).parent / "data" / "fpga-verified-welland-2026-10-04.json").read_text())


def pi(hostname, detail=None, seen_ago=0, verifying=False, serial=None):
    """A registered Pi, seen `seen_ago` minutes ago, with an fpga-verified event of `detail` in its boot."""
    m = Machine.objects.create(serial=serial or hostname, site="welland", hostname=hostname, online=True,
                               last_seen=timezone.now() - datetime.timedelta(minutes=seen_ago), last_boot_id="b1")
    if verifying:
        BootEvent.objects.create(machine=m, boot_id="b1", stage="fpga-verifying", ts=timezone.now(), detail={})
    if detail is not None:
        BootEvent.objects.create(machine=m, boot_id="b1", stage="fpga-verified", ts=timezone.now(), detail=detail)
    return m


@pytest.fixture(autouse=True)
def site_address(settings):
    """The site's own address, as the infra site role always writes it (DOMAIN_NAME): the /fpgas/ pages' host."""
    settings.DOMAIN_NAME = "welland.fpgas.online"


@pytest.fixture
def fleet():
    pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    pi("pi-sw2-p47", REAL["pi-sw2-p47"])
    pi("pi-sw2-p33", REAL["pi-sw2-p33"])
    pi("pi-sw2-p7", verifying=True)  # its check is running
    pi("pi-sw2-p4", REAL["pi-sw2-p46"], seen_ago=60 * 7, serial="10000000f536c51f")  # gone for 7 hours
    pi("pi-sw2-p10", REAL["pi-sw2-p47"], seen_ago=30, serial="10000000f77b8415")  # stopped checking in 30 min ago


@pytest.mark.parametrize("state, checked_in, minutes, want", [
    ("pass", True, 0, OPERATIONAL),
    ("fail", True, 0, ATTENTION), ("error", True, 0, ATTENTION), ("unknown", True, 0, ATTENTION),
    ("", True, 0, UNKNOWN), ("verifying", True, 0, UNKNOWN),
    ("pass", False, 30, ATTENTION),  # passed, but stopped checking in: not offered, and should be
    ("pass", False, 60 * 7, MISSING), ("", False, 60 * 7, MISSING),
])
def test_one_rule_for_how_a_pi_is_doing(state, checked_in, minutes, want):
    now = timezone.now()
    assert condition(state, checked_in, now - datetime.timedelta(minutes=minutes), now) == want


@pytest.mark.django_db
def test_every_registered_pi_is_counted_once_and_the_summary_adds_up(fleet):
    rows = fpga.hosts()
    assert len(rows) == Machine.objects.count() == 6
    table, total, everything = fpga.summary(rows)
    assert everything == 6 and sum(n for _, _, n in table) == 6
    by = {t: counts for t, counts, _ in table}
    assert by["Acorn"] == {OPERATIONAL: 2, ATTENTION: 1, MISSING: 1, UNKNOWN: 0}
    assert total == {OPERATIONAL: 2, ATTENTION: 2, MISSING: 1, UNKNOWN: 1}
    assert {r.machine.hostname: r.condition for r in rows} == {
        "pi-sw2-p46": OPERATIONAL, "pi-sw2-p47": OPERATIONAL, "pi-sw2-p33": ATTENTION, "pi-sw2-p7": UNKNOWN,
        "pi-sw2-p4": MISSING, "pi-sw2-p10": ATTENTION}


@pytest.mark.django_db
def test_a_failed_check_shows_the_failing_tests_by_name_and_its_reason(fleet):
    row = next(r for r in fpga.hosts() if r.machine.hostname == "pi-sw2-p33")
    assert row.state == "fail" and row.board_type.startswith("TT")
    assert row.boards[0]["failing"] == ["pin-id", "spiflash"]
    assert row.why == "its boot check failed: pin-id fail: the test exited 1; spiflash fail: the test exited 1"
    html = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/", follow=True).content.decode()
    assert "failed: pin-id, spiflash" in html and "pin-id fail: the test exited 1" in html


@pytest.mark.django_db
@pytest.mark.parametrize("host", ["welland.fpgas.online", "ps1.fpgas.online", "tinytapeout.fpgas.online"])
def test_the_page_answers_without_a_login_on_every_host(fleet, host):
    r = Client(HTTP_HOST=host).get("/management/fpgas/", follow=True)
    assert r.status_code == 200
    html = r.content.decode()
    assert "<title>Raspberry Pis &mdash; Management &mdash; fpgas.online</title>" in html
    assert "<h1>Raspberry Pis</h1>" in html and ">sw2 p46</td>" in html
    assert '<meta http-equiv="refresh" content="60">' in html


@pytest.mark.django_db
def test_each_count_links_to_the_pis_it_counts(fleet):
    c = Client(HTTP_HOST="welland.fpgas.online")
    html = c.get("/management/fpgas/", follow=True).content.decode()
    assert 'href="?type=Acorn&amp;condition=operational">2</a>' in html
    only = c.get("/management/fpgas/?type=Acorn&condition=operational", follow=True).content.decode()
    assert "pi-sw2-p46" in only and "pi-sw2-p47" in only and "pi-sw2-p33" not in only and "show all" in only
    missing = c.get("/management/fpgas/?condition=missing", follow=True).content.decode()
    assert "pi-sw2-p4" in missing and "it has stopped checking in" in missing and "pi-sw2-p46" not in missing


@pytest.mark.django_db
def test_text_a_pi_sent_is_escaped_and_cut_short():
    detail = dict(REAL["pi-sw2-p33"], board0_reason="<script>alert(1)</script>" + "x" * 1000)
    pi("pi-sw2-p33", detail)
    html = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/", follow=True).content.decode()
    assert "<script>alert(1)" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "x" * (fpga.MAX_TEXT + 1) not in html


@pytest.mark.django_db
def test_a_pi_with_no_report_is_shown_by_what_its_registration_says(fleet):
    row = next(r for r in fpga.hosts() if r.machine.hostname == "pi-sw2-p7")
    assert row.boards == [] and row.board_type == fpga.NO_BOARD and row.why == "its boot check is running"


@pytest.mark.django_db
def test_no_pi_registered_says_so():
    html = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/", follow=True).content.decode()
    assert "No Raspberry Pi has registered." in html


@pytest.mark.django_db
def test_the_fleet_pages_are_linked_only_where_the_host_has_them(fleet):
    welland = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/", follow=True).content.decode()
    assert '<a href="/fleet/pi-sw2-p46/">pi-sw2-p46 (no model registered)</a>' in welland
    tt = Client(HTTP_HOST="tinytapeout.fpgas.online").get("/management/fpgas/", follow=True).content.decode()
    assert "pi-sw2-p46" in tt and 'href="/fleet/' not in tt


@pytest.mark.django_db
def test_times_are_the_installations_own_with_the_zone_named_once(fleet, settings):
    """Tim: clock times are local, never UTC. welland's gateway runs in Australia/Adelaide."""
    settings.MANAGEMENT_TIME_ZONE = "Australia/Adelaide"
    m = Machine.objects.get(hostname="pi-sw2-p46")
    m.last_seen = datetime.datetime(2026, 10, 9, 0, 15, tzinfo=datetime.timezone.utc)  # 10:45 in Adelaide
    m.save()
    html = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/", follow=True).content.decode()
    # "x ago" in the cell, the exact local time in its title (#91)
    assert 'title="2026-10-09 10:45:00 ACDT"' in html  # UTC only in the <time datetime> a machine reads
    assert "Times are Australia/Adelaide." in html and "UTC" not in html
    from django.utils import timezone as tz

    assert tz.get_current_timezone_name() == settings.TIME_ZONE  # nothing left behind for the next request


def test_the_zone_is_the_host_s_own_when_the_settings_name_none(settings, tmp_path):
    from management import localtime

    settings.MANAGEMENT_TIME_ZONE = ""
    link = tmp_path / "localtime"
    link.symlink_to("/usr/share/zoneinfo/America/Chicago")
    assert localtime._host_zone(str(link)) == "America/Chicago"
    assert localtime._host_zone(str(tmp_path / "none")) == ""
    assert localtime.zone_name() in (localtime._host_zone(), settings.TIME_ZONE)
    settings.MANAGEMENT_TIME_ZONE = "Not/AZone"
    from django.core.exceptions import ImproperlyConfigured

    with pytest.raises(ImproperlyConfigured):
        localtime.zone_name()


@pytest.mark.django_db
def test_uptime_reads_as_hours_and_minutes(fleet):
    m = Machine.objects.get(hostname="pi-sw2-p46")
    for seconds, text in ((3600, "1 h 0 min"), (720, "12 min"), (3 * 86400 + 4 * 3600 + 5, "3 d 4 h"), (0, "")):
        m.last_uptime_s = seconds
        m.save()
        assert next(r for r in fpga.hosts() if r.machine.pk == m.pk).uptime == text


@pytest.mark.django_db
def test_rows_are_in_switch_and_port_order_as_numbers(fleet):
    pi("pi-sw1-p12", REAL["pi-sw2-p46"], serial="00000000e2eb5dbf")
    pi("noname-host", REAL["pi-sw2-p46"], serial="0000000000000001")
    assert [r.machine.hostname for r in fpga.hosts()] == [
        "pi-sw1-p12", "pi-sw2-p4", "pi-sw2-p7", "pi-sw2-p10", "pi-sw2-p33", "pi-sw2-p46", "pi-sw2-p47",
        "noname-host"]


@pytest.mark.django_db
def test_board_names_a_pi_sends_are_cut_and_unknown_ones_share_one_row():
    """Review 1 of #74: a Pi's board names make the summary's rows, so one cannot make a row per name it sends."""
    for i in range(5):
        pi(f"pi-sw2-p{20 + i}", {"result": "pass", "board0": f"forged{i}{'y' * 400} - pass"})
    rows = fpga.hosts()
    table, _total, _n = fpga.summary(rows)
    assert [t for t, _, _ in table] == [fpga.OTHER] and all(len(r.board_type) < 50 for r in rows)


@pytest.mark.django_db
def test_failing_tests_are_read_by_the_checks_own_board_number():
    """board0 and board2, no board1: the second board's tests are board2's."""
    detail = {"result": "fail", "board0": "acorn cle-215+ pass", "board0_tests": "jtag=pass",
              "board2": "arty a7-35t fail", "board2_tests": "jtag=fail ddr=pass", "board2_reason": "jtag fail: no idcode"}
    pi("pi-sw2-p40", detail)
    row = next(r for r in fpga.hosts() if r.machine.hostname == "pi-sw2-p40")
    assert [b["failing"] for b in row.boards] == [[], ["jtag"]]
    assert row.board_type == "Acorn + Arty A7" and row.why == "its boot check failed: jtag fail: no idcode"


@pytest.mark.django_db
def test_a_pi_with_no_report_this_boot_is_typed_by_its_registration():
    from fleet.models import HardwareSnapshot

    m = pi("pi-sw2-p33", verifying=True)
    snap = HardwareSnapshot.objects.create(machine=m, fingerprint="f", document={
        "fpga": {"boards": [{"kind": "tt-demo-board"}, {"kind": "<b>" + "z" * 300}]}})
    m.latest_snapshot = snap
    m.save()
    row = next(r for r in fpga.hosts() if r.machine.pk == m.pk)
    assert row.boards == [] and row.board_type == f"{fpga.SNAPSHOT_TT} + {fpga.OTHER}"
    assert row.registered is not None
    html = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/", follow=True).content.decode()
    assert "the port its host name says it booted on, as it registered" in html and "<b>" not in html
    # its board as the registration names it, escaped and cut
    assert "tt-demo-board + &lt;b&gt;zzz" in html


@pytest.mark.django_db
def test_the_page_can_stop_reloading_and_ignores_a_filter_it_has_not(fleet):
    c = Client(HTTP_HOST="welland.fpgas.online")
    assert '<meta http-equiv="refresh"' not in c.get("/management/fpgas/?reload=off", follow=True).content.decode()
    odd = c.get("/management/fpgas/?condition=bogus", follow=True).content.decode()
    assert "pi-sw2-p46" in odd and "show all" not in odd


@pytest.mark.django_db
def test_an_offered_pi_links_to_its_visitor_page_where_the_host_has_one(fleet):
    welland = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/", follow=True).content.decode()
    assert '<a href="/fpgas/pi-sw2-p46.html">yes</a>' in welland
    # tinytapeout.fpgas.online has no /fpgas/ pages: its Acorns are offered at the site's own address
    tt = Client(HTTP_HOST="tinytapeout.fpgas.online").get("/management/fpgas/", follow=True).content.decode()
    assert '<a href="https://welland.fpgas.online/fpgas/pi-sw2-p46.html">yes</a>' in tt


@pytest.mark.django_db
def test_its_page_is_linked_only_for_a_pi_the_fpgas_pages_list(fleet):
    """Review 2 of #74: a TT chip board's Pi is offered but not listed on /fpgas/ (that page is not its own)."""
    from pibfpgas.pis import listed

    pi("pi-sw2-p20", {"result": "pass", "board0": "tt tt-asic pass", "board0_identity_chip": "asic"})
    assert "pi-sw2-p20" not in {p.hostname for p in listed()}
    html = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/", follow=True).content.decode()
    assert "/fpgas/pi-sw2-p20.html" not in html
    for hostname in {p.hostname for p in listed()}:
        assert f'<a href="/fpgas/{hostname}.html">yes</a>' in html


@pytest.mark.django_db
@pytest.mark.parametrize("document", [
    {"fpga": {"boards": [{"kind": ["x"]}]}}, {"fpga": "x"}, {"fpga": {"boards": 5}}, {"kind": {"a": 1}},
    {"fpga": {"boards": [{"kind": {"a": 1}}, "x", None]}}, [], "x",
])
def test_a_registration_that_does_not_fit_does_not_break_the_page(document):
    from fleet.models import HardwareSnapshot

    m = pi("pi-sw2-p33", verifying=True)
    m.latest_snapshot = HardwareSnapshot.objects.create(machine=m, fingerprint="f", document=document)
    m.save()
    r = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/", follow=True)
    assert r.status_code == 200 and next(h for h in fpga.hosts() if h.machine.pk == m.pk).board_type == fpga.NO_BOARD



# --- one Pi dashboard (#94) ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_management_fpgas_sends_a_browser_to_fleet_where_the_host_has_it_keeping_the_filter():
    r = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/?type=Acorn&condition=missing")
    assert r.status_code == 302 and r["Location"] == "/fleet/?type=Acorn&condition=missing"
    assert Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/")["Location"] == "/fleet/"
    # tinytapeout.fpgas.online has no fleet pages: the dashboard is its /management/fpgas/
    tt = Client(HTTP_HOST="tinytapeout.fpgas.online").get("/management/fpgas/")
    assert tt.status_code == 200 and "<h1>Raspberry Pis</h1>" in tt.content.decode()


def _rows(html):
    """The Pi table's rows as [[cell text, ...]]."""
    import html as htmllib
    import re

    body = html.split('class="mgmt-table mgmt-hosts', 1)[1].split("</table>", 1)[0]
    rows = [[htmllib.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            for tr in re.findall(r"<tr>(.*?)</tr>", body.split("<tbody>", 1)[1], re.S)]
    assert rows and all(len(r) == 7 for r in rows)
    return rows


@pytest.mark.django_db
def test_each_pi_is_one_row_of_values_state_pi_board_check_seen_offered(fleet):
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    rows = _rows(html)
    by_port = {r[2]: r for r in rows}
    # columns: State, Pi (its label), Port, Board (its label), Boot check, Seen, Offered
    assert by_port["sw2 p33"][0] == "needs attention" and by_port["sw2 p33"][4] == "failed: pin-id, spiflash"
    assert by_port["sw2 p46"][0] == "operational" and by_port["sw2 p46"][4] == "passed"
    assert by_port["sw2 p46"][1] == "pi-sw2-p46 (no model registered)" and by_port["sw2 p46"][3].startswith("acorn-")
    assert by_port["sw2 p46"][6] == "yes"
    # why a Pi is not offered, in the index's words
    assert by_port["sw2 p33"][6] == "no: pin-id, spiflash" and by_port["sw2 p10"][6] == "no: stopped checking in"
    assert by_port["sw2 p4"][6] == "no: missing 7 h" and by_port["sw2 p7"][6] == "no: boot check running"
    assert by_port["sw2 p7"][4] == "running" and by_port["sw2 p7"][0] == "checking"
    assert by_port["sw2 p4"][0] == "missing" and by_port["sw2 p4"][5] == "7 h ago"
    assert by_port["sw2 p33"][3] == "TT board, not identified 4df39a7a6856f86f"  # the USB serial its check read
    # what needs a person first, then by switch and port
    assert [r[0] for r in rows] == ["needs attention", "needs attention", "missing", "checking", "operational",
                                    "operational"]
    assert [r[2] for r in rows][:2] == ["sw2 p10", "sw2 p33"]
    # every test and its result in the check cell's title; the reason the Pi is not offered in the state's title
    import re

    assert re.search(r'<td title="[^"]*pin-id=fail[^"]*spiflash=fail[^"]*">failed: pin-id, spiflash</td>', html)
    assert 'title="its boot check failed: pin-id fail: the test exited 1' in html
    assert 'class="mgmt-table mgmt-hosts mgmt-striped mgmt-sortable"' in html


@pytest.mark.django_db
def test_a_pi_seen_in_the_future_fails_its_own_cell_and_the_page_stands(fleet):
    Machine.objects.filter(hostname="pi-sw2-p46").update(last_seen=timezone.now() + datetime.timedelta(hours=2))
    r = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/")
    assert r.status_code == 200
    rows = {row[2]: row for row in _rows(r.content.decode())}
    assert "future" in rows["sw2 p46"][5] and rows["sw2 p47"][5].endswith("ago")


@pytest.mark.django_db
def test_the_explanations_are_one_collapsed_key_after_the_tables(fleet):
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    body = html.split("<h1>Raspberry Pis</h1>", 1)[1]
    assert '<details class="mgmt-key-box"><summary>Key</summary>' in body
    assert "<details open" not in body
    # no paragraph of prose between the heading and the tables
    before_key = body.split("<details", 1)[0]
    assert "operational</strong>" not in before_key and "<p>" not in before_key


@pytest.mark.django_db
def test_a_pi_is_named_by_its_label_from_its_registered_revision():
    from fleet.models import HardwareSnapshot

    m = pi("pi-sw1-p3", serial="285df3f84af242d0")
    m.latest_snapshot = HardwareSnapshot.objects.create(machine=m, fingerprint="f", document={
        "machine": {"serial": m.serial, "revision_code": "c04170"}})  # a Pi 5, 4 GB
    m.save()
    assert fpga.pi_label(m) == "Pi 5 4 GB 285df3f84af242d0"
    m.latest_snapshot.document = {"machine": {"revision_code": "zz"}}  # one rpi-hwid cannot read: said
    assert fpga.pi_label(m) == "285df3f84af242d0 (revision 'zz' not readable)"
    m.latest_snapshot.document = {"machine": {}}  # none registered: the serial alone
    assert fpga.pi_label(m) == "285df3f84af242d0 (no model registered)"


def test_a_board_is_named_by_its_label_where_its_check_read_one():
    from rpi_hwid import names

    def board(name, **identity):
        return {"board": name, "variant": "", "result": "pass", "reason": "", "identity": identity}

    dna = "0x00200c8664b04854"
    assert fpga.board_label(board("acorn", dna=dna)) == f"{names.acorn_name(dna)} {dna}"
    assert fpga.board_label(board("netv2", dna=dna)) == f"{names.netv2_name(dna)} {dna}"
    assert fpga.board_label(board("tt", chip="fpga", usb_serial="4df39a7a6856f86f")) == "TT FPGA 4df39a7a6856f86f"
    assert fpga.board_label(board("tt")) == "TT board, not identified"
    assert fpga.board_label(board("arty", serial="210319A8C1F3")) == "Arty A7 210319A8C1F3"
    # a DNA rpi-hwid cannot read: its type and what the check sent
    assert fpga.board_label(board("acorn", dna="not-a-dna")) == "Acorn not-a-dna"


@pytest.mark.django_db
def test_every_yes_links_a_visitor_page_and_an_offered_pi_without_one_says_so(fleet):
    # a TT chip board: offered, and not on /fpgas/ (that page is not its own): its page is tinytapeout's
    pi("pi-sw2-p20", {"result": "pass", "board0": "tt tt-asic pass", "board0_identity_chip": "asic",
                      "board0_identity_usb_serial": "abcd1234"})
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    rows = {r[2]: r for r in _rows(html)}
    assert rows["sw2 p20"][6] == "yes"
    assert '<a href="https://tinytapeout.fpgas.online/board/' in html and 'abcd1234/">yes</a>' in html
    tt = Client(HTTP_HOST="tinytapeout.fpgas.online").get("/management/fpgas/").content.decode()
    assert 'abcd1234/">yes</a>' in tt and 'href="https://tinytapeout.fpgas.online' not in tt  # its own host: a path
    # an offered Pi that no visitor page shows
    from unittest import mock

    with mock.patch("ttsite.boards.pages", return_value=[]):
        html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    assert {r[2]: r for r in _rows(html)}["sw2 p20"][6] == "no: no visitor page"
    for row in _rows(html):
        assert row[6] in ("yes",) or row[6].startswith("no: ")


@pytest.mark.django_db
def test_the_reload_switch_keeps_the_filter_and_the_redirect_keeps_every_parameter(fleet):
    c = Client(HTTP_HOST="welland.fpgas.online")
    html = c.get("/fleet/?type=TT+board%2C+not+identified&condition=attention").content.decode()
    assert 'href="?type=TT+board%2C+not+identified&amp;condition=attention&amp;reload=off">stop</a>' in html
    off = c.get("/fleet/?condition=attention&reload=off").content.decode()
    assert 'href="?condition=attention">reload every minute</a>' in off
    r = c.get("/management/fpgas/?type=TT+board%2C+not+identified&condition=attention&reload=off")
    assert r["Location"] == "/fleet/?type=TT+board%2C+not+identified&condition=attention&reload=off"


@pytest.mark.django_db
@pytest.mark.parametrize("machine_doc, label", [
    ({"model": "Xunlong Orange Pi PC", "mem_total_kb": 1015000}, "Orange Pi PC 1 GB S1"),  # rpi-hwid's identify()
    ({"model": "Xunlong Orange Pi PC"}, "Orange Pi PC S1"),  # no memory registered: none said
    ({"revision_code": "a020d3"}, "Pi 3 Model B+ 1 GB S1"),  # the revision code: the label header's "Pi <model>"
    ({"revision_code": "zz"}, "S1 (revision 'zz' not readable)"),
    ({"revision_code": 7}, "S1 (revision '7' not readable)"),
    ({"model": "Raspberry Pi 4 Model B Rev 1.4"}, "S1 (no revision code registered)"),
    ({"model": "Some Router"}, "S1 (model 'Some Router' not known to rpi-hwid)"),
    ({"model": "   "}, "S1 (model '   ' not readable)"),
    ({"model": 42}, "S1 (model '42' not readable)"),
    ({}, "S1 (no model registered)"),
])
def test_a_host_is_named_by_rpi_hwids_rules_or_says_why_not(machine_doc, label):
    """#92: one form for a label and one for why there is none: "<serial> (<why>)"."""
    from fleet.models import HardwareSnapshot

    m = pi("pi-sw2-p21", serial="S1")
    m.latest_snapshot = HardwareSnapshot.objects.create(machine=m, fingerprint="d", document={"machine": machine_doc})
    m.save()
    assert fpga.pi_label(m) == label


@pytest.mark.django_db
def test_a_host_with_no_revision_code_is_named_by_its_registered_model():
    """#92: an Orange Pi has no Pi revision code; it is not a "Pi", it is its model."""
    from fleet.models import HardwareSnapshot

    m = pi("pi-sw2-p20", serial="02c0008164c2a1b0")
    m.latest_snapshot = HardwareSnapshot.objects.create(machine=m, fingerprint="o", document={
        "machine": {"serial": m.serial, "model": "Xunlong Orange Pi PC"}})
    m.save()
    assert fpga.pi_label(m) == "Orange Pi PC 02c0008164c2a1b0"
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    assert '<a href="/fleet/02c0008164c2a1b0/">Orange Pi PC 02c0008164c2a1b0</a>' in html
    assert 'title="model Xunlong Orange Pi PC; host name pi-sw2-p20' in html  # the registered model in full


# --- /fleet/registry.json: the hosts for programs (the e2e suite, probes) ------------------------------------------


@pytest.mark.django_db
def test_the_registry_json_lists_the_same_hosts_as_the_fleet_table(fleet, settings):
    import re

    settings.MANAGEMENT_SITE_NAME = "welland"
    c = Client(HTTP_HOST="welland.fpgas.online")
    data = c.get("/fleet/registry.json").json()
    table = set(re.findall(r'<a href="/fleet/([^/"]+)/">', c.get("/fleet/").content.decode()))
    assert {m["serial"] for m in data["machines"]} == table and len(table) == 6
    assert data["site"] == "welland" and data["generated_at"].endswith("+00:00")


@pytest.mark.django_db
def test_a_registry_entry_carries_what_a_program_needs(fleet, settings):
    from fleet.models import HardwareSnapshot

    settings.MANAGEMENT_SITE_NAME = "welland"
    m = Machine.objects.get(hostname="pi-sw2-p46")
    m.latest_snapshot = HardwareSnapshot.objects.create(machine=m, fingerprint="c", document={
        "machine": {"serial": m.serial, "revision_code": "c04170"}, "peripherals": {"cameras": ["imx219"]}})
    m.save()
    data = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/registry.json").json()
    by_host = {e["hostname"]: e for e in data["machines"]}
    p46 = by_host["pi-sw2-p46"]
    assert (p46["label"], p46["port"], p46["condition"], p46["offered"]) == (
        "Pi 5 4 GB pi-sw2-p46", "sw2 p46", "operational", True)
    assert p46["check"]["result"] == "pass" and p46["check"]["at"]
    assert p46["boards"][0]["label"].startswith("acorn-") and p46["boards"][0]["id"]
    assert p46["cameras"] == ["imx219"]
    # what the site does not know is null: no cameras said, no check started
    assert by_host["pi-sw2-p47"]["cameras"] is None
    assert by_host["pi-sw2-p7"]["check"]["result"] == "verifying"
    # the condition as a key, not the page's title; the port null where the host name names none
    assert {e["condition"] for e in data["machines"]} <= {"operational", "attention", "missing", "checking"}
    assert by_host["pi-sw2-p7"]["condition"] == "checking" and by_host["pi-sw2-p33"]["condition"] == "attention"
    p33 = by_host["pi-sw2-p33"]
    assert p33["offered"] is False and p33["boards"][0]["tests"]["pin-id"] == "fail"


@pytest.mark.django_db
def test_a_host_whose_name_names_no_port_has_a_null_port(settings):
    settings.MANAGEMENT_SITE_NAME = "welland"
    pi("lab-bench-pi", REAL["pi-sw2-p46"], serial="10000000dddd0001")
    entry = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/registry.json").json()["machines"][0]
    assert entry["serial"] == "10000000dddd0001" and entry["port"] is None


@pytest.mark.django_db
def test_the_registry_json_answers_without_a_login_and_is_not_on_tinytapeout():
    assert Client(HTTP_HOST="welland.fpgas.online").get("/fleet/registry.json").status_code == 200
    assert Client(HTTP_HOST="tinytapeout.fpgas.online").get("/fleet/registry.json").status_code == 404
