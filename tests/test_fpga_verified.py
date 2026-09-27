"""FPGAS_REQUIRE_VERIFIED: a Pi is offered on /fpgas/ only when fpgas-verify's
`fpga-verified` boot event for the boot it is running now says "pass"."""

import datetime

import pytest
from django.test import Client
from django.utils import timezone
from fleet.models import BootEvent, Machine
from fleet.services import fpga_states, verified_serials
from pibfpgas.models import Pi

T0 = timezone.now()


def machine(serial, boot_id="b2"):
    return Machine.objects.create(serial=serial, site="welland", last_seen=T0,
                                  last_boot_id=boot_id)


def verifying(m, boot_id="b2", minutes=0):
    BootEvent.objects.create(machine=m, boot_id=boot_id, stage="fpga-verifying",
                             detail={"started_at": "2026-09-27T06:00:00+00:00"},
                             ts=T0 + datetime.timedelta(minutes=minutes))


def verified(m, result, boot_id="b2", minutes=0):
    BootEvent.objects.create(machine=m, boot_id=boot_id, stage="fpga-verified",
                             detail={"result": result, "mode": "all-boards"},
                             ts=T0 + datetime.timedelta(minutes=minutes))


@pytest.fixture
def c(settings):
    settings.DOMAIN_NAME = "welland.fpgas.online"
    settings.PI_PW = "cGFzc3dvcmQ="
    return Client(HTTP_HOST="welland.fpgas.online")


@pytest.mark.django_db
def test_only_a_pass_in_the_current_boot_counts():
    verified(machine("pass-now"), "pass")
    verified(machine("missing-now"), "missing")
    verified(machine("pass-last-boot"), "pass", boot_id="b1")
    machine("still-checking")
    m = machine("passed-then-failed")
    verified(m, "pass", minutes=0)
    verified(m, "fail", minutes=5)
    m = machine("failed-then-passed")
    verified(m, "fail", minutes=0)
    verified(m, "pass", minutes=5)
    BootEvent.objects.create(machine=machine("other-stage"), boot_id="b2",
                             stage="ssh-up", detail={"result": "pass"}, ts=T0)
    assert verified_serials() == {"pass-now", "failed-then-passed"}


@pytest.mark.django_db
def test_a_detail_that_is_not_a_dict_is_no_pass_and_breaks_nothing(c, settings):
    """The fleet broker is open on the site LAN: anyone on a Pi can publish."""
    settings.FPGAS_REQUIRE_VERIFIED = True
    Pi.objects.create(port=38, switch=2, serial_no="odd", fpga_board="Digilent Arty A7-35T")
    Pi.objects.create(port=42, switch=2, serial_no="good", fpga_board="Digilent Arty A7-35T")
    BootEvent.objects.create(machine=machine("odd"), boot_id="b2", stage="fpga-verified",
                             detail="pass", ts=T0)
    verified(machine("good"), "pass")
    assert verified_serials() == {"good"}
    html = c.get("/fpgas/").content.decode()
    assert "pi-sw2-p42" in html and "pi-sw2-p38" not in html


@pytest.mark.django_db
def test_off_by_default_every_pi_is_offered(c):
    Pi.objects.create(port=38, switch=2, serial_no="aaa", fpga_board="Digilent Arty A7-35T")
    assert "pi-sw2-p38" in c.get("/fpgas/").content.decode()
    assert c.get("/fpgas/pi38.html").status_code == 200


@pytest.mark.django_db
def test_required_hides_pis_that_did_not_pass(c, settings):
    settings.FPGAS_REQUIRE_VERIFIED = True
    Pi.objects.create(port=38, switch=2, serial_no="good", fpga_board="Digilent Arty A7-35T")
    Pi.objects.create(port=46, switch=2, serial_no="nofpga", fpga_board="Sqrl Acorn CLE-215+")
    Pi.objects.create(port=16, switch=2, serial_no="", fpga_board="Digilent Arty A7-35T")
    verified(machine("good"), "pass")
    verified(machine("nofpga"), "missing")
    html = c.get("/fpgas/").content.decode()
    assert "pi-sw2-p38" in html
    assert "pi-sw2-p46" not in html and "pi-sw2-p16" not in html
    assert c.get("/fpgas/pi38.html").status_code == 200
    assert c.get("/fpgas/pi46.html").status_code == 404
    assert c.get("/fpgas/pi16.html").status_code == 404


@pytest.mark.django_db
def test_the_check_is_verifying_until_its_result_follows():
    m = machine("checking")
    verifying(m)
    m = machine("checked")
    verifying(m)
    verified(m, "fail", minutes=5)
    m = machine("rechecking")  # passed, then `systemctl restart fpgas-verify`
    verifying(m)
    verified(m, "pass", minutes=5)
    verifying(m, minutes=10)
    m = machine("started-last-boot")
    verifying(m, boot_id="b1")
    machine("not-started")
    assert fpga_states() == {"checking": "verifying", "checked": "fail", "rechecking": "verifying"}
    assert verified_serials() == set()  # a Pi being checked is not offered


@pytest.mark.django_db
def test_the_newest_event_by_arrival_wins_not_by_the_pis_clock():
    """fpga-verifying goes out early in the boot, often before the Pi's clock is set."""
    m = machine("clock-behind")
    verifying(m, minutes=60)  # stamped by a clock that later stepped back
    verified(m, "pass", minutes=0)
    assert fpga_states() == {"clock-behind": "pass"}
    assert verified_serials() == {"clock-behind"}


@pytest.mark.django_db
def test_the_fleet_pages_show_the_check(c):
    verifying(machine("aaa"))
    m = machine("bbb")
    verifying(m)
    verified(m, "missing", minutes=5)
    machine("ccc")
    html = c.get("/fleet/").content.decode()
    assert 'class="badge verifying">verifying<' in html
    assert 'class="badge offline">missing<' in html
    assert "not started" in html
    assert 'class="badge verifying">verifying<' in c.get("/fleet/aaa/").content.decode()
    assert "not started" in c.get("/fleet/ccc/").content.decode()
