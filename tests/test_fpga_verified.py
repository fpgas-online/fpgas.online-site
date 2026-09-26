"""FPGAS_REQUIRE_VERIFIED: a Pi is offered on /fpgas/ only when fpgas-verify's
`fpga-verified` boot event for the boot it is running now says "pass"."""

import datetime

import pytest
from django.test import Client
from django.utils import timezone
from fleet.models import BootEvent, Machine
from fleet.services import verified_serials
from pibfpgas.models import Pi

T0 = timezone.now()


def machine(serial, boot_id="b2"):
    return Machine.objects.create(serial=serial, site="welland", last_seen=T0,
                                  last_boot_id=boot_id)


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
