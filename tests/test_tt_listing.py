"""Tiny Tapeout boards on the site's own list (docs/superpowers/specs/2026-10-04-tinytapeout-listing-design.md,
section 2): an FPGA board is shown, an ASIC board is not, and a `tt` board whose variant is neither is shown
nowhere. What a Pi carries comes from its `fpga-verified` event, which is sent even when the progress events
were lost. Ping and upload stay open to every offered Pi, whatever its boards."""

import pytest
from django.test import Client
from fleet.models import BootEvent
from fleet.services import verified_boards
from pibfpgas.pis import board_title, listed, offered, shown_here

from tests.fleet_pis import machine, verified_detail, verified_pi, verifying


@pytest.fixture
def c(settings):
    settings.DOMAIN_NAME = "welland.fpgas.online"
    settings.PI_PW = "cGFzc3dvcmQ="
    return Client(HTTP_HOST="welland.fpgas.online")


def _verified(m, boards, result="pass", boot_id="b2"):
    BootEvent.objects.create(machine=m, boot_id=boot_id, stage="fpga-verified", ts=m.last_seen,
                             detail={"result": result, "mode": "auto", **verified_detail(boards, result)})


# -- what the fpga-verified event says a Pi carries -----------------------------------------------------------


@pytest.mark.django_db
def test_verified_boards_come_from_the_verified_event_of_this_boot():
    m = machine("now")
    _verified(m, [("acorn", "cle-215+")], boot_id="b1")  # an earlier boot's
    _verified(m, [("tt", "tt-asic", {"shuttle": "tt06", "usb_serial": "E661", "sdk": "2.0.4"})])
    assert verified_boards() == {"now": [{"board": "tt", "variant": "tt-asic", "result": "pass",
                                          "identity": {"kind": "tt", "shuttle": "tt06", "usb_serial": "E661",
                                                       "sdk": "2.0.4"}}]}


@pytest.mark.django_db
def test_a_check_that_is_running_again_has_no_verified_boards():
    m = machine("again")
    _verified(m, [("tt", "tt-fpga")])
    verifying(m)
    assert verified_boards() == {}


@pytest.mark.django_db
def test_two_boards_of_one_kind_keep_their_kind_and_no_variant_is_empty():
    m = machine("two")
    _verified(m, [("arty@1-1.2", "a7-35"), ("arty@1-1.4", "a7-100"), ("tt", None)])
    assert [(b["board"], b["variant"]) for b in verified_boards()["two"]] == [
        ("arty", "a7-35"), ("arty", "a7-100"), ("tt", "")]


@pytest.mark.django_db
def test_a_detail_that_is_not_as_fpgas_verify_sends_it_is_left_out():
    """The fleet broker is open on the site LAN: anyone on a Pi can publish."""
    for i, detail in enumerate(("pass", {"result": "pass", "board0": 7}, {"result": "pass", "board0": "tt"},
                                {"result": "pass", "boardx": "tt tt-fpga pass"})):
        m = machine(f"odd{i}")
        BootEvent.objects.create(machine=m, boot_id="b2", stage="fpga-verified", ts=m.last_seen, detail=detail)
    assert verified_boards() == {}


# -- which boards this site shows -----------------------------------------------------------------------------


@pytest.mark.parametrize("board, variant, shown", [
    ("tt", "tt-fpga", True),
    ("tt", "tt-asic", False),
    ("tt", "", False),  # not identified: on neither site
    ("tt", "tt-fgpa", False),  # an allow-list: anything unexpected is not shown
    ("acorn", "cle-215+", True),
    ("arty", "", True),
])
def test_only_an_fpga_tiny_tapeout_board_is_shown_here(board, variant, shown):
    assert shown_here({"board": board, "variant": variant}) is shown


@pytest.mark.parametrize("variant, title", [("tt-fpga", "TT FPGA"), ("tt-asic", "TT ASIC")])
def test_a_tiny_tapeout_board_is_titled_by_what_it_is(variant, title):
    assert board_title({"board": "tt", "variant": variant}) == title


@pytest.mark.django_db
def test_an_asic_only_pi_is_offered_but_not_listed_and_has_no_page(c):
    verified_pi("pi-sw2-p6", ("tt", "tt-asic", {"shuttle": "tt06"}))
    verified_pi("pi-sw2-p33", ("tt", "tt-fpga"))
    assert [pi.hostname for pi in offered()] == ["pi-sw2-p6", "pi-sw2-p33"]  # ping and upload still work
    assert [pi.hostname for pi in listed()] == ["pi-sw2-p33"]
    html = c.get("/fpgas/").content.decode()
    assert "pi-sw2-p33</h1>TT FPGA</td>" in html and "pi-sw2-p6" not in html
    assert c.get("/fpgas/pi-sw2-p33.html").status_code == 200
    assert c.get("/fpgas/pi-sw2-p6.html").status_code == 404


@pytest.mark.django_db
def test_a_pi_with_an_asic_and_another_board_is_listed_without_the_asic(c):
    verified_pi("pi-sw2-p9", ("arty", "a7-35"), ("tt", "tt-asic", {"shuttle": "tt05"}))
    html = c.get("/fpgas/").content.decode()
    assert "pi-sw2-p9</h1>Arty A7 (a7-35)</td>" in html and "TT ASIC" not in html
    assert "TT ASIC" not in c.get("/fpgas/pi-sw2-p9.html").content.decode()


@pytest.mark.django_db
def test_a_tiny_tapeout_board_that_was_not_identified_is_not_listed(c):
    verified_pi("pi-sw2-p35", ("tt", None))
    assert "pi-sw2-p35" not in c.get("/fpgas/").content.decode()


@pytest.mark.django_db
def test_the_name_survives_lost_progress_events(c):
    """A broker timeout stops fpga-board-found; fpga-verified is still sent and carries the boards."""
    m = machine("lost", "pi-sw2-p46")
    _verified(m, [("acorn", "cle-215+")])
    assert "pi-sw2-p46</h1>Acorn (cle-215+)</td>" in c.get("/fpgas/").content.decode()

