"""A Pi's own page shows its kernel console of this boot from the gateway's netconsole receiver (round 6 item 5, Tim,
18 Oct 2026: "What is the state of netconsole logs? They should be appearing on pages like
https://welland.fpgas.online/fleet/00000000cc479fd1/")."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone
from fleet.models import GatewayRecord, Machine
from fleet.services import status

from management import netconsole

SERIAL = "00000000cc479fd1"
HOST = "welland.fpgas.online"


@pytest.fixture
def receiver(tmp_path, settings):
    settings.NETCONSOLE_DIR = str(tmp_path)
    return tmp_path


def pi(uptime_s=600, address="10.21.1.2", leased=True):
    """A Pi whose live beat (fleet.services.status) carried `uptime_s`, leased `address` over IPv4 since its boot."""
    m = Machine.objects.create(serial=SERIAL, site="welland", hostname="pi-sw1-p2", online=True,
                               last_seen=timezone.now(), last_boot_id="b1")
    if uptime_s:
        status(SERIAL, {"online": True, "boot_id": "b1", "uptime_s": uptime_s})
        m.refresh_from_db()
    if leased:
        GatewayRecord.objects.create(site="welland", kind="lease", switch=1, port=2, hostname="pi-sw1-p2",
                                     at=timezone.now() - timedelta(seconds=uptime_s - 5),
                                     doc={"family": 4, "event": "add", "address": address}, machine=m)
    return m


def write(receiver, address, lines):
    (receiver / f"{address}.log").write_text("".join(f"{at.isoformat()} {text}\n" for at, text in lines))


def page():
    return Client(HTTP_HOST=HOST).get(f"/fleet/{SERIAL}/").content.decode()


@pytest.mark.django_db
def test_this_boots_lines_newest_last_and_not_an_earlier_pis_on_the_same_port(receiver):
    m = pi(uptime_s=600)
    booted = m.booted_at
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
    booted = m.booted_at
    write(receiver, "10.21.1.2", [(booted + timedelta(seconds=n), f"line {n}") for n in range(1, 401)])
    c = netconsole.read(m)
    assert c.count == 400 and len(c.lines) == netconsole.LIMIT and c.lines[-1][1] == "line 400"
    assert f"the newest {netconsole.LIMIT} of 400 lines" in page()


@pytest.mark.django_db
def test_a_file_longer_than_the_read_says_earlier_lines_were_not_read(receiver):
    m = pi(uptime_s=3600)
    booted = m.booted_at
    write(receiver, "10.21.1.2", [(booted + timedelta(seconds=n), f"line {n} " + "x" * 50) for n in range(1, 101)])
    c = netconsole.read(m, read_bytes=2000)
    assert c.partial and 0 < c.count < 100 and c.lines[-1][1].startswith("line 100 ")


@pytest.mark.django_db
def test_nothing_received_this_boot_is_said(receiver):
    pi()
    assert "No netconsole lines received this boot" in page()


@pytest.mark.django_db
@pytest.mark.parametrize("kw, words", [
    ({"leased": False}, "the gateway has leased the Pi no IPv4 address"),
])
def test_what_keeps_the_console_from_being_shown_is_said(receiver, kw, words):
    pi(**kw)
    html = page()
    assert f"No kernel console to show: {words}" in html


@pytest.mark.django_db
def test_a_site_with_no_receiver_says_so(settings):
    settings.NETCONSOLE_DIR = ""
    pi()
    html = page()
    # a state of the site, said plainly (welland until its receiver is deployed), not a fault box
    assert "<p>No kernel console: the gateway&#x27;s netconsole receiver is not set up on this site yet.</p>" in html
    assert "NETCONSOLE_DIR" not in html


@pytest.mark.django_db
def test_a_lease_record_without_an_address_is_said(receiver):
    m = pi(leased=False)
    GatewayRecord.objects.create(site="welland", kind="lease", switch=1, port=2, hostname="pi-sw1-p2",
                                 at=timezone.now(), doc={"family": 4}, machine=m)
    assert "No kernel console to show: the gateway&#x27;s newest IPv4 lease record of the Pi" in page()


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


@pytest.mark.django_db
def test_the_boot_start_is_from_a_live_beat_not_from_a_will_or_a_replay(receiver):
    """The review of #174: a will or a shutdown notice moves last_seen with no uptime, a retained replay moves the
    uptime without last_seen; neither moves the boot start."""
    m = pi(uptime_s=600)
    booted = m.booted_at
    status(SERIAL, {"online": False, "boot_id": "b1"})  # the will, a minute later in real life
    status(SERIAL, {"online": True, "boot_id": "b1", "uptime_s": 5}, retained=True)
    m.refresh_from_db()
    assert m.booted_at == booted
    assert netconsole.boot_start(m) == booted - netconsole.SLACK


@pytest.mark.django_db
def test_the_sender_is_the_newest_ipv4_lease_not_an_ipv6_one(receiver):
    m = pi(uptime_s=600)
    GatewayRecord.objects.create(site="welland", kind="lease", switch=1, port=2, hostname="pi-sw1-p2",
                                 at=timezone.now(), doc={"family": 6, "event": "add", "address": "2001:db8::5"},
                                 machine=m)
    write(receiver, "10.21.1.2", [(m.booted_at + timedelta(seconds=1), "Booting Linux on physical CPU 0x0")])
    c = netconsole.read(m)
    assert c.address == "10.21.1.2" and [t for _, t in c.lines] == ["Booting Linux on physical CPU 0x0"]


@pytest.mark.django_db
def test_a_boot_that_straddles_a_rotation_reads_the_rotated_file_too(receiver):
    m = pi(uptime_s=3600)
    booted = m.booted_at
    (receiver / "10.21.1.2.log.1").write_text("".join(f"{at.isoformat()} {t}\n" for at, t in [
        (booted - timedelta(hours=3), "an earlier boot"),
        (booted + timedelta(seconds=1), "Booting Linux on physical CPU 0x0"),
        (booted + timedelta(seconds=2), "Machine model: Raspberry Pi 3 Model B Plus Rev 1.3"),
    ]))
    write(receiver, "10.21.1.2", [(booted + timedelta(minutes=30), "after the rotation")])
    c = netconsole.read(m)
    assert [t for _, t in c.lines] == ["Booting Linux on physical CPU 0x0",
                                       "Machine model: Raspberry Pi 3 Model B Plus Rev 1.3", "after the rotation"]
    assert not c.partial
    # a rotation just after the boot: the current file empty of this boot's lines, the rotated one holds them
    (receiver / "10.21.1.2.log").unlink()
    assert [t for _, t in netconsole.read(m).lines][0] == "Booting Linux on physical CPU 0x0"


@pytest.mark.django_db
def test_a_pi_with_no_boot_start_yet_shows_its_newest_lines_plainly(receiver):
    """The review of #174 round 2: a Pi that has not beaten since booted_at arrived (every Pi for a minute after the
    deploy; one that dies before its first beat) is an expected state, not a fault: its newest lines, said to be
    possibly of earlier boots."""
    m = pi(uptime_s=0)
    assert m.booted_at is None
    now = timezone.now()
    write(receiver, "10.21.1.2", [(now - timedelta(hours=1), "an earlier boot"), (now, "Kernel panic - not syncing")])
    c = netconsole.read(m)
    assert c.since is None and [t for _, t in c.lines] == ["an earlier boot", "Kernel panic - not syncing"]
    html = page()
    assert "this boot's start is not known yet" in html and "Kernel panic - not syncing" in html
    assert "mgmt-problem" not in html.split('id="kernel-console"')[1].split("<h2")[0]


@pytest.mark.django_db
def test_older_lines_in_a_compressed_rotation_are_said(receiver):
    m = pi(uptime_s=3600)
    write(receiver, "10.21.1.2", [(m.booted_at + timedelta(seconds=1), "x")])
    (receiver / "10.21.1.2.log.2.gz").write_bytes(b"")
    assert netconsole.read(m).partial
