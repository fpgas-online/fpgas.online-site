"""A Pi's own page shows its kernel console of this boot from the gateway's netconsole receiver (round 6 item 5, Tim,
18 Oct 2026: "What is the state of netconsole logs? They should be appearing on pages like
https://welland.fpgas.online/fleet/00000000cc479fd1/")."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone
from fleet.models import GatewayRecord, Machine

from management import netconsole

SERIAL = "00000000cc479fd1"
HOST = "welland.fpgas.online"


@pytest.fixture
def receiver(tmp_path, settings):
    settings.NETCONSOLE_DIR = str(tmp_path)
    return tmp_path


def pi(uptime_s=600, address="10.21.1.2", leased=True):
    m = Machine.objects.create(serial=SERIAL, site="welland", hostname="pi-sw1-p2", online=True,
                               last_seen=timezone.now(), last_boot_id="b1", last_uptime_s=uptime_s)
    if leased:
        GatewayRecord.objects.create(site="welland", kind="lease", switch=1, port=2, hostname="pi-sw1-p2",
                                     at=timezone.now() - timedelta(seconds=uptime_s - 5), doc={"address": address},
                                     machine=m)
    return m


def write(receiver, address, lines):
    (receiver / f"{address}.log").write_text("".join(f"{at.isoformat()} {text}\n" for at, text in lines))


def page():
    return Client(HTTP_HOST=HOST).get(f"/fleet/{SERIAL}/").content.decode()


@pytest.mark.django_db
def test_this_boots_lines_newest_last_and_not_an_earlier_pis_on_the_same_port(receiver):
    m = pi(uptime_s=600)
    booted = m.last_seen - timedelta(seconds=600)
    write(receiver, "10.21.1.2", [
        (booted - timedelta(hours=2), "an earlier Pi on this port"),
        (booted + timedelta(seconds=1), "Booting Linux on physical CPU 0x0"),
        (booted + timedelta(seconds=9), "netconsole: network logging started"),
    ])
    c = netconsole.read(m)
    assert [t for _, t in c.lines] == ["Booting Linux on physical CPU 0x0", "netconsole: network logging started"]
    assert c.count == 2 and not c.partial and c.address == "10.21.1.2"
    html = page()
    assert '<h2 id="kernel-console">Kernel console</h2>' in html
    assert "an earlier Pi on this port" not in html
    assert html.index("Booting Linux on physical CPU 0x0") < html.index("netconsole: network logging started")
    assert "sent by netconsole from 10.21.1.2" in html and "2 lines" in html


@pytest.mark.django_db
def test_the_page_shows_the_newest_lines_and_says_how_many_it_left_out(receiver):
    m = pi(uptime_s=3600)
    booted = m.last_seen - timedelta(seconds=3600)
    write(receiver, "10.21.1.2", [(booted + timedelta(seconds=n), f"line {n}") for n in range(1, 401)])
    c = netconsole.read(m)
    assert c.count == 400 and len(c.lines) == netconsole.LIMIT and c.lines[-1][1] == "line 400"
    assert f"the newest {netconsole.LIMIT} of 400 lines" in page()


@pytest.mark.django_db
def test_a_file_longer_than_the_read_says_earlier_lines_were_not_read(receiver):
    m = pi(uptime_s=3600)
    booted = m.last_seen - timedelta(seconds=3600)
    write(receiver, "10.21.1.2", [(booted + timedelta(seconds=n), f"line {n} " + "x" * 50) for n in range(1, 101)])
    c = netconsole.read(m, read_bytes=2000)
    assert c.partial and 0 < c.count < 100 and c.lines[-1][1].startswith("line 100 ")


@pytest.mark.django_db
def test_nothing_received_this_boot_is_said(receiver):
    pi()
    assert "No netconsole lines received this boot" in page()


@pytest.mark.django_db
@pytest.mark.parametrize("kw, words", [
    ({"uptime_s": 0}, "the Pi has reported no uptime"),
    ({"leased": False}, "the gateway has leased the Pi no address"),
])
def test_what_keeps_the_console_from_being_shown_is_said(receiver, kw, words):
    pi(**kw)
    html = page()
    assert f"No kernel console to show: {words}" in html


@pytest.mark.django_db
def test_a_site_with_no_receiver_says_so(settings):
    settings.NETCONSOLE_DIR = ""
    pi()
    assert "No kernel console to show: this site has no netconsole receiver configured" in page()


@pytest.mark.django_db
def test_a_line_not_in_the_receivers_form_fails_loud(receiver):
    pi()
    (receiver / "10.21.1.2.log").write_text("no time here\n")
    html = page()
    assert "No kernel console to show: the netconsole file holds a line this page cannot read" in html
    assert "line 1 from the end is not" in html


@pytest.mark.django_db
def test_kernel_text_is_escaped(receiver):
    m = pi()
    write(receiver, "10.21.1.2", [(m.last_seen - timedelta(seconds=1), "<script>alert(1)</script>")])
    html = page()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "<script>alert(1)" not in html
