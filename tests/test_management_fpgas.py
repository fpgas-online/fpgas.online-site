"""The FPGA dashboard (#68): every registered Pi with its port, board, boot check and check-in, and a summary by
board type, from the fleet registry, by the one rule that offers a Pi (fleet.services.is_offered)."""

import datetime
import json
import pathlib
import re

import pytest
from django.test import Client
from django.utils import timezone
from fleet.models import BootEvent, Machine
from fleet.services import ATTENTION, MISSING, OPERATIONAL, UNKNOWN, condition

from management import fpga

# Three Pis' fpga-verified events as welland's fleet registry received them on 4 October 2026: two Acorns that
# passed, and a TT FPGA demo board that failed its pin-id and spiflash tests.
REAL = json.loads((pathlib.Path(__file__).parent / "data" / "fpga-verified-welland-2026-10-04.json").read_text())


def pi(hostname, detail=None, seen_ago=0, verifying=False, serial=None, lease=True):
    """A registered Pi, seen `seen_ago` minutes ago, with an fpga-verified event of `detail` in its boot; with
    `lease`, the gateway's lease record of it on the port its host name names, at the same time (a live Pi renews its
    lease, so the gateway has one)."""
    from fleet.models import GatewayRecord
    from pibfpgas.pis import Pi

    seen = timezone.now() - datetime.timedelta(minutes=seen_ago)
    m = Machine.objects.create(serial=serial or hostname, site="welland", hostname=hostname, online=True,
                               last_seen=seen, last_boot_id="b1")
    place = Pi.from_hostname(hostname)
    if lease and place is not None and place.switch:
        GatewayRecord.objects.create(site="welland", kind="lease", switch=place.switch, port=place.port,
                                     hostname=hostname, at=seen, doc={}, machine=m)
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
    assert row.boards[0]["failing"] == ["wiring", "flash"]  # the old names (pin-id, spiflash) as the new ones
    assert row.why == "its boot check failed: pin-id fail: the test exited 1; spiflash fail: the test exited 1"
    html = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/", follow=True).content.decode()
    # every check is a column of the one Pi table; a failed one is ✗ in its own column, its reason the ✗'s title
    # (Tim, 15 Oct 2026: "Make the error message be the popup when mousing over the x") and the ✗ links the Pi's page
    failing = _by_serial(html)["pi-sw2-p33"]
    assert (failing["wiring"], failing["flash"], failing["uart"]) == ("✗", "✗", "✓")
    assert ('data-label="wiring" title="pin-id fail: the test exited 1"><div class="mgmt-test-fail">'
            '<a href="/fleet/pi-sw2-p33/#boards">✗</a>') in html
    assert 'title="spiflash fail: the test exited 1"' in html
    assert "Reason" not in _header(html)


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
    assert "10000000f536c51f" in missing and "it has stopped checking in" in missing and "pi-sw2-p46" not in missing


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
    assert '<a href="/fleet/pi-sw2-p46/">pi-sw2-p46</a>' in welland
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

    m = pi("pi-sw2-p33", verifying=True, lease=False)
    snap = HardwareSnapshot.objects.create(machine=m, fingerprint="f", document={
        "fpga": {"boards": [{"kind": "tt-demo-board"}, {"kind": "<b>" + "z" * 300}]}})
    m.latest_snapshot = snap
    m.save()
    row = next(r for r in fpga.hosts() if r.machine.pk == m.pk)
    assert row.boards == [] and row.board_type == f"{fpga.SNAPSHOT_TT} + {fpga.OTHER}"
    assert row.registered is not None
    html = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/", follow=True).content.decode()
    assert "no lease seen; named sw2 p" in html and "<b>" not in html
    # its board as the registration names it, escaped and cut
    assert "<div>tt-demo-board</div><div>&lt;b&gt;zzz" in html


@pytest.mark.django_db
def test_the_page_can_stop_reloading_and_ignores_a_filter_it_has_not(fleet):
    c = Client(HTTP_HOST="welland.fpgas.online")
    assert '<meta http-equiv="refresh"' not in c.get("/management/fpgas/?reload=off", follow=True).content.decode()
    odd = c.get("/management/fpgas/?condition=bogus", follow=True).content.decode()
    assert "pi-sw2-p46" in odd and "show all" not in odd


@pytest.mark.django_db
def test_an_offered_pi_links_to_its_visitor_page_where_the_host_has_one(fleet):
    welland = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/", follow=True).content.decode()
    assert '<a class="mgmt-test-pass" href="/fpgas/pi-sw2-p46.html">✓</a>' in welland
    # tinytapeout.fpgas.online has no /fpgas/ pages: its Acorns are offered at the site's own address
    tt = Client(HTTP_HOST="tinytapeout.fpgas.online").get("/management/fpgas/", follow=True).content.decode()
    assert '<a class="mgmt-test-pass" href="https://welland.fpgas.online/fpgas/pi-sw2-p46.html">✓</a>' in tt


