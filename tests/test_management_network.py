"""A Pi's uplink as its newest network-state record says it: the registry.json "network" and the host page's Network
section (the record is fleet.services.boot_event's, stored as it came)."""

import datetime
import re

import pytest
from django.test import Client
from django.utils import timezone
from fleet.models import BootEvent, GatewayRecord, Machine

from management import network
from tests.test_management_fpgas import REAL, pi

WELLAND = "welland.fpgas.online"
NOW = None  # set per test by the now fixture


@pytest.fixture(autouse=True)
def now(monkeypatch):
    """NOW for this test: the doc() and net_event() times are relative to it, not to when the module was imported."""
    moment = timezone.now().replace(microsecond=0)
    monkeypatch.setitem(globals(), "NOW", moment)
    return moment


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


def gateway_ack(machine, acked_at, minutes=0, kind="lease", **doc_changes):
    """A gateway record about `machine`: a lease record whose DHCPACK was at `acked_at` (a datetime or a string)."""
    if isinstance(acked_at, datetime.datetime):
        acked_at = acked_at.astimezone(datetime.timezone.utc).isoformat()
    at = NOW + datetime.timedelta(minutes=minutes)
    return GatewayRecord.objects.create(site="welland", kind=kind, hostname="pi-sw2-p43", mac="d8:3a:dd:00:00:01",
                                        at=at, machine=machine,
                                        doc=dict({"acked_at": acked_at}, **doc_changes))


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
        "last_ack": (NOW - datetime.timedelta(minutes=5)).isoformat(), "last_ack_source": "pi",
        "boot_id": "b1", "current_boot": True, "error": None}


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
def test_the_newest_record_is_the_last_to_arrive_of_the_current_boot_whatever_the_pi_clock_says():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])  # its current boot is b1
    net_event(m, doc(operational_state="degraded"), minutes=500, boot_id="old")  # an old boot, a LATER ts
    net_event(m, doc(operational_state="carrier"), minutes=1, boot_id="b1")
    net_event(m, doc(operational_state="no-carrier"), minutes=-60, boot_id="b1")  # arrived last, older ts
    net = registry()[m.serial]["network"]
    assert (net["state"], net["boot_id"], net["current_boot"]) == ("no-carrier", "b1", True)


@pytest.mark.django_db
def test_a_far_future_ts_does_not_win_forever():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, doc(operational_state="carrier"), minutes=60 * 24 * 365 * 20)
    net_event(m, doc(operational_state="routable"), minutes=1)
    assert registry()[m.serial]["network"]["state"] == "routable"


@pytest.mark.django_db
def test_with_no_record_of_the_current_boot_the_last_to_arrive_of_any_boot_is_said_to_be_an_earlier_boot():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, doc(operational_state="degraded"), minutes=9, boot_id="old1")
    net_event(m, doc(operational_state="carrier"), minutes=1, boot_id="old2")
    net = registry()[m.serial]["network"]
    assert (net["state"], net["boot_id"], net["current_boot"]) == ("carrier", "old2", False)
    section = page(m.serial).split("<h2>Network</h2>", 1)[1].split("<h2>Labels</h2>", 1)[0]
    assert "<code>old2</code> (an earlier boot" in section


@pytest.mark.django_db
def test_a_machine_that_has_said_no_boot_id_falls_back_to_the_last_to_arrive():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    Machine.objects.filter(pk=m.pk).update(last_boot_id="")
    net_event(m, doc(operational_state="carrier"), boot_id="")
    net = registry()[m.serial]["network"]
    assert net["state"] == "carrier" and net["current_boot"] is False


