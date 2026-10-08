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
    html = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/").content.decode()
    assert "failed: pin-id, spiflash" in html and "pin-id fail: the test exited 1" in html


@pytest.mark.django_db
@pytest.mark.parametrize("host", ["welland.fpgas.online", "ps1.fpgas.online", "tinytapeout.fpgas.online"])
def test_the_page_answers_without_a_login_on_every_host(fleet, host):
    r = Client(HTTP_HOST=host).get("/management/fpgas/")
    assert r.status_code == 200
    html = r.content.decode()
    assert "<title>FPGA hosts &mdash; Management &mdash; fpgas.online</title>" in html
    assert "Summary by board type" in html and "switch 2, port 46" in html
    assert '<meta http-equiv="refresh" content="60">' in html


@pytest.mark.django_db
def test_each_count_links_to_the_pis_it_counts(fleet):
    c = Client(HTTP_HOST="welland.fpgas.online")
    html = c.get("/management/fpgas/").content.decode()
    assert 'href="?type=Acorn&amp;condition=operational">2</a>' in html
    only = c.get("/management/fpgas/?type=Acorn&condition=operational").content.decode()
    assert "pi-sw2-p46" in only and "pi-sw2-p47" in only and "pi-sw2-p33" not in only and "show all" in only
    missing = c.get("/management/fpgas/?condition=missing").content.decode()
    assert "pi-sw2-p4" in missing and "it has stopped checking in" in missing and "pi-sw2-p46" not in missing


@pytest.mark.django_db
def test_text_a_pi_sent_is_escaped_and_cut_short():
    detail = dict(REAL["pi-sw2-p33"], board0_reason="<script>alert(1)</script>" + "x" * 1000)
    pi("pi-sw2-p33", detail)
    html = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/").content.decode()
    assert "<script>alert(1)" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "x" * (fpga.MAX_TEXT + 1) not in html


@pytest.mark.django_db
def test_a_pi_with_no_report_is_shown_by_what_its_registration_says(fleet):
    row = next(r for r in fpga.hosts() if r.machine.hostname == "pi-sw2-p7")
    assert row.boards == [] and row.board_type == fpga.NO_BOARD and row.why == "its boot check is running"


@pytest.mark.django_db
def test_no_pi_registered_says_so():
    html = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/").content.decode()
    assert "No Raspberry Pi has registered." in html


@pytest.mark.django_db
def test_the_fleet_pages_are_linked_only_where_the_host_has_them(fleet):
    welland = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/").content.decode()
    assert '<a href="/fleet/pi-sw2-p46/">pi-sw2-p46</a>' in welland
    tt = Client(HTTP_HOST="tinytapeout.fpgas.online").get("/management/fpgas/").content.decode()
    assert "pi-sw2-p46" in tt and 'href="/fleet/' not in tt


@pytest.mark.django_db
def test_times_are_the_installations_own_with_the_zone_named_once(fleet, settings):
    """Tim: clock times are local, never UTC. welland's gateway runs in Australia/Adelaide."""
    settings.MANAGEMENT_TIME_ZONE = "Australia/Adelaide"
    m = Machine.objects.get(hostname="pi-sw2-p46")
    m.last_seen = datetime.datetime(2026, 10, 9, 0, 15, tzinfo=datetime.timezone.utc)  # 10:45 in Adelaide
    m.save()
    html = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/").content.decode()
    assert "9 Oct 10:45" in html and "9 Oct 00:15" not in html
    assert html.count("Australia/Adelaide") == 1 and "Times are Australia/Adelaide." in html and "UTC" not in html
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
    html = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/").content.decode()
    assert "its own word at registration" in html and "<b>" not in html


@pytest.mark.django_db
def test_the_page_can_stop_reloading_and_ignores_a_filter_it_has_not(fleet):
    c = Client(HTTP_HOST="welland.fpgas.online")
    assert '<meta http-equiv="refresh"' not in c.get("/management/fpgas/?reload=off").content.decode()
    odd = c.get("/management/fpgas/?condition=bogus").content.decode()
    assert "pi-sw2-p46" in odd and "show all" not in odd


@pytest.mark.django_db
def test_an_offered_pi_links_to_its_visitor_page_where_the_host_has_one(fleet):
    welland = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/").content.decode()
    assert '<a href="/fpgas/pi-sw2-p46.html">its page</a>' in welland
    tt = Client(HTTP_HOST="tinytapeout.fpgas.online").get("/management/fpgas/").content.decode()
    assert "/fpgas/pi-sw2-p46.html" not in tt


@pytest.mark.django_db
def test_its_page_is_linked_only_for_a_pi_the_fpgas_pages_list(fleet):
    """Review 2 of #74: a TT chip board's Pi is offered but not listed on /fpgas/ (that page is not its own)."""
    from pibfpgas.pis import listed

    pi("pi-sw2-p20", {"result": "pass", "board0": "tt tt-asic pass", "board0_identity_chip": "asic"})
    assert "pi-sw2-p20" not in {p.hostname for p in listed()}
    html = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/").content.decode()
    assert "/fpgas/pi-sw2-p20.html" not in html
    for hostname in {p.hostname for p in listed()}:
        assert f'<a href="/fpgas/{hostname}.html">its page</a>' in html


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
    r = Client(HTTP_HOST="welland.fpgas.online").get("/management/fpgas/")
    assert r.status_code == 200 and next(h for h in fpga.hosts() if h.machine.pk == m.pk).board_type == fpga.NO_BOARD
