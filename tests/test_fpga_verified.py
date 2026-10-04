"""A Pi is offered on /fpgas/ only when it has checked in recently (online,
a status beat within CHECKED_IN_WITHIN) and fpgas-verify's `fpga-verified`
boot event for the boot it is running now says "pass"."""

import datetime

import pytest
from django.test import Client
from django.utils import timezone
from fleet.models import BootEvent, Machine
from fleet.services import CHECKED_IN_WITHIN, checked_in, fpga_states, verified_serials

from tests.fleet_pis import T0, machine, registered, verified, verifying


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
    BootEvent.objects.create(machine=machine("odd", "pi-sw2-p38"), boot_id="b2",
                             stage="fpga-verified", detail="pass", ts=T0)
    verified(machine("good", "pi-sw2-p42"), "pass")
    assert verified_serials() == {"good"}
    html = c.get("/fpgas/").content.decode()
    assert "pi-sw2-p42" in html and "pi-sw2-p38" not in html


@pytest.mark.django_db
def test_nothing_is_offered_that_did_not_register(c):
    assert "Use this FPGA" not in c.get("/fpgas/").content.decode()  # no board card
    assert c.get("/fpgas/pi-sw2-p38.html").status_code == 404


@pytest.mark.django_db
def test_a_pi_that_passed_but_stopped_checking_in_is_not_offered(c):
    for serial, port in (("silent", 38), ("offline", 39), ("beating", 40)):
        verified(machine(serial, f"pi-sw2-p{port}"), "pass")
    late = timezone.now() - CHECKED_IN_WITHIN - datetime.timedelta(seconds=5)
    Machine.objects.filter(serial="silent").update(last_seen=late)  # died, last will unheard
    Machine.objects.filter(serial="offline").update(online=False)  # its last will said so
    assert checked_in() == {"beating"}
    html = c.get("/fpgas/").content.decode()
    assert "pi-sw2-p40" in html
    assert "pi-sw2-p38" not in html and "pi-sw2-p39" not in html
    assert c.get("/fpgas/pi-sw2-p38.html").status_code == 404
    assert c.get("/fpgas/pi-sw2-p39.html").status_code == 404
    assert c.get("/fpgas/pi-sw2-p40.html").status_code == 200


@pytest.mark.django_db
def test_pis_that_did_not_pass_are_not_offered(c):
    verified(machine("good", "pi-sw2-p38"), "pass")
    verified(machine("nofpga", "pi-sw2-p46"), "missing")
    machine("unchecked", "pi-sw2-p16")
    html = c.get("/fpgas/").content.decode()
    assert "pi-sw2-p38" in html
    assert "pi-sw2-p46" not in html and "pi-sw2-p16" not in html
    assert c.get("/fpgas/pi-sw2-p38.html").status_code == 200
    assert c.get("/fpgas/pi-sw2-p46.html").status_code == 404
    assert c.get("/fpgas/pi-sw2-p16.html").status_code == 404


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


@pytest.mark.django_db
def test_a_pi_is_offered_on_the_port_it_registered_and_only_there(c):
    registered("moved", "pi-sw2-p9", "pass")
    html = c.get("/fpgas/").content.decode()
    assert "pi-sw2-p9" in html and "pi-sw2-p37" not in html
    assert c.get("/fpgas/pi-sw2-p9.html").status_code == 200
    assert c.get("/fpgas/pi-sw2-p37.html").status_code == 404


@pytest.mark.django_db
def test_the_newest_pi_on_a_hostname_speaks_for_it(c):
    # the Pi that passed and was unplugged keeps its last result; the one on the
    # port now failed, so the port is not offered
    registered("gone", "pi-sw2-p9", "pass", minutes=0)
    registered("here", "pi-sw2-p9", "fail", minutes=1)
    assert c.get("/fpgas/pi-sw2-p9.html").status_code == 404


@pytest.mark.django_db
def test_a_fully_qualified_hostname_still_names_its_port(c):
    registered("fqdn", "pi-sw2-p9.welland.fpgas.online", "pass")
    assert c.get("/fpgas/pi-sw2-p9.html").status_code == 200


@pytest.mark.django_db
def test_a_flat_site_hostname_names_its_port(c):
    registered("ps1", "pi9", "pass")
    html = c.get("/fpgas/pi9.html").content.decode()
    assert "hostname=10.21.0.109" in html


@pytest.mark.django_db
def test_a_machine_with_no_port_hostname_is_not_offered(c):
    verified(machine("old-agent"), "pass")  # registered no hostname: on no port
    verified(machine("other", "tweed"), "pass")  # not a Pi's name
    assert "Use this FPGA" not in c.get("/fpgas/").content.decode()  # no board card


@pytest.mark.django_db
def test_two_switches_with_the_same_port_are_two_pis(c):
    registered("sw1", "pi-sw1-p10", "pass")
    registered("sw2", "pi-sw2-p10", "pass")
    html = c.get("/fpgas/").content.decode()
    assert 'href="pi-sw1-p10.html"' in html and 'href="pi-sw2-p10.html"' in html
    assert "hostname=10.21.1.10" in c.get("/fpgas/pi-sw1-p10.html").content.decode()


@pytest.mark.django_db
def test_the_tt_page_needs_a_board_on_port_21(c):
    registered("opi", "pi-sw2-p21", "missing")  # an Orange Pi, no FPGA
    assert c.get("/fpgas/tt.html").status_code == 404