@pytest.mark.django_db
def test_times_are_in_utc_whatever_offset_the_pi_wrote():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    ack = (NOW - datetime.timedelta(minutes=5)).astimezone(datetime.timezone(datetime.timedelta(hours=10, minutes=30)))
    assert ack.isoformat().endswith("+10:30")
    net_event(m, doc(lease=dict(doc()["lease"], acked_at=ack.isoformat())))
    net = registry()[m.serial]["network"]
    assert net["last_ack"] == (NOW - datetime.timedelta(minutes=5)).astimezone(datetime.timezone.utc).isoformat()
    assert net["last_ack"].endswith("+00:00") and net["changed"].endswith("+00:00")


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
    gateway_ack(m, NOW)
    few = count()
    for n in range(3):
        other = pi(f"pi-sw2-p{50 + n}", REAL["pi-sw2-p47"], serial=f"10000000aaaa000{n}")
        net_event(other, doc(), minutes=n)
        net_event(other, doc(operational_state="carrier"), minutes=n + 1)
        gateway_ack(other, NOW, minutes=n)
        gateway_ack(other, NOW, minutes=n + 1)
    gateway_ack(pi("pi-sw2-p60", serial="10000000aaaa0009"), NOW)  # known only from the gateway
    assert count() == few


def addr(**changes):
    return doc(addresses=[dict(doc()["addresses"][0], **changes)])


def route(**changes):
    return doc(default_routes=[dict(doc()["default_routes"][0], **changes)])


def lease(**changes):
    return doc(lease=dict(doc()["lease"], **changes))


def without(*keys):
    return {k: v for k, v in doc().items() if k not in keys}


def without_in(key, name):
    row = {k: v for k, v in doc()[key][0].items() if k != name}
    return doc(**{key: [row]})


BAD = {
    "not an object": lambda: "text",
    "no schema": lambda: without("schema"),
    "schema 2": lambda: doc(schema=2),
    "schema true": lambda: doc(schema=True),
    "no interface": lambda: without("interface"),
    "empty interface": lambda: doc(interface=""),
    "interface int": lambda: doc(interface=3),
    "empty mac": lambda: doc(mac=""),
    "mac null": lambda: doc(mac=None),
    "no hostname": lambda: without("hostname"),
    "state int": lambda: doc(operational_state=3),
    "state upper": lambda: doc(operational_state="Routable"),
    "state long": lambda: doc(administrative_state="a" * 33),
    "state html": lambda: doc(operational_state="<b>"),
    "state empty": lambda: doc(administrative_state=""),
    "addresses not list": lambda: doc(addresses="x"),
    "address not object": lambda: doc(addresses=["x"]),
    "address family 5": lambda: addr(family=5),
    "address family str": lambda: addr(family="4"),
    "address empty": lambda: addr(address=""),
    "address int": lambda: addr(address=4),
    "prefixlen str": lambda: addr(prefixlen="16"),
    "prefixlen bool": lambda: addr(prefixlen=True),
    "scope int": lambda: addr(scope=1),
    "valid negative": lambda: addr(valid_lifetime_s=-1),
    "preferred str": lambda: addr(preferred_lifetime_s="1"),
    "address lacks scope": lambda: without_in("addresses", "scope"),
    "routes not list": lambda: doc(default_routes={}),
    "route family 0": lambda: route(family=0),
    "gateway int": lambda: route(gateway=1),
    "metric str": lambda: route(metric="1"),
    "protocol null": lambda: route(protocol=None),
    "route lacks metric": lambda: without_in("default_routes", "metric"),
    "no lease": lambda: without("lease"),
    "lease null": lambda: doc(lease=None),
    "lease lifetime str": lambda: lease(lifetime_s="1"),
    "lease address int": lambda: lease(address=1),
    "lease server list": lambda: lease(server=[]),
    "lease lacks expires": lambda: doc(lease={k: v for k, v in doc()["lease"].items() if k != "expires"}),
    "serial empty": lambda: doc(serial=""), "hostname int": lambda: doc(hostname=3), "switch negative": lambda: doc(switch=-1),
    "port str": lambda: doc(port="43"), "at null": lambda: doc(at=None), "at no offset": lambda: doc(at="2026-10-14T10:00:00"),
    "trigger upper": lambda: doc(trigger="Routable"), "trigger int": lambda: doc(trigger=1),
    "65 addresses": lambda: doc(addresses=[doc()["addresses"][0]] * 65),
    "65 routes": lambda: doc(default_routes=[doc()["default_routes"][0]] * 65),
    "long hostname": lambda: doc(hostname="h" * 257), "long scope": lambda: addr(scope="s" * 257),
    "long lease server": lambda: lease(server="s" * 257), "long unknown key value": lambda: doc(extra="e" * 257),
    "ipv4 prefixlen 33": lambda: addr(prefixlen=33), "ipv4 prefixlen -1": lambda: addr(prefixlen=-1),
    "ipv6 prefixlen 129": lambda: addr(family=6, address="fe80::1", prefixlen=129),
    "huge integer": lambda: addr(prefixlen=10 ** 100),
    "acked_at text": lambda: lease(acked_at="yesterday"),
    "acked_at no offset": lambda: lease(acked_at="2026-10-14T10:00:00"),
}


