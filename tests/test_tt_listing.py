"""Tiny Tapeout boards on the site's own list (docs/superpowers/specs/2026-10-04-tinytapeout-listing-design.md,
section 2): an FPGA board is shown, an ASIC board is not, and a `tt` board whose variant is neither is shown
nowhere. What a Pi carries comes from its `fpga-verified` event, which is sent even when the progress events
were lost. Ping and upload stay open to every offered Pi, whatever its boards."""

import json
import pathlib

import pytest
from django.test import Client
from fleet.models import BootEvent
from fleet.services import MAX_BOARDS, verified_boards
from pibfpgas.pis import board_title, listed, offered, shown_here

from tests.fleet_pis import machine, verified_detail, verified_pi, verifying

# fpga-verified as three Welland Pis sent it on 2026-10-04 (fpgas-verify 0.0.post1013), copied from the public
# /fleet/<serial>/ pages: the two Acorn hosts, which passed, and a Tiny Tapeout FPGA host, which failed.
REAL = json.loads((pathlib.Path(__file__).parent / "data" / "fpga-verified-welland-2026-10-04.json").read_text())
# The three Tiny Tapeout FPGA hosts' first passing events (2026-10-05, from the public /fleet/ pages), by Pi serial.
REAL_TT = json.loads((pathlib.Path(__file__).parent / "data" / "fpga-verified-tt-fpga-2026-10-05.json").read_text())


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
                                          "identity": {"board": "tt", "kind": "tt", "variant": "tt-asic",
                                                       "shuttle": "tt06", "usb_serial": "E661", "sdk": "2.0.4"}}]}


@pytest.mark.django_db
def test_a_check_that_is_running_again_has_no_verified_boards():
    m = machine("again")
    _verified(m, [("tt", "tt-fpga")])
    verifying(m)
    assert verified_boards() == {}


@pytest.mark.django_db
def test_boards_keep_the_checks_order_and_no_variant_is_empty():
    m = machine("two")
    _verified(m, [("arty", "a7-35"), ("tt", None)])
    assert [(b["board"], b["variant"]) for b in verified_boards()["two"]] == [("arty", "a7-35"), ("tt", "")]


@pytest.mark.django_db
def test_a_detail_that_is_not_an_event_names_no_board():
    """The fleet broker is open on the site LAN: anyone on a Pi can publish."""
    for i, detail in enumerate(("pass", {"result": "pass"}, {"result": "pass", "boardx": "tt tt-fpga pass"},
                                {"result": "pass", "board01": "tt tt-fpga pass"},
                                {"result": "pass", "board\u0661": "tt tt-fpga pass"})):
        m = machine(f"odd{i}")
        BootEvent.objects.create(machine=m, boot_id="b2", stage="fpga-verified", ts=m.last_seen, detail=detail)
    assert verified_boards() == {}


@pytest.mark.django_db
def test_an_entry_that_cannot_be_read_is_kept_as_unreadable_not_dropped():
    m = machine("bad")
    BootEvent.objects.create(machine=m, boot_id="b2", stage="fpga-verified", ts=m.last_seen,
                             detail={"result": "pass", "board0": 7, "board1": "tt", "board2": "tt tt-asic x pass",
                                     "board3": "acorn cle-215+ pass"})
    assert [b["board"] for b in verified_boards()["bad"]] == ["", "", "", "acorn"]


@pytest.mark.django_db
def test_a_key_too_long_to_be_a_number_breaks_nothing(c):
    """int() of thousands of digits raises: such a key is no board, and the pages still answer."""
    m = machine("huge", "pi-sw2-p46")
    BootEvent.objects.create(machine=m, boot_id="b2", stage="fpga-verified", ts=m.last_seen,
                             detail={"result": "pass", "board" + "9" * 5000: "tt tt-asic pass",
                                     "board0": "acorn cle-215+ pass"})
    assert [b["board"] for b in verified_boards()["huge"]] == ["acorn"]
    assert c.get("/fpgas/").status_code == 200


@pytest.mark.django_db
def test_no_more_boards_are_read_than_a_pi_can_carry():
    m = machine("many")
    BootEvent.objects.create(machine=m, boot_id="b2", stage="fpga-verified", ts=m.last_seen,
                             detail={"result": "pass", **{f"board{i}": "arty a7-35 pass" for i in range(400)}})
    assert len(verified_boards()["many"]) == MAX_BOARDS


# -- which boards this site shows -----------------------------------------------------------------------------


@pytest.mark.parametrize("board, variant, kind, shown", [
    ("tt", "tt-fpga", "tt", True),
    ("tt", "tt-fpga", None, True),  # no kind in the identity: fpgas-verify before 0.0.post1013
    ("tt", "tt-asic", "tt", False),
    ("tt", "", "tt", False),  # not identified: on neither site
    ("tt", "tt-fgpa", "tt", False),  # an allow-list: anything unexpected is not shown
    ("tt", "tt-asic", "acorn", False),  # a kind that disagrees with the board does not unhide it
    ("tt", "tt-fpga", "acorn", False),
    ("tt", "tt-fpga", "-", False),
    ("arty", "tt-fpga", "tt", False),  # says it is a Tiny Tapeout board by kind only: hidden, not believed
    ("TT", "tt-fpga", "tt", False),
    ("", "", None, False),  # an entry that could not be read
    ("acorn", "cle-215+", "acorn", True),
    ("acorn", "cle-215+", "pcileech", True),  # rpi-hwid's name for the design found on an Acorn card
    ("arty", "", None, True),
])
def test_only_a_consistent_fpga_tiny_tapeout_board_is_shown_here(board, variant, kind, shown):
    identity = {} if kind is None else {"kind": kind}
    assert shown_here({"board": board, "variant": variant, "identity": identity}) is shown


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



