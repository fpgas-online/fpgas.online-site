"""A Pi's network identity, from its registered hostname, and the /fpgas/
board pages.

Two addressing schemes exist. Welland's Pis register pi-sw<s>-p<p>, the
VLAN-per-port scheme (ip 10.21.<s>.<p>, gateway ssh forward <s><pp>22, HLS
stream pi-sw<s>-p<p>.m3u8). PS1's register pi<p>, the legacy flat scheme
(10.21.0.<100+p>, <100+p>22, stream pi<p>.m3u8). Nothing is stored: identity
derives from the hostname.
"""

import pytest
from django.test import Client
from pibfpgas.pis import Pi

from tests.fleet_pis import verified_pi


@pytest.fixture
def site_settings(settings):
    settings.DOMAIN_NAME = "welland.fpgas.online"
    settings.PI_PW = "cGFzc3dvcmQ="
    return settings


@pytest.fixture
def c(site_settings):
    return Client(HTTP_HOST="welland.fpgas.online")


def test_legacy_scheme_derives_flat_addresses():
    pi = Pi.from_hostname("pi9")
    assert (pi.switch, pi.port) == (None, 9)
    assert pi.hostname == "pi9"
    assert pi.ip == "10.21.0.109"
    assert pi.ssh_port == 10922
    assert pi.stream_url == "/live/pi9.m3u8"
    assert pi.whep_url == "/cam/pi9/whep"


def test_vlan_per_port_scheme_derives_from_switch_and_port():
    pi = Pi.from_hostname("pi-sw2-p34")
    assert (pi.switch, pi.port) == (2, 34)
    assert pi.hostname == "pi-sw2-p34"
    assert pi.ip == "10.21.2.34"
    assert pi.ssh_port == 23422
    assert pi.stream_url == "/live/pi-sw2-p34.m3u8"
    # the stream key on the gateway is the hostname, for WHEP as for HLS
    assert pi.whep_url == "/cam/pi-sw2-p34/whep"


def test_vlan_per_port_single_digit_port_pads_ssh_port():
    # gateway dnat table: 20322 -> 10.21.2.3:22
    assert Pi.from_hostname("pi-sw2-p3").ssh_port == 20322


@pytest.mark.parametrize("hostname", [
    "", "tweed", "pi", "pi-sw2", "pi-sw2-p", "opi21", "pi9x",
    # only the one spelling of each port: no alias for another Pi's page
    "pi-sw2-p046", "pi-sw02-p46", "pi09", "pi-sw2-p46\n", "pi9\n", "PI9", "pi-sw2-p０",
])
def test_a_hostname_that_names_no_port_is_no_pi(hostname):
    assert Pi.from_hostname(hostname) is None


@pytest.mark.django_db
def test_an_alias_of_a_port_is_not_listed_twice(c):
    verified_pi("pi-sw2-p46", serial="real")
    verified_pi("pi-sw2-p046", serial="alias")
    assert c.get("/fpgas/").content.decode().count("<h1>pi-sw2-p46</h1>") == 1


@pytest.mark.django_db
def test_home_lists_every_board_with_its_stream(c):
    # the board each names is what its Pi found: tests/test_board_labels.py
    verified_pi("pi-sw2-p38")
    verified_pi("pi-sw2-p46")
    html = c.get("/fpgas/").content.decode()
    assert "pi-sw2-p38" in html and "pi-sw2-p46" in html
    assert "https://welland.fpgas.online/live/pi-sw2-p38.m3u8" in html
    assert 'href="pi-sw2-p38.html"' in html


@pytest.mark.django_db
def test_home_lists_by_switch_then_port(c):
    for host in ("pi-sw2-p9", "pi-sw1-p17", "pi-sw2-p46", "pi-sw1-p10"):
        verified_pi(host)
    html = c.get("/fpgas/").content.decode()
    order = [html.index(f"<h1>{host}</h1>") for host in
             ("pi-sw1-p10", "pi-sw1-p17", "pi-sw2-p9", "pi-sw2-p46")]
    assert order == sorted(order)


@pytest.mark.django_db
def test_board_page_uses_derived_ip_ssh_port_and_stream(c):
    verified_pi("pi-sw2-p42")
    html = c.get("/fpgas/pi-sw2-p42.html").content.decode()
    assert "hostname=10.21.2.42" in html  # wssh iframe
    assert "-p 24222" in html  # direct ssh instructions
    assert "https://welland.fpgas.online/live/pi-sw2-p42.m3u8" in html
    assert "vlc https://welland.fpgas.online/live/pi-sw2-p42.m3u8" in html
    assert 'data-whep-url="/cam/pi-sw2-p42/whep"' in html
    # this vhost's docroot IS the collected static dir (like /dcws.js): there
    # is no /static/ alias here, so /static/js/... 404s and WHEP never starts
    assert 'src="/js/mediamtx-reader.js"' in html
    assert 'src="/js/whep-live.js"' in html
    assert "/static/" not in html
    # /snmp/status and /snmp/toggle need the switch index as well as the port
    assert 'PiStatus("42", 2)' in html
    # the upload form names the board by its hostname
    assert 'action="/pibup/upload?host=pi-sw2-p42"' in html


@pytest.mark.django_db
def test_board_page_legacy_hostnames_keep_old_addresses(c):
    verified_pi("pi9")
    html = c.get("/fpgas/pi9.html").content.decode()
    assert "hostname=10.21.0.109" in html
    assert "-p 10922" in html
    assert 'data-whep-url="/cam/pi9/whep"' in html
    # legacy flat scheme: one switch, so only the port is sent
    assert 'PiStatus("9", null)' in html


@pytest.mark.django_db
def test_the_old_port_only_page_names_are_gone(c):
    verified_pi("pi-sw2-p42")
    assert c.get("/fpgas/pi42.html").status_code == 404