@pytest.mark.django_db
@pytest.mark.parametrize("bad", BAD.values(), ids=BAD.keys())
def test_a_record_that_is_not_schema_1_is_an_error_not_a_guess(bad):
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    event = net_event(m, bad())
    with pytest.raises(ValueError, match=f"network-state event {event.pk}"):
        network.parse(event, True)


@pytest.mark.django_db
def test_a_json_null_is_no_value_and_is_accepted():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    nulls = doc(switch=None, port=None, addresses=[dict(doc()["addresses"][0], scope=None, valid_lifetime_s=None)],
                default_routes=[dict(doc()["default_routes"][0], gateway=None)], lease=dict.fromkeys(network.LEASE_KEYS))
    assert network.parse(net_event(m, nulls), True).last_ack is None


@pytest.mark.django_db
def test_a_boundary_document_is_accepted():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    full = doc(addresses=[dict(doc()["addresses"][0], prefixlen=32)] * 64 + [], hostname="h" * 256)
    full["addresses"][-1] = dict(full["addresses"][-1], family=6, address="fe80::1", prefixlen=128)
    assert len(network.parse(net_event(m, full), True).addresses) == 64


@pytest.mark.django_db
def test_a_large_pi_supplied_value_is_not_repr_d_whole():
    assert network._r("x" * 10_000_000).endswith("…") and len(network._r("x" * 10_000_000)) <= network.SHOWN
    assert network._r(["x"] * 1000) == "a list of 1000"
    assert network._r(10 ** 5000) == "an integer of 16610 bits"


@pytest.mark.django_db
def test_a_pi_supplied_value_in_an_error_is_cut_short():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    event = net_event(m, doc(interface="x" * 5000))
    with pytest.raises(ValueError) as caught:
        network.parse(event, True)
    assert len(str(caught.value)) < 250 and "…" in str(caught.value)


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
    assert re.search(r"<time [^>]*>5 min ago</time>", section)  # the Pi's own last ACK
    assert "<code>b1</code> (this boot)" in section
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
    net_event(m, doc(interface=evil, mac=evil,
                     addresses=[{"family": 4, "address": evil, "prefixlen": 16, "scope": evil,
                                 "valid_lifetime_s": None, "preferred_lifetime_s": None}],
                     default_routes=[{"family": 4, "gateway": evil, "metric": 0, "protocol": evil}],
                     lease=dict(doc()["lease"], address=evil, server=evil)))
    html = page(m.serial)
    section = html.split("<h2>Network</h2>", 1)[1].split("<h2>Labels</h2>", 1)[0]
    assert "<script>" not in section and section.count("&lt;script&gt;alert(1)&lt;/script&gt;") == 8


@pytest.mark.django_db
def test_a_malformed_record_is_said_in_the_section_and_the_page_stands():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, {"operational_state": "<b>"})
    html = page(m.serial)
    assert "The newest network-state record cannot be shown: network-state event" in html
    assert "<h2 id=\"boards\">Boards</h2>" in html


@pytest.mark.django_db
def test_the_four_network_tables_are_blocks_of_one_side_by_side_row():
    # Tim, 16 Oct 2026: "the network sections should sit next to each other if there is space" (#155)
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, doc())
    section = page(m.serial).split("<h2>Network</h2>", 1)[1].split("<h2>Labels</h2>", 1)[0]
    row = re.search(r'<div class="mgmt-side">(.*)</div>', section, re.S)
    assert row
    blocks = re.findall(r"<section>\s*<h3>([^<]+)</h3>", row[1])
    assert blocks == ["Interface and record", "Addresses", "Default routes", "DHCP lease"]
    assert row[1].count("<section>") == row[1].count("</section>") == 4