# -- today's real events --------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_acorn_hosts_read_as_before_from_their_real_events(c):
    for host in ("pi-sw2-p46", "pi-sw2-p47"):
        m = machine(host, host)
        BootEvent.objects.create(machine=m, boot_id="b2", stage="fpga-verified", ts=m.last_seen, detail=REAL[host])
    assert [(pi.hostname, pi.boards) for pi in listed()] == [("pi-sw2-p46", "Acorn (cle-215+)"),
                                                             ("pi-sw2-p47", "Acorn (cle-215+)")]
    assert "pi-sw2-p46</h1>Acorn (cle-215+)</td>" in c.get("/fpgas/").content.decode()
    assert "Accessing pi-sw2-p47 &mdash; Acorn (cle-215+)</h1>" in c.get("/fpgas/pi-sw2-p47.html").content.decode()


@pytest.mark.django_db
def test_real_passing_tiny_tapeout_fpga_hosts_are_listed_as_tt_fpga_with_their_identity(c):
    """The boot check that writes nothing to the board and names it (fpgas-online-verify 0.0.post1094)."""
    for i, (serial, detail) in enumerate(sorted(REAL_TT.items())):
        m = machine(serial, f"pi-sw9-p{i + 1}")  # a place for the test: the event does not say where a Pi sits
        BootEvent.objects.create(machine=m, boot_id="b2", stage="fpga-verified", ts=m.last_seen, detail=detail)
    assert [(pi.hostname, pi.boards) for pi in listed()] == [(f"pi-sw9-p{i}", "TT FPGA") for i in (1, 2, 3)]
    boards = verified_boards()
    assert sorted(b["identity"]["usb_serial"] for (b,) in boards.values()) == [
        "4df39a7a6856f86f", "8c46329b33590ecb", "a2961e5cac65b25f"]  # fmt: skip
    assert {(b["identity"]["mcu"], b["identity"]["chip"], b["identity"]["sdk"]) for (b,) in boards.values()} == {
        ("RP2350", "fpga", "3.1.0")}  # fmt: skip
    html = c.get("/fpgas/").content.decode()
    assert html.count("</h1>TT FPGA</td>") == 3
    assert "Accessing pi-sw9-p1 &mdash; TT FPGA</h1>" in c.get("/fpgas/pi-sw9-p1.html").content.decode()


@pytest.mark.django_db
def test_a_real_failing_tiny_tapeout_host_is_read_but_not_offered(c):
    m = machine("pi-sw2-p33", "pi-sw2-p33")
    BootEvent.objects.create(machine=m, boot_id="b2", stage="fpga-verified", ts=m.last_seen,
                             detail=REAL["pi-sw2-p33"])
    (board,) = verified_boards()["pi-sw2-p33"]
    assert (board["board"], board["variant"], board["result"]) == ("tt", "tt-fpga", "fail")
    assert board["identity"]["usb_serial"] == "4df39a7a6856f86f"
    assert offered() == [] and c.get("/fpgas/pi-sw2-p33.html").status_code == 404  # its check failed


@pytest.mark.django_db
def test_the_identity_kind_does_not_rename_or_unhide_a_board():
    """`board<i>_identity_kind` is rpi-hwid's name for what was found, and is Pi-asserted like the rest."""
    m = machine("pcileech", "pi-sw2-p44")
    _verified(m, [("acorn", "cle-215+", {"kind": "pcileech"})])
    asic = machine("liar", "pi-sw2-p6")
    _verified(asic, [("tt", "tt-asic", {"kind": "arty"})])
    assert [b["board"] for b in verified_boards()["pcileech"]] == ["acorn"]
    assert [pi.hostname for pi in listed()] == ["pi-sw2-p44"]  # the ASIC stays hidden whatever its kind says


@pytest.mark.django_db
def test_a_pi_whose_only_entry_cannot_be_read_is_not_listed(c):
    """Fail closed: it reported a board, and what it is cannot be told."""
    m = machine("garbled", "pi-sw2-p6")
    BootEvent.objects.create(machine=m, boot_id="b2", stage="fpga-verified", ts=m.last_seen,
                             detail={"result": "pass", "board0": "tt tt-asic x pass"})
    assert [pi.hostname for pi in offered()] == ["pi-sw2-p6"] and listed() == []
    assert c.get("/fpgas/pi-sw2-p6.html").status_code == 404


@pytest.mark.django_db
def test_a_pass_from_the_older_fpgas_verify_without_a_kind_is_listed(c):
    m = machine("old", "pi-sw2-p33")
    BootEvent.objects.create(machine=m, boot_id="b2", stage="fpga-verified", ts=m.last_seen,
                             detail={"result": "pass", "mode": "auto",
                                     **verified_detail([("tt", "tt-fpga")], kind=False)})
    assert "pi-sw2-p33</h1>TT FPGA</td>" in c.get("/fpgas/").content.decode()


@pytest.mark.django_db
def test_state_and_boards_are_read_together(django_assert_num_queries):
    verified_pi("pi-sw2-p46", ("acorn", "cle-215+"))
    with django_assert_num_queries(3):  # hostnames, check-ins, FPGA events: one read each
        assert [pi.boards for pi in offered()] == ["Acorn (cle-215+)"]
