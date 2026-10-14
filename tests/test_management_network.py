"""A Pi's uplink as its newest network-state record says it: the registry.json "network" and the host page's Network
section (the record is fleet.services.boot_event's, stored as it came)."""

import datetime

import pytest
from django.test import Client
from django.utils import timezone
from fleet.models import BootEvent, Machine

from management import network
from tests.test_management_fpgas import REAL, pi

WELLAND = "welland.fpgas.online"
NOW = timezone.now().replace(microsecond=0)


@pytest.fixture(autouse=True)
def site_address(settings):
    settings.DOMAIN_NAME = WELLAND
    settings.MANAGEMENT_TIME_ZONE = "Australia/Adelaide"
    settings.MANAGEMENT_SITE_NAME = "welland"


def doc(**changes):
    """A schema 1 document: the same keys every time."""
    base = {
        "schema": 1, "serial": "285df3f84af242d0", "mac": "d8:3a:dd:00:00:01", "hostname": "pi-sw2-p43", "switch": 2,
        "port": 43, "at": NOW.isoformat(), "interface": "eth0", "trigger": "routable",
        "operational_state": "routable", "administrative_state": "configured",
        "addresses": [
            {"family": 4, "address": "10.21.2.43", "prefixlen": 16, "scope": "global", "valid_lifetime_s": 3600,
             "preferred_lifetime_s": 1800},
            {"family": 6, "address": "fe80::1", "prefixlen": 64, "scope": "link", "valid_lifetime_s": None,
             "preferred_lifetime_s": None}],
        "default_routes": [{"family": 4, "gateway": "10.21.0.1", "metric": 1024, "protocol": "dhcp"}],
        "lease": {"address": "10.21.2.43", "server": "10.21.0.1", "lifetime_s": 7200,
                  "acked_at": (NOW - datetime.timedelta(minutes=5)).isoformat(),
                  "expires": (NOW + datetime.timedelta(hours=2)).isoformat()},
    }
    base.update(changes)
    return base


def net_event(machine, detail, minutes=0, boot_id="b1"):
    return BootEvent.objects.create(machine=machine, boot_id=boot_id, stage="network-state", detail=detail,
                                    ts=NOW + datetime.timedelta(minutes=minutes))


def registry():
    response = Client(HTTP_HOST=WELLAND).get("/fleet/registry.json")
    assert response.status_code == 200
    return {e["serial"]: e for e in response.json()["machines"]}


def page(serial):
    response = Client(HTTP_HOST=WELLAND).get(f"/fleet/{serial}/")
    assert response.status_code == 200
    return response.content.decode()


# --- registry.json ---------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_registry_entry_has_the_network_of_its_newest_record():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, doc())
    entry = registry()[m.serial]
    assert entry["network"] == {
        "state": "routable", "administrative_state": "configured", "changed": NOW.isoformat(),
        "last_ack": (NOW - datetime.timedelta(minutes=5)).isoformat(), "last_ack_source": "pi"}


@pytest.mark.django_db
def test_a_machine_with_no_record_has_a_null_network():
    pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    assert next(iter(registry().values()))["network"] is None


@pytest.mark.django_db
def test_a_record_with_no_ack_has_a_null_last_ack():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    lease = dict(doc()["lease"], acked_at=None, expires=None)
    net_event(m, doc(lease=lease, operational_state="no-carrier", administrative_state="configuring"))
    network_ = registry()[m.serial]["network"]
    assert (network_["state"], network_["administrative_state"], network_["last_ack"]) == (
        "no-carrier", "configuring", None)


@pytest.mark.django_db
def test_the_newest_record_is_the_highest_ts_of_any_boot_ties_by_id():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, doc(operational_state="degraded"), minutes=5, boot_id="old")  # older boot, newer ts than the first
    net_event(m, doc(operational_state="carrier"), minutes=1, boot_id="b1")  # this boot, but older
    assert registry()[m.serial]["network"]["state"] == "degraded"
    net_event(m, doc(operational_state="no-carrier"), minutes=5, boot_id="b1")  # same ts: the later id
    assert registry()[m.serial]["network"]["state"] == "no-carrier"
    # the record of the id order does not beat a newer ts
    net_event(m, doc(operational_state="routable"), minutes=-60, boot_id="b1")
    assert registry()[m.serial]["network"]["state"] == "no-carrier"