@pytest.mark.django_db
def test_the_events_table_lists_the_records_of_this_boot():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, doc())
    events = page(m.serial).split("<h2>Events of this boot</h2>", 1)[1]
    # the detail is collapsed (#132: no page of JSON to read), the whole of it inside
    row = re.search(r'<tr class="mgmt-ev-network"><td>[^<]*</td><td title="[^"]*"><span class="mgmt-ev">network</span>'
                    r"</td><td>network-state</td><td>–</td><td[^>]*>.*?</td><td><details><summary>detail</summary>"
                    r"<code>(.*?)</code></details></td></tr>", events)
    assert row and "&quot;operational_state&quot;: &quot;routable&quot;" in row[1]


@pytest.mark.django_db
def test_one_machines_malformed_record_is_its_own_error_in_the_registry(caplog):
    good = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    bad = pi("pi-sw2-p47", REAL["pi-sw2-p47"], serial="10000000bbbb0001")
    net_event(good, doc())
    event = net_event(bad, {"operational_state": "<b>"})
    with caplog.at_level("WARNING"):
        entries = registry()
    assert entries[good.serial]["network"]["state"] == "routable"
    error = entries[bad.serial]["network"]
    assert set(error) == set(network.ENTRY_KEYS) | {"error"}
    assert all(v is None for k, v in error.items() if k != "error")
    assert f"network-state event {event.pk}" in error["error"]
    assert any(f"network-state event {event.pk}" in r.getMessage() and r.levelname == "WARNING"
               for r in caplog.records)


@pytest.mark.django_db
def test_the_host_page_reads_its_record_in_a_constant_number_of_queries():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    def count(serial):
        with CaptureQueriesContext(connection) as queries:
            page(serial)
        return len(queries)

    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, doc())
    one = count(m.serial)
    for n in range(5):
        net_event(m, doc(operational_state="carrier"), minutes=n + 1)
    assert count(m.serial) == one


@pytest.mark.django_db
def test_a_value_in_the_error_message_is_escaped_on_the_page():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, doc(operational_state="<script>alert(1)</script>"))
    section = page(m.serial).split("<h2>Network</h2>", 1)[1].split("<h2>Labels</h2>", 1)[0]
    assert "<script>" not in section and "&lt;script&gt;alert(1)&lt;/script&gt;" in section


def with_old_boot_after(m):
    """The current boot's record, then an old boot's that arrived LATER: higher id, later ts, other state."""
    net_event(m, doc(operational_state="routable"), minutes=0, boot_id="b1")
    net_event(m, doc(operational_state="degraded"), minutes=500, boot_id="old")


@pytest.mark.django_db
def test_the_current_boot_wins_over_an_old_boots_later_arrival_in_both_paths():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    with_old_boot_after(m)
    for net in (network.newest()[m.serial], network.of(m)):
        assert (net.operational_state, net.event.boot_id, net.current_boot) == ("routable", "b1", True)


@pytest.mark.django_db
def test_of_falls_back_to_the_last_to_arrive_of_any_boot_and_says_it_is_not_this_boot():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, doc(operational_state="degraded"), minutes=9, boot_id="old1")
    net_event(m, doc(operational_state="carrier"), minutes=1, boot_id="old2")
    net = network.of(m)
    assert (net.operational_state, net.event.boot_id, net.current_boot) == ("carrier", "old2", False)
    assert network.of(pi("pi-sw2-p47", REAL["pi-sw2-p47"], serial="10000000bbbb0002")) is None


@pytest.mark.django_db
def test_of_with_an_empty_last_boot_id_takes_the_last_to_arrive():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    Machine.objects.filter(pk=m.pk).update(last_boot_id="")
    m.refresh_from_db()
    net_event(m, doc(operational_state="degraded"), boot_id="")
    net_event(m, doc(operational_state="carrier"), boot_id="old")
    net = network.of(m)
    assert (net.operational_state, net.current_boot) == ("carrier", False)


@pytest.mark.django_db
def test_a_record_sent_without_a_boot_id_has_an_empty_boot_id_in_the_registry():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, doc(), boot_id="")
    net = registry()[m.serial]["network"]
    assert net["boot_id"] == "" and net["current_boot"] is False


