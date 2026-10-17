"""A Pi's own page carries its switch port as the switch port dashboard shows it (round 6 item 6, Tim, 18 Oct 2026):
"The RPi status page @ https://welland.fpgas.online/fleet/00000000cc479fd1/ should have the same set of data as shown
for the port on the switches dashboard, IE badges (link, poe state, poe control), info (In / Out / Err), etc. You should
be able to power cycle the PoE power from this page." One source: the same row template, the same builder script, the
same reading."""

import re
from pathlib import Path

import pytest
from django.test import Client
from management.views import switches as page

from tests.test_management_switches import HOSTS, reader, row_of, seat, welland_inventory  # noqa: F401 (fixtures)

SERIAL = "10000000aaaa0001"


def pi_page(serial=SERIAL):
    return Client(HTTP_HOST=HOSTS[0]).get(f"/fleet/{serial}/").content.decode()


@pytest.mark.django_db
@pytest.mark.usefixtures("reader")
def test_the_pi_page_has_its_ports_row_exactly_as_the_dashboard_draws_it():
    seat("pi-sw1-p1", SERIAL, lease=(1, 1), lease_ago=5)
    dashboard = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    html = pi_page()
    assert '<h2 id="switch-port">Switch port</h2>' in html
    # the same cells, word for word: badges, PoE state, the power button, In / Out / Err
    assert row_of(html, 1) == row_of(dashboard, 1)
    row = row_of(html, 1)
    for cls in ("c-link", "c-poe", "c-power", "c-rx", "c-tx", "c-rx-errors", "c-tx-errors"):
        assert re.search(rf'<td class="[^"]*\b{cls}\b', row), cls
    assert 'class="mgmt-poe-button mgmt-poe-to-off" data-switch="1" data-port="1" data-action="off"' in row
    # the same header, and the section the buttons find their reading's start time in
    assert 'id="switch-1" data-read-from="' in html and 'title="the watts the port delivers">W</th>' in html
    assert '<a href="/management/switches/#switch-1-port-1">Switch 1: ' in html
    assert 'id="mgmt-toasts"' in html


@pytest.mark.django_db
@pytest.mark.usefixtures("reader")
def test_the_pi_page_presses_and_refreshes_with_the_dashboards_scripts():
    seat("pi-sw1-p1", SERIAL, lease=(1, 1))
    html = pi_page()
    head = html.split("</head>")[0]
    for js in ("power-button.js", "port-row.js", "switch-events.js"):
        assert f"management/{js}" in head, js
    script = head.split("<script>")[1].split("</script>")[0]
    assert 'PowerButton.attach(window, {power: "/snmp/power", link: "/snmp/link"}, tick)' in script
    assert 'var url = "/management/switches.json"' in script and "PortRow.fill(tr, found.p, found.s.index)" in script
    assert 'var anchor = "switch-1"' in script and f'serial = "{SERIAL}"' in script
    # the row builder is the dashboard's own file, not a copy
    assert "function fill(tr, p, sw)" not in script
    js = (Path(page.__file__).parents[1] / "static" / "management" / "port-row.js").read_text()
    assert "function fill(tr, p, sw)" in js


@pytest.mark.django_db
@pytest.mark.usefixtures("reader")
def test_a_pi_on_no_port_says_why_and_loads_no_buttons():
    seat("pi-sw1-p1", SERIAL)  # registered, but the gateway has leased it nothing on a port
    html = pi_page()
    assert ("No switch port to show: the gateway has leased this Pi an address on no port of the switches it "
            "reads.") in html
    assert "power-button.js" not in html and "mgmt-poe-button" not in html


@pytest.mark.django_db
def test_switches_that_cannot_be_read_are_said_on_the_pi_page(monkeypatch):
    seat("pi-sw1-p1", SERIAL, lease=(1, 1))
    monkeypatch.setattr(page, "_reader", lambda: None)
    html = pi_page()
    assert "No switch port to show: " in html and "mgmt-poe-button" not in html
