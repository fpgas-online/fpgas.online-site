"""The board named on /fpgas/ and on each board page is the one the Pi on that
port found this boot: fpgas-verify's `fpga-board-found` events (board,
variant, where), never a hand-typed label (docs/verify-events.md)."""

import datetime

import pytest
from django.test import Client
from django.utils import timezone
from fleet.models import BootEvent, Machine
from fleet.services import found_boards
from pibfpgas.pis import board_title

from tests.fleet_pis import by_serial, verified_pi

T0 = timezone.now()


def machine(serial, hostname="", boot_id="b2", minutes=0):
    return Machine.objects.create(serial=serial, site="welland", hostname=hostname, online=True,
                                  last_seen=timezone.now() + datetime.timedelta(minutes=minutes),
                                  last_boot_id=boot_id)


def found(m, board, variant, where="1-1.4", boot_id="b2"):
    BootEvent.objects.create(machine=m, boot_id=boot_id, stage="fpga-board-found",
                             detail={"board": board, "variant": variant, "where": where}, ts=T0)


def passed(m, boot_id="b2"):
    BootEvent.objects.create(machine=m, boot_id=boot_id, stage="fpga-verified",
                             detail={"result": "pass", "mode": "auto"}, ts=T0)


@pytest.fixture
def c(settings):
    settings.DOMAIN_NAME = "welland.fpgas.online"
    settings.PI_PW = "cGFzc3dvcmQ="
    return Client(HTTP_HOST="welland.fpgas.online")


@pytest.mark.parametrize("board, variant, title", [
    ("acorn", "cle-215+", "Acorn (cle-215+)"),
    ("arty", "a7-35", "Arty A7 (a7-35)"),
    ("netv2", "a7-35", "NeTV2 (a7-35)"),
    ("tt", "tt-fpga", "TT FPGA (tt-fpga)"),
    ("fomu", "evt", "Fomu EVT (evt)"),
    ("acorn", "-", "Acorn"),  # "-": read, and no variant
    ("acorn", "", "Acorn"),
    ("ulx3s", "85f", "ulx3s (85f)"),  # a board this site has no title for: its key, as sent
])
def test_board_title(board, variant, title):
    assert board_title({"board": board, "variant": variant}) == title


@pytest.mark.django_db
def test_found_boards_are_this_boots_only():
    m = machine("now")
    found(m, "acorn", "cle-215+", where="0001:01:00.0", boot_id="b1")
    found(m, "arty", "a7-35")
    found(machine("last-boot", boot_id="b9"), "arty", "a7-35", boot_id="b8")
    machine("none")
    assert by_serial(found_boards()) == {"now": [{"board": "arty", "variant": "a7-35", "where": "1-1.4"}]}


@pytest.mark.django_db
def test_found_boards_one_per_place_newest_first_found_order_kept():
    m = machine("two")
    found(m, "arty", "a7-35", where="1-1.4")
    found(m, "tt", "tt-fpga", where="1-1.2")
    found(m, "arty", "a7-100", where="1-1.4")  # the check ran again this boot
    assert by_serial(found_boards()) == {"two": [{"board": "arty", "variant": "a7-100", "where": "1-1.4"},
                                      {"board": "tt", "variant": "tt-fpga", "where": "1-1.2"}]}


@pytest.mark.django_db
def test_found_boards_ignore_a_detail_that_is_not_a_board():
    """The fleet broker is open on the site LAN: anyone on a Pi can publish."""
    m = machine("odd")
    for detail in ("acorn", {"variant": "x"}, {"board": 7}, {"board": ""}):
        BootEvent.objects.create(machine=m, boot_id="b2", stage="fpga-board-found",
                                 detail=detail, ts=T0)
    assert by_serial(found_boards()) == {}


@pytest.mark.django_db
def test_the_list_names_the_board_each_port_found(c):
    verified_pi("pi-sw2-p46", ("acorn", "cle-215+"))
    verified_pi("pi-sw2-p47")  # passed, but found nothing this boot
    html = c.get("/fpgas/").content.decode()
    assert "pi-sw2-p46</h1>Acorn (cle-215+)" in html
    assert "pi-sw2-p47</h1></td>" in html


@pytest.mark.django_db
def test_the_label_follows_the_board_to_its_new_port(c):
    # the Pi that moved away still holds its old hostname's registration; the
    # newest Pi registered with a hostname speaks for that port
    old = verified_pi("pi-sw2-p46", ("acorn", "cle-215+"), serial="moved")
    Machine.objects.filter(pk=old.pk).update(last_seen=timezone.now() - datetime.timedelta(minutes=1))
    verified_pi("pi-sw2-p46", ("arty", "a7-35"), serial="here")
    html = c.get("/fpgas/").content.decode()
    assert "Arty A7 (a7-35)" in html and "Acorn" not in html


@pytest.mark.django_db
def test_the_board_page_names_the_board(c):
    verified_pi("pi-sw2-p46", ("acorn", "cle-215+"))
    html = c.get("/fpgas/pi-sw2-p46.html").content.decode()
    assert "Accessing pi-sw2-p46 &mdash; Acorn (cle-215+)</h1>" in html


@pytest.mark.django_db
def test_two_boards_on_one_pi_are_both_named(c):
    verified_pi("pi-sw2-p9", ("arty", "a7-35"), ("tt", "tt-fpga"))
    assert "Arty A7 (a7-35), TT FPGA (tt-fpga)" in c.get("/fpgas/").content.decode()


@pytest.mark.django_db
def test_the_tt_page_needs_a_tt_board_found_on_port_21(c):
    verified_pi("pi-sw2-p21", ("arty", "a7-35"))
    assert c.get("/fpgas/tt.html").status_code == 404
    verified_pi("pi-sw1-p21", ("tt", "tt-fpga"))
    assert c.get("/fpgas/tt.html").status_code == 200