@pytest.mark.django_db
def test_nulls_are_dashes_on_the_page():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, doc(
        addresses=[dict(doc()["addresses"][0], scope=None, valid_lifetime_s=None, preferred_lifetime_s=None)],
        default_routes=[dict(doc()["default_routes"][0], gateway=None)]))
    section = page(m.serial).split("<h2>Network</h2>", 1)[1].split("<h2>Labels</h2>", 1)[0]
    assert "None" not in section
    assert "<td>IPv4</td><td>–</td><td>1024</td><td>dhcp</td>" in section
    assert "<code>10.21.2.43/16</code></td><td>–</td><td>forever</td><td>forever</td>" in section


# --- the gateway's half of last_ack ------------------------------------------------------------------------------------

PLUS_10_30 = datetime.timezone(datetime.timedelta(hours=10, minutes=30))


def ack_of(m):
    net = registry()[m.serial]["network"]
    return net["last_ack"], net["last_ack_source"]


def iso(moment):
    return moment.astimezone(datetime.timezone.utc).isoformat()


@pytest.mark.django_db
def test_the_pi_ack_is_newer_than_the_gateways():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, doc())  # the Pi's ack: 5 minutes ago
    gateway_ack(m, NOW - datetime.timedelta(minutes=30))
    assert ack_of(m) == (iso(NOW - datetime.timedelta(minutes=5)), "pi")


@pytest.mark.django_db
def test_the_gateway_ack_is_newer_than_the_pis():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, doc())
    gateway_ack(m, NOW - datetime.timedelta(minutes=1))
    assert ack_of(m) == (iso(NOW - datetime.timedelta(minutes=1)), "gateway")
    net = registry()[m.serial]["network"]
    assert net["state"] == "routable" and net["boot_id"] == "b1"  # the record's own fields stay the Pi's


@pytest.mark.django_db
def test_the_pi_alone_is_the_source_pi_and_neither_is_null():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, doc())
    assert ack_of(m) == (iso(NOW - datetime.timedelta(minutes=5)), "pi")
    lease = dict(doc()["lease"], acked_at=None)
    net_event(m, doc(lease=lease), minutes=1)
    assert ack_of(m) == (None, None)


@pytest.mark.django_db
def test_a_machine_known_only_from_the_gateway_has_the_one_shape_with_null_fields():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    gateway_ack(m, NOW - datetime.timedelta(minutes=2))
    assert registry()[m.serial]["network"] == {
        "state": None, "administrative_state": None, "changed": None,
        "last_ack": iso(NOW - datetime.timedelta(minutes=2)), "last_ack_source": "gateway", "boot_id": None,
        "current_boot": None, "error": None}
    section = page(m.serial).split("<h2>Network</h2>", 1)[1].split("<h2>Labels</h2>", 1)[0]
    assert "This host has sent no network-state record." in section and "2 min ago" in section


@pytest.mark.django_db
def test_acks_are_compared_as_utc_instants_whatever_their_offset():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    pi_ack = (NOW - datetime.timedelta(minutes=5)).astimezone(PLUS_10_30)
    net_event(m, doc(lease=dict(doc()["lease"], acked_at=pi_ack.isoformat())))
    # the gateway's string sorts after the Pi's as text, but is the same instant minus a minute: older
    older = (NOW - datetime.timedelta(minutes=6)).astimezone(datetime.timezone(datetime.timedelta(hours=-5)))
    gateway_ack(m, older.isoformat())
    assert ack_of(m) == (iso(NOW - datetime.timedelta(minutes=5)), "pi")
    newer = (NOW - datetime.timedelta(minutes=4)).astimezone(PLUS_10_30)
    gateway_ack(m, newer.isoformat(), minutes=1)
    assert ack_of(m) == (iso(NOW - datetime.timedelta(minutes=4)), "gateway")


@pytest.mark.django_db
def test_a_gateway_record_of_no_registered_machine_is_never_counted():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    gateway_ack(None, NOW)
    assert registry()[m.serial]["network"] is None
    net_event(m, doc())
    assert ack_of(m) == (iso(NOW - datetime.timedelta(minutes=5)), "pi")


