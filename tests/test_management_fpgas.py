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
    assert row.why == "its boot check gave fail: pin-id fail: the test exited 1; spiflash fail: the test exited 1"
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