@pytest.mark.django_db
def test_the_registry_reads_every_machines_record_in_a_constant_number_of_queries():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    def count():
        with CaptureQueriesContext(connection) as queries:
            registry()
        return len(queries)

    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, doc())
    few = count()
    for n in range(3):
        other = pi(f"pi-sw2-p{50 + n}", REAL["pi-sw2-p47"], serial=f"10000000aaaa000{n}")
        net_event(other, doc(), minutes=n)
        net_event(other, doc(operational_state="carrier"), minutes=n + 1)
    assert count() == few


@pytest.mark.django_db
@pytest.mark.parametrize("bad", [
    "text", {"operational_state": 3, "administrative_state": "configured"},
    doc(lease=None), doc(addresses="x"), doc(lease=dict(doc()["lease"], acked_at="yesterday")),
    doc(lease=dict(doc()["lease"], acked_at="2026-10-14T10:00:00")),  # no offset
])
def test_a_malformed_record_is_an_error_not_a_guess(bad):
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    event = net_event(m, bad)
    with pytest.raises(ValueError, match=f"network-state event {event.pk}"):
        network.parse(event)


# --- the host page -----------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_page_says_when_the_host_has_sent_no_record():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    html = page(m.serial)
    assert "<h2>Network</h2>" in html and "<p>This host has sent no network-state record.</p>" in html


@pytest.mark.django_db
def test_the_page_shows_the_newest_record_in_its_tables():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, doc(interface="old0", operational_state="carrier"), minutes=-30)
    net_event(m, doc())
    html = page(m.serial)
    section = html.split("<h2>Network</h2>", 1)[1].split("<h2>Labels</h2>", 1)[0]
    assert "<td>routable / configured</td>" in section and "old0" not in section
    assert "<td>eth0</td>" in section and "<code>d8:3a:dd:00:00:01</code>" in section
    assert f'<time datetime="{NOW.isoformat()}"' in section
    assert "mgmt-striped mgmt-sortable mgmt-addresses" in section
    assert "<td>IPv4</td><td><code>10.21.2.43/16</code></td><td>global</td><td>3600 s</td><td>1800 s</td>" in section
    assert ("<td>IPv6</td><td><code>fe80::1/64</code></td><td>link</td><td>forever</td><td>forever</td>"
            in section)
    assert "<td>IPv4</td><td><code>10.21.0.1</code></td><td>1024</td><td>dhcp</td>" in section
    assert "<th scope=\"row\">Server</th><td>10.21.0.1</td>" in section
    assert "<th scope=\"row\">Lifetime</th><td>7200 s</td>" in section
    assert "5 min ago" in section  # the Pi's own last ACK
    assert "ACDT" in section or "ACST" in section  # the lease expiry, in the site's zone


@pytest.mark.django_db
def test_a_record_with_no_lease_and_no_routes_says_so_with_dashes():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    lease = dict.fromkeys(network.LEASE_KEYS)
    net_event(m, doc(lease=lease, addresses=[], default_routes=[], operational_state="no-carrier"))
    section = page(m.serial).split("<h2>Network</h2>", 1)[1].split("<h2>Labels</h2>", 1)[0]
    assert "The record lists no address." in section and "The record lists no default route." in section
    assert '<th scope="row">Address</th><td>–</td>' in section and '<th scope="row">Expires</th><td>–</td>' in section


@pytest.mark.django_db
def test_values_the_pi_sent_are_escaped():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    evil = "<script>alert(1)</script>"
    net_event(m, doc(interface=evil, mac=evil, operational_state=evil, administrative_state=evil,
                     addresses=[{"family": 4, "address": evil, "prefixlen": 16, "scope": evil,
                                 "valid_lifetime_s": None, "preferred_lifetime_s": None}],
                     default_routes=[{"family": 4, "gateway": evil, "metric": 0, "protocol": evil}],
                     lease=dict(doc()["lease"], address=evil, server=evil)))
    html = page(m.serial)
    section = html.split("<h2>Network</h2>", 1)[1].split("<h2>Labels</h2>", 1)[0]
    assert "<script>" not in section and section.count("&lt;script&gt;alert(1)&lt;/script&gt;") == 10


@pytest.mark.django_db
def test_a_malformed_record_is_said_in_the_section_and_the_page_stands():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, {"operational_state": "<b>"})
    html = page(m.serial)
    assert "The newest network-state record cannot be shown: network-state event" in html
    assert "<h2>Boards</h2>" in html


@pytest.mark.django_db
def test_the_events_table_lists_the_records_of_this_boot():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, doc())
    events = page(m.serial).split("<h2>Events of this boot</h2>", 1)[1]
    assert "<td>network-state</td>" in events
    assert Machine.objects.count() == 1