@pytest.mark.django_db
def test_only_lease_records_with_an_ack_count():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    gateway_ack(m, NOW, kind="port")
    GatewayRecord.objects.create(site="welland", kind="lease", at=NOW, machine=m, doc={"acked_at": None})
    assert registry()[m.serial]["network"] is None


@pytest.mark.django_db
@pytest.mark.parametrize("bad", ["yesterday", "2026-10-14T10:00:00", 5])
def test_a_malformed_gateway_ack_is_that_machines_error(bad):
    good = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    broken = pi("pi-sw2-p47", REAL["pi-sw2-p47"], serial="10000000bbbb0003")
    net_event(good, doc())
    net_event(broken, doc())
    record = gateway_ack(broken, bad)
    entries = registry()
    assert entries[good.serial]["network"]["error"] is None
    error = entries[broken.serial]["network"]
    assert set(error) == set(network.ENTRY_KEYS) | {"error"} and error["state"] is None
    assert f"gateway lease record {record.pk}" in error["error"]
    assert "gateway lease record" in page(broken.serial)


@pytest.mark.django_db
def test_the_page_shows_the_source_next_to_the_last_ack():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    net_event(m, doc())
    section = page(m.serial).split("<h2>Network</h2>", 1)[1].split("<h2>Labels</h2>", 1)[0]
    assert "5 min ago</time> (seen by the pi)" in section
    gateway_ack(m, NOW - datetime.timedelta(minutes=1))
    section = page(m.serial).split("<h2>Network</h2>", 1)[1].split("<h2>Labels</h2>", 1)[0]
    assert "1 min ago</time> (seen by the gateway)" in section


@pytest.mark.django_db
@pytest.mark.parametrize("extra", [
    [],
    [dict(minutes=5, acked_at=None)],  # newer records whose acked_at is JSON null
    [dict(minutes=5, missing=True)],  # ... or absent
    [dict(minutes=5, empty=True)],  # ... or whose doc is empty
    [dict(minutes=5, acked_at=None), dict(minutes=6, missing=True), dict(minutes=7, empty=True)],
    [dict(minutes=0, acked_at="same-at")],  # the same `at`: the higher id wins
], ids=["alone", "null", "missing", "empty", "all three", "same at"])
def test_the_registry_and_the_page_pick_the_same_gateway_record(extra):
    from fleet.services import latest_gateway_ack

    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    oldest = NOW - datetime.timedelta(minutes=30)
    gateway_ack(m, oldest, minutes=0)
    for case in extra:
        at = case["minutes"]
        if case.get("acked_at") == "same-at":
            gateway_ack(m, NOW - datetime.timedelta(minutes=20), minutes=0)  # same `at`, later id, other ack
        elif case.get("missing"):
            GatewayRecord.objects.create(site="welland", kind="lease", at=NOW + datetime.timedelta(minutes=at),
                                         machine=m, doc={"other": 1})
        elif case.get("empty"):
            GatewayRecord.objects.create(site="welland", kind="lease", at=NOW + datetime.timedelta(minutes=at),
                                         machine=m, doc={})
        else:
            gateway_ack(m, None, minutes=at)
    chosen = latest_gateway_ack(m)
    expected = iso(NOW - datetime.timedelta(minutes=20 if extra and extra[0].get("acked_at") == "same-at" else 30))
    assert utc_of(chosen) == expected
    assert ack_of(m) == (expected, "gateway")
    assert network.of(m).last_ack.astimezone(datetime.timezone.utc).isoformat() == expected
    assert network.newest()[m.serial].last_ack.astimezone(datetime.timezone.utc).isoformat() == expected


def utc_of(record):
    return iso(datetime.datetime.fromisoformat(record.doc["acked_at"]))


@pytest.mark.django_db
def test_equal_instants_in_different_offsets_are_a_tie_and_the_pi_wins():
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    instant = NOW - datetime.timedelta(minutes=5)
    net_event(m, doc(lease=dict(doc()["lease"], acked_at=instant.astimezone(PLUS_10_30).isoformat())))
    gateway_ack(m, instant.astimezone(datetime.timezone(datetime.timedelta(hours=-5))).isoformat())
    assert ack_of(m) == (iso(instant), "pi")
    assert network.of(m).last_ack_source == "pi"