@pytest.mark.django_db
def test_its_page_is_linked_only_for_a_pi_the_fpgas_pages_list(fleet):
    """Review 2 of #74: a TT chip board's Pi is offered but not listed on /fpgas/ (that page is not its own)."""
    from pibfpgas.pis import listed

    pi("pi-sw2-p20", {"result": "pass", "board0": "tt tt-asic pass", "board0_identity_chip": "asic"})
    assert "pi-sw2-p20" not in {p.hostname for p in listed()}
    html = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/", follow=True).content.decode()
    assert "/fpgas/pi-sw2-p20.html" not in html
    for hostname in {p.hostname for p in listed()}:
        assert f'<a class="mgmt-test-pass" href="/fpgas/{hostname}.html">✓</a>' in html


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


def _cells(table_html):
    """[[cell text, ...]] of a table's body rows (tags removed)."""
    import html as htmllib
    import re

    body = table_html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    return [[htmllib.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            for tr in re.findall(r"<tr>(.*?)</tr>", body, re.S)]


# the checks follow Seen, a column each; Notes is last (the checks are what a reader scans for, #123)
# the fixture's checks (two Acorns and a TT board of 4 Oct) in their groups, each group in dependency order (#127)
EXPECTED_CHECKS = ["jtag", "wiring", "flash", "ddr", "uart", "p2-uart", "p2-serial", "p2-gpio", "pcie-link",
                   "pcie-bar0", "rp1-pio", "scratch", "sdk"]  # sdk: what a TT board not identified gets (#129)
# #129: Boot check and Check ran beside the checks they belong to; Offered, Up and Seen before them
FIXED = ["State", "Model", "RAM", "Serial", "Port", "Board", "ID", "Offered", "Up", "Seen", "Boot check", "Check ran"]


def _hosts_table(html, n=0):
    """The n-th Pi table: 0 the present Pis, 1 the missing ones (#127)."""
    return html.split('class="mgmt-table mgmt-hosts')[n + 1].split("</table>", 1)[0]


def _tables(html):
    return html.count('class="mgmt-table mgmt-hosts')


def _header(html):
    """The Pi table's column names, in order: the head's last row (a row above it names the checks' groups)."""
    head = _hosts_table(html).split("</thead>", 1)[0]
    last = re.findall(r"<tr[^>]*>(.*?)</tr>", head, re.S)[-1]
    return [re.sub(r"<[^>]+>", "", th).strip() for th in re.findall(r"<th[^>]*>(.*?)</th>", last, re.S)]


def _groups(html):
    """[(group, its number of columns)] of the Pi table's group row, or [] when it has none."""
    head = _hosts_table(html).split("</thead>", 1)[0]
    return [(name, int(n)) for n, name in re.findall(r'<th scope="colgroup" colspan="(\d+)" class="mgmt-group">(.*?)</th>',
                                                       head)]


def _rows(html, n=None):
    """The Pi tables' rows as [{column: cell text}] (both tables, or the n-th), a cell's text without the words only a
    screen reader hears, its lines (a label or a mark per board) told apart by a newline."""
    import html as htmllib

    if n is None:
        return [row for i in range(_tables(html)) for row in _rows(html, i)]
    header = _header(html)
    cleaned = re.sub(r'<span class="mgmt-sr">.*?</span>', "", _hosts_table(html, n), flags=re.S)
    cleaned = cleaned.replace("</div><div", "</div>\n<div")
    rows = _cells(cleaned)
    assert rows and all(len(r) == len(header) for r in rows)
    return [dict(zip(header, (htmllib.unescape(c) for c in r))) for r in rows]


def _by_serial(html):
    return {r["Serial"]: r for r in _rows(html)}


def _by_port(html):
    return {r["Port"]: r for r in _rows(html)}


@pytest.mark.django_db
def test_each_pi_is_one_row_of_one_value_per_cell(fleet):
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    header = _header(html)
    # a value per column (Tim, 15 Oct 2026): the label's model, memory and serial apart, the checks between
    assert header[:len(FIXED)] == FIXED and header[-1] == "Notes"  # the checks between, a column each
    present, missing = _rows(html, 0), _rows(html, 1)
    by_port = _by_port(html)
    # the Boot check is one character like every check (#129: "Boot check is still random text"), its words on hover
    assert by_port["sw2 p33"]["Boot check"] == "✗"
    assert by_port["sw2 p46"]["Boot check"] == "✓" and by_port["sw2 p7"]["Boot check"] == "⟳"
    assert re.search(r'data-label="Boot check" data-sort="\d" title="failed, [^"]*: its boot check failed: pin-id fail',
                     html)
    # State is one character (#127), the word in its title and for a screen reader
    assert (by_port["sw2 p46"]["State"], by_port["sw2 p33"]["State"], by_port["sw2 p7"]["State"]) == ("●", "!", "⟳")
    assert re.search(r'<td class="mgmt-c-state mgmt-state-unknown" data-sort="\d" data-label="State" '
                     r'title="checking: its boot check is running">⟳<span class="mgmt-sr"> checking</span></td>', html)
    for row in present + missing:
        assert row["Boot check"] in ("✓", "✗", "⟳", "⊘")
        assert row["State"] in ("●", "!", "⟳", "○") and row["Offered"] in ("✓", "✗")
        assert "(" not in row["Serial"] and " " not in row["Serial"]  # the serial alone, never a sentence beside it
    # Board is the type only (#127); the id on its label is the ID, the board's name its title
    assert by_port["sw2 p46"]["Serial"] == "pi-sw2-p46" and by_port["sw2 p46"]["Board"] == "Acorn"
    assert '<div title="Acorn CLE-215+">Acorn</div>' in html  # the variant on hover
    assert '<a href="/fleet/pi-sw2-p46/">pi-sw2-p46</a>' in html  # the serial links the Pi's page
    assert (by_port["sw2 p33"]["Board"], by_port["sw2 p33"]["ID"]) == ("TT board, not identified", "4df39a7a6856f86f")
    assert by_port["sw2 p46"]["ID"] == "0x0054b48664b04854"
    assert re.search(r'<div title="acorn-\w+ 0x0054b48664b04854">0x0054b48664b04854</div>', html)
    # Offered is one mark, the reason on hover (#127: "Offered should be a tick with the 'why not' being a popup")
    assert "Why not" not in header
    assert by_port["sw2 p46"]["Offered"] == "✓" and by_port["sw2 p33"]["Offered"] == "✗"
    assert 'title="not offered: stopped checking in"' in html and 'title="not offered: wiring, flash"' in html
    # Seen is the time only
    assert by_port["sw2 p4"]["Seen"] == "7 h ago" and by_port["sw2 p46"]["Seen"].endswith("ago")
    # these Pis registered no model: Model and RAM are empty, Notes says why in a short value
    assert (by_port["sw2 p46"]["Model"], by_port["sw2 p46"]["RAM"]) == ("", "")
    assert by_port["sw2 p46"]["Notes"] == "no model registered"
    # the Pis that are here, what needs a person first; the missing ones in a table of their own below (#127)
    assert [r["State"] for r in present] == ["!", "!", "⟳", "●", "●"]
    assert [r["Port"] for r in present][:2] == ["sw2 p10", "sw2 p33"]
    assert [(r["State"], r["Serial"]) for r in missing] == [("○", "10000000f536c51f")]
    assert html.index("<h2>Missing</h2>") > html.index('aria-label="Raspberry Pis"')
    # as wide as its values (#127: no slack in State/Model/RAM/Board at 2560 px)
    assert 'class="mgmt-table mgmt-hosts mgmt-fit mgmt-striped mgmt-sortable"' in html


@pytest.mark.django_db
def test_without_a_missing_pi_there_is_no_missing_table(fleet):
    Machine.objects.filter(serial="10000000f536c51f").delete()
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    assert _tables(html) == 1 and "<h2>Missing</h2>" not in html


@pytest.mark.django_db
def test_every_check_is_a_column_grouped_in_dependency_order_with_four_states(fleet):
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    # no table per board type: the summary, the present Pis, the missing ones
    assert " tests</h2>" not in html and "mgmt-testgrid" not in html and html.count("<table") == 3
    checks = _header(html)[len(FIXED):-1]
    # grouped (#127): basic first (jtag before anything), then memory, peripherals, board specific; each named once
    # "this boot" heads Boot check and Check ran, beside the check groups (#129)
    assert _groups(html) == [("this boot", 2), ("basic", 2), ("memory", 2), ("peripherals", 4), ("board specific", 5)]
    assert checks == EXPECTED_CHECKS
    assert len(set(checks)) == len(checks) == sum(n for g, n in _groups(html) if g != "this boot")
    rows = _by_serial(html)
    # every cell is one of four marks: ✓ ran and passed, ✗ ran and failed, ⊘ did not run, – not applicable
    for row in rows.values():
        assert {row[name] for name in checks} <= {"✓", "✗", "⊘", "–"}
    assert rows["pi-sw2-p46"]["jtag"] == "✓" and rows["pi-sw2-p46"]["wiring"] == "–"
    assert (rows["pi-sw2-p33"]["wiring"], rows["pi-sw2-p33"]["jtag"]) == ("✗", "–")
    # a check its type gets that its report does not have is ⊘ (#129), never –: the TT board's sdk
    assert rows["pi-sw2-p33"]["sdk"] == "⊘"
    assert 'title="sdk was not reported by this board&#x27;s check (fpgas.online-test-designs#340)"' in html
    assert {rows["pi-sw2-p7"][name] for name in checks} == {"–"}  # a Pi whose check named no board this boot
    # the state is in the cell's title, and said to a screen reader; a – says why it is not applicable
    assert ('data-label="jtag" title="jtag passed"><div class="mgmt-test-pass">✓<span class="mgmt-sr"> jtag passed'
            '</span></div>') in html
    # – carries why the check does not apply to the type (verify's words)
    assert 'title="wiring not applicable to Acorn CLE-215+: its two cables are each tested by name' in html
    assert 'title="jtag not applicable: no board checked this boot"' in html
    # a cell sorts failures first, then checks that did not run, then passes, then the Pis it does not apply to
    assert re.search(r'<td class="mgmt-mark" data-sort="0" data-label="wiring" title="[^"]*"><div class="mgmt-test-fail"', html)
    assert re.search(r'<td class="mgmt-mark" data-sort="2" data-label="jtag" title="[^"]*"><div class="mgmt-test-pass"', html)
    assert re.search(r'<td class="mgmt-mark" data-sort="3" data-label="jtag" title="[^"]*"><div class="mgmt-test-none"', html)
    # a passing or not applicable mark is not a link: only a ✗ leads to the reason's page
    assert html.count('#boards">✗</a>') == 2 and '#boards">✓' not in html


@pytest.mark.django_db
def test_a_check_left_out_because_an_earlier_one_failed_is_did_not_run(fleet):
    """fpgas-verify lists what it skipped in board<i>_not_run (#127: "Should have run but did not because an earlier
    test failed"); its reason is the check's own words."""
    pi("pi-sw2-p30", {"result": "fail", "board0": "acorn cle-215+ fail", "board0_tests": "pcie-link=pass jtag=fail",
                      "board0_not_run": "flash, ddr, scratch over BAR0",
                      "board0_reason": "jtag fail: no IDCODE; flash not run: it needs jtag to pass first"})
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    row = _by_serial(html)["pi-sw2-p30"]
    assert (row["pcie-link"], row["jtag"], row["flash"], row["ddr"]) == ("✓", "✗", "⊘", "⊘")
    assert 'title="flash not run: it needs jtag to pass first"' in html
    # a test the reason does not name: the checks of the board that did not pass
    assert re.search(r'data-sort="1" data-label="ddr" title="ddr did not run: jtag did not pass">'
                     r'<div class="mgmt-test-notrun">⊘', html)
    # a part of a test that ran ("scratch over BAR0") is no column of its own
    assert "scratch over BAR0" not in _header(html)


@pytest.mark.django_db
def test_a_board_whose_check_stopped_before_its_tests_did_not_run_what_its_type_runs(fleet):
    """A check that stopped before any test (no bitstreams, a problem) lists none; the checks other boards of its type
    ran are the ones it should have run (fpgas.online-test-designs#339 asks the check to say so itself)."""
    pi("pi-sw2-p30", {"result": "error", "board0": "acorn cle-215+ error",
                      "board0_reason": "no bitstreams: fpgas-online-acorn-bitstreams is not installed"})
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    row = _by_serial(html)["pi-sw2-p30"]
    assert row["jtag"] == "⊘" and row["wiring"] == "–"  # an Acorn gets jtag; wiring does not apply to it
    assert 'title="jtag did not run: no bitstreams: fpgas-online-acorn-bitstreams is not installed"' in html


@pytest.mark.django_db
def test_every_result_but_pass_is_a_fail_mark_with_the_result_in_its_title(fleet):
    """fpgas-verify: only "pass" means the board is as it should be ("changed", "missing", "error" are not)."""
    pi("pi-sw2-p30", {"result": "fail", "board0": "acorn - fail", "board0_tests": "jtag=error ddr=pass flash=changed",
                      "board0_reason": "jtag error: openocd: no device; the cable may be loose; flash changed: id"})
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    row = _by_serial(html)["pi-sw2-p30"]
    assert (row["jtag"], row["ddr"], row["flash"]) == ("✗", "✓", "✗")  # the mark is the cell's whole text
    # each ✗ carries its own test's part of the reason; a part that names no test stays with the one before
    assert 'title="jtag error: openocd: no device; the cable may be loose"' in html
    assert 'title="flash changed: id"' in html


@pytest.mark.django_db
def test_a_reason_that_names_no_test_is_the_boards_whole_reason():
    board = {"label": "acorn-holly 0x1", "tests": [("jtag", "fail")], "not_run": [],
             "reason": "the board did not answer"}
    from management.views.fpgas import test_reason

    assert test_reason(board, "jtag") == "the board did not answer"
    assert test_reason(dict(board, reason=""), "jtag") == ""


@pytest.mark.django_db
def test_the_up_column_sorts_by_seconds_not_text(fleet):
    for hostname, seconds in (("pi-sw2-p46", 3 * 86400), ("pi-sw2-p47", 700), ("pi-sw2-p33", 90000)):
        Machine.objects.filter(hostname=hostname).update(last_uptime_s=seconds)
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    keys = re.findall(r'<td class="mgmt-c-up" data-label="Up" data-sort="(\d+)">([^<]*)</td>', html)
    assert ("259200", "3 d 0 h") in keys and ("700", "11 min") in keys and ("90000", "1 d 1 h") in keys
    # an unreported or stale uptime sorts as 0, with nothing shown
    assert ("0", "") in keys


@pytest.mark.django_db
def test_a_pi_seen_in_the_future_has_the_clock_error_in_notes_and_the_page_stands(fleet):
    Machine.objects.filter(hostname="pi-sw2-p46").update(last_seen=timezone.now() + datetime.timedelta(hours=2, minutes=1))
    r = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/")
    assert r.status_code == 200
    rows = _by_port(r.content.decode())
    assert rows["sw2 p46"]["Seen"] == ""  # Seen is a time or nothing
    assert "<div>no model registered</div><div>clock 2 h ahead</div>" in r.content.decode()  # one fact a line
    assert rows["sw2 p47"]["Seen"].endswith("ago")


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
    assert _by_port(html)["sw2 p20"]["Offered"] == "✓"
    assert '<a class="mgmt-test-pass" href="https://tinytapeout.fpgas.online/board/' in html
    assert 'abcd1234/">✓</a>' in html
    tt = Client(HTTP_HOST="tinytapeout.fpgas.online").get("/management/fpgas/").content.decode()
    assert 'abcd1234/">✓</a>' in tt and 'href="https://tinytapeout.fpgas.online' not in tt  # its own host: a path
    # an offered Pi that no visitor page shows
    from unittest import mock

    with mock.patch("ttsite.boards.pages", return_value=[]):
        html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    assert _by_port(html)["sw2 p20"]["Offered"] == "✗"
    assert 'data-label="Offered" data-sort="0" title="not offered: no visitor page"><span class="mgmt-test-fail">✗' in html
    # every ✓ links a visitor page, every ✗ carries its reason as its title (#127: "a popup / shown on hover")
    offered = re.findall(r'<td class="mgmt-c-offered mgmt-mark" data-label="Offered" data-sort="(\d)" title="([^"]*)">'
                         r'(<a class="mgmt-test-pass" href="[^"]+">✓</a>|<span class="mgmt-test-fail">✗</span>)', html)
    assert len(offered) == len(_rows(html))
    for sort, title, mark in offered:
        assert (sort == "1") == (title == "offered") == ("✓" in mark)
        assert title == "offered" or (title.startswith("not offered: ") and len(title) > len("not offered: "))


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
    # an Orange Pi's kernel gives revision 0000 (welland's seven, 15 Oct 2026): its model names it
    ({"revision_code": "0000", "model": "Xunlong Orange Pi PC", "mem_total_kb": 1016548}, "Orange Pi PC 1 GB S1"),
    # a Raspberry Pi's model with a code that is not one: the code is the fault
    ({"revision_code": "0000", "model": "Raspberry Pi 4 Model B Rev 1.4"}, "S1 (revision '0000' not readable)"),
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
    row = _by_serial(html)["02c0008164c2a1b0"]
    assert (row["Model"], row["Serial"]) == ("OPi PC", "02c0008164c2a1b0")  # short (#92), the label's words on hover
    assert '<a href="/fleet/02c0008164c2a1b0/">02c0008164c2a1b0</a>' in html
    assert 'data-label="Model" title="Orange Pi PC; registered as Xunlong Orange Pi PC">OPi PC</td>' in html
    # the registered model in full is on the host's own page, not only in a tooltip here
    assert "<td>Xunlong Orange Pi PC</td>" in Client(HTTP_HOST="welland.fpgas.online").get(
        "/fleet/02c0008164c2a1b0/").content.decode()


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



@pytest.mark.django_db
def test_a_filter_that_matches_nothing_says_so_under_the_table(fleet):
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/?type=Arty+A7").content.decode()
    assert "<p>No Raspberry Pi matches.</p>" in html and "colspan" not in html


@pytest.mark.django_db
def test_with_two_boards_each_test_names_its_board(fleet):
    pi("pi-sw2-p30", {"result": "pass", "board0": "acorn - pass", "board0_tests": "pin-id=pass",
                      "board1": "tt tt-fpga pass", "board1_identity_chip": "fpga", "board1_tests": "pin-id=pass"})
    pi("pi-sw2-p32", {"result": "fail", "board0": "acorn - pass", "board0_tests": "pin-id=pass jtag=pass",
                      "board1": "tt tt-fpga fail", "board1_identity_chip": "fpga", "board1_tests": "pin-id=fail",
                      "board1_reason": "pin-id fail: pin 3 stuck low"})
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    # a name two types share (pin-id, now wiring) is one column; a Pi with two boards has a mark per board
    assert _header(html).count("wiring") == 1 and "pin-id" not in _header(html)
    rows = _by_serial(html)
    assert rows["pi-sw2-p30"]["Board"].split("\n") == ["Acorn", "TT FPGA"]  # the types (#127)
    assert rows["pi-sw2-p30"]["ID"].split("\n") == ["–", "–"]  # neither check read an id
    # the Acorn gets jtag and did not report it (⊘); jtag does not apply to a TT FPGA (–)
    assert rows["pi-sw2-p30"]["wiring"] == "✓\n✓" and rows["pi-sw2-p30"]["jtag"] == "⊘\n–"
    assert rows["pi-sw2-p32"]["wiring"] == "✓\n✗" and rows["pi-sw2-p32"]["jtag"] == "✓\n–"
    # the cell sorts as its worst mark, and its ✗ says which check failed and why
    # the cell's title says each board's result a line, in the Board cell's order (#123: on the cell, not the mark)
    assert re.search(r'data-sort="0" data-label="wiring" title="wiring passed\npin-id fail: pin 3 stuck low">'
                     r'<div class="mgmt-test-pass">✓<span class="mgmt-sr"> wiring passed</span></div>'
                     r'<div class="mgmt-test-fail"><a href="/fleet/pi-sw2-p32/#boards">✗</a>', html)
    assert 'title="jtag passed\njtag not applicable to TT FPGA: the iCE40UP5K has no JTAG port' in html


@pytest.mark.django_db
def test_a_revision_that_cannot_be_read_is_a_short_note_and_the_label_is_the_serial_alone():
    from fleet.models import HardwareSnapshot

    m = pi("pi-sw1-p3", serial="285df3f84af242d0")
    m.latest_snapshot = HardwareSnapshot.objects.create(machine=m, fingerprint="f", document={
        "machine": {"serial": m.serial, "revision_code": "zz"}})
    m.save()
    assert fpga.pi_label_parts(m) == ("285df3f84af242d0", "revision 'zz' not readable", "revision unreadable")
    assert fpga.pi_label(m) == "285df3f84af242d0 (revision 'zz' not readable)"  # the registry JSON's one string
    row = _rows(Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode())[0]
    assert (row["Model"], row["RAM"], row["Serial"]) == ("", "", "285df3f84af242d0")
    assert row["Notes"] == "revision unreadable"
    assert "not readable" not in Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()


@pytest.mark.django_db
def test_two_boards_of_one_type_are_one_row_with_a_mark_per_board_label(fleet):
    pi("pi-sw2-p31", {"result": "pass", "board0": "acorn - pass", "board0_tests": "jtag=pass",
                      "board0_identity_dna": "0x00200c8664b04854",
                      "board1": "acorn - pass", "board1_tests": "jtag=fail", "board1_identity_dna": "0x0054b48664b04854"})
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    assert _header(html).count("jtag") == 1
    row = _by_serial(html)["pi-sw2-p31"]
    assert row["Board"].split("\n") == ["Acorn", "Acorn"]  # the type, a board a line (#127)
    assert row["ID"].split("\n") == ["0x00200c8664b04854", "0x0054b48664b04854"]
    assert row["jtag"] == "✓\n✗"  # the first board's mark, then the second's
    # a Pi with one board has one mark
    assert _by_serial(html)["pi-sw2-p46"]["jtag"] == "✓"


@pytest.mark.django_db
def test_the_board_cell_lists_a_label_a_line(fleet):
    pi("pi-sw2-p31", {"result": "pass", "board0": "acorn - pass", "board1": "acorn - pass",
                      "board0_identity_dna": "0x00200c8664b04854", "board1_identity_dna": "0x0054b48664b04854"})
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    # the type a line, and its id a line with the board's name (its label) as the title (#127)
    assert re.search(r'<td class="mgmt-c-board" data-label="Board"><div>Acorn</div><div>Acorn</div></td>'
                     r'\s*<td class="mgmt-c-id" data-label="ID"><div title="acorn-\w+ 0x00200c8664b04854">'
                     r'0x00200c8664b04854</div><div title="acorn-\w+ 0x0054b48664b04854">0x0054b48664b04854</div></td>',
                     html)


@pytest.mark.parametrize("model, short", [
    ("Pi 3 Model B+", "Pi 3B+"), ("Pi 4 Model B", "Pi 4B"), ("Pi 5", "Pi 5"), ("Pi Zero 2 W", "Pi Zero 2 W"),
    ("Pi Compute Module 4", "Pi CM4"), ("Orange Pi PC", "OPi PC"), ("Orange Pi Zero", "OPi Zero"),
])
def test_a_model_is_short_on_the_dashboard(model, short):
    """The dashboard spec (Tim, 9 Oct 2026): "Pi 3B+", "Pi 5", "OPi PC"; the label's words are the title."""
    assert fpga.short_model(model) == short


@pytest.mark.django_db
def test_memory_is_its_own_column_and_sorts_as_megabytes():
    from fleet.models import HardwareSnapshot

    for serial, code in (("s-a", "a020d3"), ("s-b", "c04170"), ("s-c", "902120")):  # 1 GB, 4 GB, 512 MB
        m = pi(f"pi-sw1-p{len(serial)}{code[-1]}", serial=serial)
        m.latest_snapshot = HardwareSnapshot.objects.create(machine=m, fingerprint=serial, document={
            "machine": {"serial": serial, "revision_code": code}})
        m.save()
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    rows = _by_serial(html)
    assert (rows["s-a"]["Model"], rows["s-a"]["RAM"]) == ("Pi 3B+", "1 GB")
    assert (rows["s-c"]["Model"], rows["s-c"]["RAM"]) == ("Pi Zero 2 W", "512 MB")
    assert 'data-label="Memory" data-sort="1024">1 GB</td>' in html and 'data-sort="512">512 MB</td>' in html
    assert 'title="Pi 3 Model B+">Pi 3B+</td>' in html  # the label's own words on hover


@pytest.mark.django_db
def test_the_port_is_where_the_gateway_last_leased_the_pi_an_address_with_the_time(fleet, settings):
    from fleet.models import GatewayRecord

    settings.MANAGEMENT_TIME_ZONE = "Australia/Adelaide"
    m = Machine.objects.get(hostname="pi-sw2-p46")
    at = datetime.datetime(2026, 10, 15, 5, 18, 22, tzinfo=datetime.timezone.utc)
    for port, when in ((40, at - datetime.timedelta(hours=1)), (41, at)):  # the newest record is the observation
        GatewayRecord.objects.create(site="welland", kind="lease", switch=2, port=port, mac="98:fe:54:13:e0:75",
                                     at=when, doc={}, machine=m)
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    row = _by_serial(html)["pi-sw2-p46"]
    assert row["Port"] == "sw2 p41"
    assert 'data-sort="002041" title="the gateway leased it an address on this port at 15 Oct 15:48">sw2 p41' in html
    # its host name names another port: a short fact in Notes, the observation stands
    assert "named sw2 p46" in row["Notes"]
    # a Pi the gateway has no lease record of: no port observed (the review of #122: a host name's port is the Pi's
    # word, not an observation); Notes names the port its host name names
    pi("pi-sw2-p50", lease=False)
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    row = _by_serial(html)["pi-sw2-p50"]
    assert (row["Port"], row["Notes"]) == ("", "no model registered\nno lease seen; named sw2 p50")


@pytest.mark.django_db
def test_a_xilinx_pcie_card_is_a_pcie_card_and_an_unknown_board_name_is_a_note(fleet):
    """fpgas-verify calls a Xilinx PCIe card that is not ours "xilinx-pcie" (welland's showed as "other", 15 Oct)."""
    pi("pi-sw1-p38", {"result": "fail", "board0": "xilinx-pcie - fail"})
    pi("pi-sw1-p39", {"result": "fail", "board0": "gizmo - fail"})
    rows = _by_serial(Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode())
    assert rows["pi-sw1-p38"]["Board"] == "PCIe card" and "not known" not in rows["pi-sw1-p38"]["Notes"]
    assert "board 'gizmo' not known" in rows["pi-sw1-p39"]["Notes"]


@pytest.mark.django_db
def test_a_fail_whose_reason_names_no_test_says_the_test_and_result_first():
    from management.views.fpgas import _mark

    board = {"label": "acorn-holly 0x1", "tests": [("jtag", "fail")], "not_run": [], "not_run_parts": [], "failing": ["jtag"],
             "result": "fail", "title": "Acorn", "type": "Acorn", "reason": "the board did not answer"}
    assert _mark(board, "jtag", {})[0].title == "jtag fail: the board did not answer"
    assert _mark(dict(board, reason=""), "jtag", {})[0].title == "jtag fail"


@pytest.mark.django_db
def test_the_number_of_queries_for_the_pi_table_does_not_grow_with_the_fleet(fleet, django_assert_max_num_queries):
    c = Client(HTTP_HOST="welland.fpgas.online")
    with django_assert_max_num_queries(20) as before:
        c.get("/fleet/")
    for n in range(20):
        pi(f"pi-sw1-p{n + 1}", REAL["pi-sw2-p46"], serial=f"20000000{n:08x}")
    with django_assert_max_num_queries(len(before.captured_queries)):
        c.get("/fleet/")


@pytest.mark.django_db
def test_checked_is_when_the_boot_check_of_this_boot_reported(fleet, settings):
    settings.MANAGEMENT_TIME_ZONE = "Australia/Adelaide"
    BootEvent.objects.filter(machine__hostname="pi-sw2-p46").update(ts=timezone.now() - datetime.timedelta(hours=2))
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    rows = _by_serial(html)
    assert rows["pi-sw2-p46"]["Check ran"] == "2 h ago"
    assert rows["pi-sw2-p7"]["Check ran"].endswith("ago")  # a running check: when it started reporting
    assert re.search(r'<td class="mgmt-c-checked" data-label="Check ran" data-sort="\d+"><time ', html)


@pytest.mark.django_db
@pytest.mark.parametrize("zone", ["Australia/Adelaide", "America/Chicago"])
def test_the_fleet_page_says_times_are_the_zone_in_the_exact_form_the_gateway_verify_reads(fleet, settings, zone):
    """fpgas.online-infra's site verify (roles/site/tasks/verify/main.yml, "Assert the dashboard's times are in
    site_timezone") GETs /fleet/ and requires the exact sentence "Times are <site_timezone>."; #122 changed it to
    "Times are <zone>, ..." and deploy 27's verify failed (site #124). The key keeps that sentence word for word."""
    settings.MANAGEMENT_TIME_ZONE = zone
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    assert f"Times are {zone}." in html


@pytest.mark.django_db
def test_a_part_of_a_test_left_out_is_said_in_that_tests_cell(fleet):
    """fpgas-verify's not_run may name a part of a test that ran ("scratch over BAR0"): said, never dropped."""
    pi("pi-sw2-p30", {"result": "fail", "board0": "acorn cle-215+ fail", "board0_tests": "scratch=pass",
                      "board0_not_run": "scratch over BAR0"})
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    assert _by_serial(html)["pi-sw2-p30"]["scratch"] == "✓"
    assert 'title="scratch passed (scratch over BAR0 not run)"' in html


@pytest.mark.django_db
def test_did_not_run_says_only_what_the_check_said(fleet):
    """A board that stopped before its tests and gave no reason: the title is its result, nothing invented."""
    pi("pi-sw2-p30", {"result": "error", "board0": "acorn cle-215+ error"})
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    assert 'title="jtag did not run: the check of this board gave error"' in html
    assert "did not run: an earlier check did not pass" not in html


@pytest.mark.django_db
def test_a_stopped_board_of_a_type_the_site_does_not_know_says_its_checks_are_not_reported(fleet):
    """A board whose check stopped before its tests and whose type has no planned list (neither its report nor the
    site's copy of verify's lists says which checks it gets): its cells stay – (nothing is known) and Notes says so,
    loud (fpgas.online-test-designs#339). A known type's checks are ⊘ whatever its variant."""
    pi("pi-sw1-p39", {"result": "fail", "board0": "gizmo - fail"})
    pi("pi-sw2-p30", {"result": "error", "board0": "acorn other-variant error"})
    rows = _by_serial(Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode())
    assert "other: checks not reported" in rows["pi-sw1-p39"]["Notes"]
    assert rows["pi-sw2-p30"]["jtag"] == "⊘" and "checks not reported" not in rows["pi-sw2-p30"]["Notes"]


def _orange_pi(hostname, serial, detail):
    from fleet.models import HardwareSnapshot

    m = pi(hostname, detail, serial=serial)
    m.latest_snapshot = HardwareSnapshot.objects.create(machine=m, fingerprint=serial, document={
        "machine": {"serial": serial, "revision_code": "0000", "model": "Xunlong Orange Pi PC", "mem_total_kb": 1016548}})
    m.save()
    return m


@pytest.mark.django_db
def test_a_host_that_carries_no_board_is_its_own_type_and_operational_when_its_check_finds_none(fleet):
    """The coordinator's default for coord-65 (15 Oct 2026): welland's Orange Pi PCs carry no FPGA by design; they are
    their own type, ● when the check finds no board, Offered – (not applicable), never needs attention for it."""
    _orange_pi("pi-sw2-p18", "02c000810b122044", {"result": "missing"})
    rows = fpga.hosts()
    host = next(r for r in rows if r.machine.serial == "02c000810b122044")
    assert (host.board_type, host.condition, host.no_board_expected) == (fpga.NO_FPGA, "operational", True)
    assert any(t == fpga.NO_FPGA and counts["operational"] == 1 for t, counts, _ in fpga.summary(rows)[0])
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    row = _by_serial(html)["02c000810b122044"]
    assert (row["State"], row["Board"], row["Offered"], row["Boot check"]) == ("●", "no FPGA", "–", "✓")
    assert 'title="not applicable: this host carries no board"' in html
    assert "found no board, as expected: this host carries none" in html
    # a Raspberry Pi that finds no board still needs attention
    pi("pi-sw2-p19", {"result": "missing"}, serial="10000000aaaa0019")
    assert _by_serial(Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode())[
        "10000000aaaa0019"]["State"] == "!"


@pytest.mark.django_db
def test_the_boards_own_planned_list_and_reasons_win_over_the_sites_copy(fleet):
    """fpgas-verify's coming fields (fpgas.online-test-designs #339/#340): board<i>_planned, the reason a planned test
    did not run (board<i>_not_run_<test>) and why a common check does not apply (board<i>_not_applicable_<check>)."""
    pi("pi-sw2-p30", {"result": "fail", "board0": "arty a7-35 fail", "board0_tests": "jtag=pass uart=fail",
                      "board0_planned": "jtag uart ddr", "board0_not_run": "ddr",
                      "board0_not_run_ddr": "it needs uart to pass first",
                      "board0_not_applicable_wiring": "a test cable, not a Pmod, on this bench"})
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    row = _by_serial(html)["pi-sw2-p30"]
    assert (row["jtag"], row["uart"], row["ddr"], row["wiring"], row["flash"]) == ("✓", "✗", "⊘", "–", "–")
    assert 'title="ddr did not run: it needs uart to pass first"' in html
    assert 'title="wiring not applicable to Arty A7 A7-35: a test cable, not a Pmod, on this bench"' in html


@pytest.mark.django_db
def test_a_planned_check_with_no_test_yet_names_its_issue(fleet):
    pi("pi-sw2-p30", {"result": "pass", "board0": "netv2 a7-35 pass", "board0_tests": "jtag=pass uart=pass"})
    html = Client(HTTP_HOST="welland.fpgas.online").get("/fleet/").content.decode()
    assert _by_serial(html)["pi-sw2-p30"]["ethernet"] == "⊘"
    assert 'title="ethernet did not run: no boot-check test yet (fpgas.online-test-designs#341)"' in html
