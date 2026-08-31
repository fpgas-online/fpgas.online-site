"""Pi model network identity and the welland /fpgas/ board pages.

Two addressing schemes exist. Welland rows set ``switch`` and follow the
VLAN-per-port scheme (hostname pi-sw<s>-p<p>, ip 10.21.<s>.<p>, gateway ssh
forward <s><pp>22, HLS stream pi-sw<s>-p<p>.m3u8). PS1 rows leave ``switch``
NULL and keep the legacy flat scheme (pi<p>, 10.21.0.<100+p>, <100+p>22,
stream pi<p>.m3u8). Nothing stores an IP: identity derives from (switch, port).
"""

import pytest
from django.core.management import call_command
from django.test import Client
from pibfpgas.models import Pi


@pytest.fixture
def site_settings(settings):
    settings.DOMAIN_NAME = "welland.fpgas.online"
    settings.PI_PW = "cGFzc3dvcmQ="
    return settings


@pytest.fixture
def c(site_settings):
    return Client(HTTP_HOST="welland.fpgas.online")


@pytest.mark.django_db
def test_legacy_scheme_derives_flat_addresses():
    pi = Pi.objects.create(port=9)
    assert pi.hostname == "pi9"
    assert pi.ip == "10.21.0.109"
    assert pi.ssh_port == 10922
    assert pi.stream_url == "/live/pi9.m3u8"


@pytest.mark.django_db
def test_vlan_per_port_scheme_derives_from_switch_and_port():
    pi = Pi.objects.create(port=34, switch=2)
    assert pi.hostname == "pi-sw2-p34"
    assert pi.ip == "10.21.2.34"
    assert pi.ssh_port == 23422
    assert pi.stream_url == "/live/pi-sw2-p34.m3u8"


@pytest.mark.django_db
def test_vlan_per_port_single_digit_port_pads_ssh_port():
    pi = Pi.objects.create(port=3, switch=2)
    # gateway dnat table: 20322 -> 10.21.2.3:22
    assert pi.ssh_port == 20322


@pytest.mark.django_db
def test_home_lists_every_board_with_stream_and_type(c):
    Pi.objects.create(port=38, switch=2, fpga_board="Digilent Arty A7-35T")
    Pi.objects.create(port=46, switch=2, fpga_board="Sqrl Acorn CLE-215+")
    html = c.get("/fpgas/").content.decode()
    assert "pi-sw2-p38" in html and "pi-sw2-p46" in html
    assert "Digilent Arty A7-35T" in html and "Sqrl Acorn CLE-215+" in html
    assert "https://welland.fpgas.online/live/pi-sw2-p38.m3u8" in html
    assert 'href="pi38.html"' in html


@pytest.mark.django_db
def test_board_page_uses_derived_ip_ssh_port_and_stream(c):
    Pi.objects.create(port=42, switch=2, fpga_board="Digilent Arty A7-35T")
    html = c.get("/fpgas/pi42.html").content.decode()
    assert "hostname=10.21.2.42" in html  # wssh iframe
    assert "-p 24222" in html  # direct ssh instructions
    assert "https://welland.fpgas.online/live/pi-sw2-p42.m3u8" in html


@pytest.mark.django_db
def test_board_page_legacy_rows_keep_old_addresses(c):
    Pi.objects.create(port=9)
    html = c.get("/fpgas/pi9.html").content.decode()
    assert "hostname=10.21.0.109" in html
    assert "-p 10922" in html


@pytest.mark.django_db
def test_welland_fixture_loads_from_the_installed_app():
    # loaddata by bare name proves the fixture ships inside the app package
    # (the infra role loads it exactly this way after a fresh converge)
    call_command("loaddata", "fpgas.online.json", verbosity=0)
    pis = Pi.objects.all()
    assert pis.count() == 14
    assert all(pi.switch == 2 for pi in pis)
    assert all(pi.fpga_board for pi in pis)
    by_board = {}
    for pi in pis:
        by_board.setdefault(pi.fpga_board.split()[0], []).append(pi.port)
    assert sorted(by_board["Digilent"]) == [16, 37, 38, 42]
    assert sorted(by_board["Sqrl"]) == [29, 43, 44, 46, 47, 48]
    assert sorted(by_board["TT"]) == [33, 34, 35, 36]


@pytest.mark.django_db
def test_ps1_fixture_loads_and_stays_on_legacy_scheme():
    call_command("loaddata", "ps1.fpgas.online.json", verbosity=0)
    assert Pi.objects.count() > 0
    assert all(pi.switch is None for pi in Pi.objects.all())
