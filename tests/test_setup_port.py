"""The setup / config port (Tim, 18 Oct 2026: "The device should never be shown on the front page of fpgas.online (It
should always be marked as failed)."; fpgas.online-infra docs/pi/setup-port.md).

A machine registered as pi-sw<s>-p<SETUP_PORT> is never offered, whatever its own check reported, and every page
that shows its check says failed, reason "setup port". These fail if any page that offers a board, or any producer of
a machine's check state, stops applying the rule, or if a site with no setup port loses its port 1.
"""

import re

import pytest
from django.test import Client
from fleet.services import ATTENTION, OPERATIONAL, on_setup_port, setup_port_state
from management.views.index import _board_reason

from fleet import services
from management import fpga
from tests.fleet_pis import machine, verified_pi, verifying

WELLAND = "welland.fpgas.online"


@pytest.fixture(autouse=True)
def site(settings):
    settings.DOMAIN_NAME = WELLAND
    settings.PI_PW = "cGFzc3dvcmQ="
    settings.MANAGEMENT_TIME_ZONE = "Australia/Adelaide"
    settings.SETUP_PORT = 1
    return settings


@pytest.mark.parametrize("hostname, want", [
    ("pi-sw1-p1", True), ("pi-sw2-p1", True), ("pi-sw2-p1.welland.fpgas.online", True),
    ("pi-sw1-p10", False), ("pi-sw1-p11", False), ("pi-sw1-p2", False), ("pi-sw1-p01", False),
    ("pi1", False), ("", False), (None, False),
])
def test_the_setup_port_is_named_by_the_port_in_the_registered_name(hostname, want):
    assert on_setup_port(hostname) is want


def test_a_site_with_no_setup_port_has_none(site):
    site.SETUP_PORT = None
    assert not on_setup_port("pi-sw1-p1")
    assert setup_port_state("pass", "pi-sw1-p1") == "pass"


@pytest.mark.parametrize("state", ["pass", "verifying", "", "fail", "missing", "unknown"])
def test_its_check_reads_failed_whatever_the_pi_reported(state):
    assert setup_port_state(state, "pi-sw2-p1") == "fail"
    assert setup_port_state(state, "pi-sw2-p2") == state


@pytest.mark.django_db
def test_a_passed_pi_on_the_setup_port_is_offered_nowhere():
    verified_pi("pi-sw1-p1", ("acorn", "cle215+"), serial="setup")
    verified_pi("pi-sw1-p2", ("acorn", "cle215+"), serial="normal")
    assert services.offered_hosts() == {"pi-sw1-p2": "normal"}
    assert set(services.offered_boards()) == {"pi-sw1-p2"}
    assert services.verified_serials() == {"normal"}
    assert services.fpga_states() == {"setup": "fail", "normal": "pass"}
    assert services.fpga_reports()["setup"][0] == "fail"
    by_serial = {m["serial"]: m for m in services.reporting_machines()}
    assert by_serial["setup"]["offered"] is False and by_serial["setup"]["state"] == "fail"
    assert by_serial["normal"]["offered"] is True
    # the board pages: not on the list, no page of its own
    c = Client(HTTP_HOST=WELLAND)
    listing = c.get("/fpgas/").content.decode()
    assert "<h1>pi-sw1-p2</h1>" in listing and "pi-sw1-p1<" not in listing
    assert c.get("/fpgas/pi-sw1-p1.html").status_code == 404


@pytest.mark.django_db
def test_the_dashboards_mark_it_failed_with_the_reason_setup_port():
    verified_pi("pi-sw2-p1", ("acorn", "cle215+"), serial="setup")
    (host,) = [h for h in fpga.hosts() if h.machine.serial == "setup"]
    assert host.setup_port and host.state == "fail" and host.condition == ATTENTION
    assert host.why == "setup port"
    assert _board_reason(host, None) == "setup port"
    c = Client(HTTP_HOST=WELLAND)
    page = c.get("/fleet/setup/").content.decode()
    assert 'title="needs attention: setup port"' in page  # State
    assert "> not offered: setup port</span>" in page  # Offered
    assert re.search(r"> failed, [^<]*: setup port</span>", page)  # Boot check
    fleet = c.get("/management/fpgas/", follow=True).content.decode()
    assert ": setup port" in fleet
    registry = c.get("/fleet/registry.json").json()
    (row,) = [m for m in registry["machines"] if m["serial"] == "setup"]
    assert row["offered"] is False and row["condition"] == "attention" and row["check"]["result"] == "fail"


@pytest.mark.django_db
def test_it_reads_failed_before_its_check_starts_and_while_it_runs():
    machine("quiet", "pi-sw1-p1")
    verifying(machine("busy", "pi-sw2-p1"))
    rows = {h.machine.serial: h for h in fpga.hosts()}
    for serial in ("quiet", "busy"):
        assert rows[serial].state == "fail" and rows[serial].condition == ATTENTION and rows[serial].why == "setup port"
    assert services.fpga_states() == {"busy": "fail"}  # "quiet" sent nothing this boot: offered by no page either
    assert {m["serial"]: m["state"] for m in services.reporting_machines()} == {"quiet": "fail", "busy": "fail"}


@pytest.mark.django_db
def test_moved_off_the_setup_port_it_is_offered_again(site):
    m = verified_pi("pi-sw1-p1", ("acorn", "cle215+"), serial="moved")
    assert services.offered_hosts() == {}
    m.hostname = "pi-sw1-p3"  # its next boot, on another port, registers that port's name
    m.save()
    assert services.offered_hosts() == {"pi-sw1-p3": "moved"}
    (host,) = fpga.hosts()
    assert host.condition == OPERATIONAL and not host.setup_port


@pytest.mark.django_db
def test_with_no_setup_port_port_1_is_an_ordinary_port(site):
    site.SETUP_PORT = None
    verified_pi("pi-sw1-p1", ("acorn", "cle215+"), serial="one")
    assert services.offered_hosts() == {"pi-sw1-p1": "one"}

