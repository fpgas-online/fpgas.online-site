"""The switch port dashboard (#67): the page and its JSON, on every host, with the reader stubbed.

The reader is `snmp_switch.dashboard` from fpgas.online-poe. Where the installed poe does not have it yet, these
tests use minimal stand-ins with the same fields as its SwitchView and PortView dataclasses."""

import json
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

try:
    from snmp_switch.dashboard import PortView, SwitchView
except ImportError:  # poe without the dashboard module: stand-ins with the same fields

    @dataclass
    class PortView:
        port: int
        label: str | None = None
        link_up: bool = False
        speed_mbps: int | None = None
        poe_state: str | None = None
        poe_watts: float | None = None
        lldp_name: str | None = None
        lldp_port: str | None = None
        lldp_chassis: str | None = None
        macs: list = field(default_factory=list)
        rx_bytes: int | None = None
        tx_bytes: int | None = None
        rx_bps: float | None = None
        tx_bps: float | None = None
        rx_errors: int | None = None
        tx_errors: int | None = None

    @dataclass
    class SwitchView:
        index: int | None
        name: str
        model: str
        reachable: bool
        error: str
        read_at: str
        ports: list = field(default_factory=list)
        good_at: str | None = None

from fleet.models import Machine
from management.views import switches as page

TT = "tinytapeout.fpgas.online"
HOSTS = ["welland.fpgas.online", "ps1.fpgas.online", TT]
COMMUNITY = "s3cret-community-xyzzy"
READ_AT = "2026-10-09T10:00:00+09:30"


def make_views():
    return [SwitchView(
        index=1, name="switch 1", model="GSM7252PS", reachable=True, error="", read_at=READ_AT, good_at=READ_AT,
        ports=[
            PortView(port=1, label="uplink", link_up=True, speed_mbps=1000, poe_state="delivering", poe_watts=3.5,
                     lldp_name="pi-sw1-p1", lldp_port="eth0", macs=["aa:00:00:00:00:01", "aa:00:00:00:00:02",
                                                                    "aa:00:00:00:00:03", "aa:00:00:00:00:04"],
                     rx_bps=250_000, tx_bps=3_400_000, rx_errors=0, tx_errors=2),
            PortView(port=2, link_up=True, speed_mbps=100, poe_state="delivering", poe_watts=1.0,
                     lldp_name="not-a-fleet-host", lldp_port="gi1"),
            PortView(port=3, poe_state="searching"),
        ])]


@pytest.fixture
def reader(monkeypatch):
    views = make_views()
    monkeypatch.setattr(page, "_reader", lambda: lambda cache, *a, **k: views)
    return views


def machine(serial, hostname, seen_ago=0):
    return Machine.objects.create(serial=serial, site="welland", hostname=hostname,
                                  last_seen=timezone.now() - timedelta(minutes=seen_ago))


@pytest.mark.django_db
@pytest.mark.parametrize("host", HOSTS)
def test_the_page_and_json_answer_without_a_login_on_every_host(host, reader):
    c = Client(HTTP_HOST=host)
    r = c.get("/management/switches/")
    assert r.status_code == 200
    html = r.content.decode()
    assert "<title>Switch ports &mdash; Management &mdash; fpgas.online</title>" in html
    assert "&rsaquo; Switch ports" in html
    assert "GSM7252PS" in html and "Times are" in html and "/accounts/login" not in html
    assert 'class="mgmt-scroll"' in html and 'scope="col"' in html and 'scope="row"' in html
    j = c.get("/management/switches.json")
    assert j.status_code == 200 and j["Content-Type"].startswith("application/json")
    assert json.loads(j.content)["switches"][0]["ports"][0]["port"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize("host", HOSTS)
def test_both_urlconfs_resolve_the_names_and_the_index_links_the_page(host, reader):
    c = Client(HTTP_HOST=host)
    index = c.get("/management/").content.decode()
    assert '<a href="/management/switches/">Switch ports</a>' in index
    assert c.get("/management/switches/").request["PATH_INFO"] == "/management/switches/"


def test_the_names_reverse_on_both_urlconfs():
    for conf in ("pib.urls", "ttsite.urls"):
        assert reverse("management-switches", urlconf=conf) == "/management/switches/"
        assert reverse("management-switches-json", urlconf=conf) == "/management/switches.json"


@pytest.mark.django_db
def test_a_port_with_nothing_connected_reads_down_searching_and_empty(reader):
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    row = html.split('<th scope="row">3</th>')[1].split("</tr>")[0]
    assert ">down<" in row and "searching" in row and row.count("<td></td>") >= 2


@pytest.mark.django_db
def test_traffic_is_rates_and_a_dash_when_unknown(reader):
    d = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)["switches"][0]["ports"]
    assert (d[0]["rx"], d[0]["tx"]) == ("250 kbit/s", "3.4 Mbit/s")
    assert (d[2]["rx"], d[2]["tx"]) == ("–", "–") and d[0]["errors"] == "0 / 2"
    assert d[0]["macs"].endswith("+1") and d[0]["macs"].count("aa:") == 3 and d[0]["link"] == "up 1G"


@pytest.mark.django_db
def test_a_fleet_pis_lldp_name_links_to_its_page_and_other_names_do_not(reader):
    machine("10000000aaaa0001", "pi-sw1-p1.welland.example")
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    assert '<a href="/fleet/10000000aaaa0001/">pi-sw1-p1</a> port eth0' in html
    assert "device port gi1" in html and "not-a-fleet-host" not in html  # a machine not in the fleet: masked


@pytest.mark.django_db
def test_the_newest_machine_on_a_hostname_is_the_one_linked(reader):
    machine("10000000aaaa0001", "pi-sw1-p1", seen_ago=60)
    machine("10000000aaaa0002", "pi-sw1-p1", seen_ago=1)
    j = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)
    assert j["switches"][0]["ports"][0]["lldp_url"] == "/fleet/10000000aaaa0002/"


@pytest.mark.django_db
def test_no_fleet_pages_on_tinytapeout_means_no_link(reader):
    machine("10000000aaaa0001", "pi-sw1-p1")
    html = Client(HTTP_HOST=TT).get("/management/switches/").content.decode()
    assert "pi-sw1-p1 port eth0" in html and "/fleet/" not in html


@pytest.mark.django_db
def test_one_query_finds_every_fleet_pi(reader, django_assert_num_queries):
    for n in range(5):
        machine(f"10000000aaaa000{n}", f"pi-sw1-p{n + 1}")
    reader[0].ports[1].lldp_name = "pi-sw1-p2"
    reader[0].ports[2].lldp_name = "pi-sw1-p3"
    with django_assert_num_queries(1):
        Client(HTTP_HOST=HOSTS[0]).get("/management/switches/")


@pytest.mark.django_db
def test_an_unreachable_switch_shows_its_error_and_last_ports(monkeypatch):
    views = make_views()
    views[0].reachable = False
    views[0].error = "not answering since 09:59:45"
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    assert "not answering since 09:59:45" in html and "mgmt-dead" in html
    assert 'th scope="row">1</th>' in html


@pytest.mark.django_db
def test_the_reader_missing_is_a_message_not_a_500(monkeypatch):
    monkeypatch.setattr(page, "_reader", lambda: None)
    c = Client(HTTP_HOST=HOSTS[0])
    r = c.get("/management/switches/")
    assert r.status_code == 200 and "The switch reader is not installed on this site yet." in r.content.decode()
    j = c.get("/management/switches.json")
    assert j.status_code == 503 and json.loads(j.content)["error"].startswith("The switch reader is not installed")


@pytest.mark.django_db
def test_a_config_error_is_shown_as_its_message(monkeypatch):
    from snmp_switch.switches import PoeConfigError

    def read(cache):
        raise PoeConfigError("no switches are configured: set FPGAS_SWITCHES_CONFIG")

    monkeypatch.setattr(page, "_reader", lambda: read)
    r = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/")
    assert r.status_code == 200 and "no switches are configured: set FPGAS_SWITCHES_CONFIG" in r.content.decode()


@pytest.mark.django_db
def test_any_other_reader_failure_is_a_message_not_a_500(monkeypatch):
    def read(cache):
        raise RuntimeError("redis went away")

    monkeypatch.setattr(page, "_reader", lambda: read)
    c = Client(HTTP_HOST=HOSTS[0])
    r = c.get("/management/switches/")
    assert r.status_code == 200 and page.READ_FAILED in r.content.decode()
    assert "redis went away" not in r.content.decode()
    j = c.get("/management/switches.json")
    assert j.status_code == 503 and json.loads(j.content)["error"] == page.READ_FAILED


@pytest.mark.django_db
def test_device_text_is_escaped_in_the_page_and_plain_in_the_json(monkeypatch):
    evil = '<script>alert("x")</script>'
    views = make_views()
    views[0].ports[0].label = evil
    views[0].ports[1].lldp_name = evil
    views[0].ports[1].lldp_port = evil
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    c = Client(HTTP_HOST=HOSTS[0])
    html = c.get("/management/switches/").content.decode()
    assert evil not in html and "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html
    ports = json.loads(c.get("/management/switches.json").content)["switches"][0]["ports"]
    assert ports[0]["label"] == evil and ports[1]["lldp_port"] == evil


@pytest.mark.django_db
def test_read_times_are_local_with_the_zone_named_once(reader, settings):
    settings.MANAGEMENT_TIME_ZONE = "Australia/Adelaide"
    c = Client(HTTP_HOST=HOSTS[0])
    html = c.get("/management/switches/").content.decode()
    assert "2026-10-09 11:00:00" in html and "Times are Australia/Adelaide." in html
    # READ_AT is 10:00 at +09:30; Adelaide is on daylight time (+10:30) on 9 Oct
    assert html.count("Australia/Adelaide") == 1 and "+09:30" not in html
    assert json.loads(c.get("/management/switches.json").content)["switches"][0]["read_at"] == "2026-10-09 11:00:00"
    with timezone.override("UTC"):
        assert page.when("2026-10-08T23:29:19.735562+00:00") == "2026-10-08 23:29:19"
    assert page.when("") == "" and page.when("not a time") == "not a time"


@pytest.mark.django_db
def test_an_lldp_port_id_that_is_a_mac_is_not_repeated(monkeypatch):
    views = make_views()
    views[0].ports[0].lldp_port = "B8:27:EB:E3:E7:E4"
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    ports = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)["switches"][0]["ports"]
    assert ports[0]["lldp_port"] == "" and ports[1]["lldp_port"] == "gi1"


@pytest.mark.django_db
def test_each_switch_is_headed_by_its_installation_number_in_order(monkeypatch):
    one = make_views()[0]
    one.name = "sw-netgear-gsm7252ps-s2"
    two = SwitchView(index=2, name="sw-netgear-s3300-1", model="s3300", reachable=True, error="", read_at=READ_AT)
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: [two, one])  # the reader's order is not trusted
    c = Client(HTTP_HOST=HOSTS[0])
    html = c.get("/management/switches/").content.decode()
    first = "<h2>Switch 1: sw-netgear-gsm7252ps-s2 (GSM7252PS)</h2>"
    second = "<h2>Switch 2: sw-netgear-s3300-1 (S3300)</h2>"
    assert first in html and second in html and html.index(first) < html.index(second)
    titles = [s["title"] for s in json.loads(c.get("/management/switches.json").content)["switches"]]
    assert titles == ["Switch 1: sw-netgear-gsm7252ps-s2 (GSM7252PS)", "Switch 2: sw-netgear-s3300-1 (S3300)"]


def test_a_switch_without_an_index_has_no_number():
    legacy = SwitchView(index=None, name="switch", model="legacy", reachable=False, error="", read_at="")
    assert page.title(legacy) == "Switch: switch (LEGACY)"


@pytest.mark.django_db
def test_watts_are_shown_only_on_a_delivering_port(monkeypatch):
    views = make_views()
    views[0].ports[2].poe_watts = 0.0  # port 3, searching
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    ports = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)["switches"][0]["ports"]
    assert (ports[0]["poe"], ports[0]["poe_title"]) == ("3.5W", "delivering power")
    assert (ports[2]["poe"], ports[2]["poe_title"]) == ("⋯", "searching: PoE on, nothing drawing power")


def test_poe_cells_are_watts_or_one_symbol():
    assert page.poe_cell("delivering", 8.6) == ("8.6W", "delivering power")
    for state in ("delivering", "searching", "disabled", "fault", "other", "something new"):
        text, title = page.poe_cell(state, None)
        assert len(text) == 1 and title and state not in text
    assert page.poe_cell(None, None) == ("", "no PoE on this port")


def test_link_speeds_are_short():
    assert [page.link_speed(m) for m in (10, 100, 1000, 2500, 10000, 20000)] == \
        ["10M", "100M", "1G", "2.5G", "10G", "20G"]


@pytest.mark.django_db
def test_headers_are_short_so_values_set_the_column_widths(reader):
    """Tim, 2026-10-09: a column is as wide as its values, not its header. Each header is no longer than the
    shortest value its column always has (a port number, "up 1G", "0 / 0"); the full names are in <abbr title>."""
    import re

    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    thead = html[html.index("<thead>"):html.index("</thead>")]
    headers = [re.sub(r"<[^>]+>", "", h) for h in re.findall(r'<th scope="col">(.*?)</th>', thead)]
    assert headers == ["#", "Label", "Link", "PoE", "LLDP", "MACs", "In", "Out", "Err"]
    assert 'title="Errors in / out"' in thead and 'title="Port"' in thead


@pytest.fixture
def gateway_host(monkeypatch):
    monkeypatch.setattr(page.socket, "gethostname", lambda: "gwhost")
    monkeypatch.setattr(page.socket, "getfqdn", lambda: "gwhost.example.org")


@pytest.mark.parametrize("given, shown", [
    ("eth-uplink.gwhost", "eth-uplink.gateway"),         # the gateway: its role word
    ("bmc.GWHOST", "bmc.gateway"),
    ("eth-uplink.pi10.fpgas", "eth-uplink"),             # not a fleet hostname: the role alone
    ("uplink.upstreamhost.example.org", "uplink"),       # the upstream host is never named
    ("1/0/50.sw-netgear-gsm7252ps-s1", "1/0/50.sw-netgear-gsm7252ps-s1"),  # a switch by its sysName
    ("eth0.pi-sw1-p10", "eth0.pi-sw1-p10"),              # a fleet Pi
    ("eth-uplink.gwhost2", "eth-uplink"),                # a host whose name only starts like the gateway's
    ("gwhost", "gateway"),
    ("", ""),
])
def test_a_label_shows_its_role_and_only_names_that_may_be_shown(gateway_host, given, shown):
    assert page.label(given) == shown


@pytest.mark.parametrize("given, shown", [
    ("pi-sw1-p10", "pi-sw1-p10"),
    ("pi-sw2-p7.example.org", "pi-sw2-p7"),
    ("sw-netgear-s3300-1", "sw-netgear-s3300-1"),
    ("gwhost.example.org", "gateway"),
    ("gwhost", "gateway"),
    ("rpi5-new-13f59c", "device"),
    ("upstreamhost.example.org", "device"),
    ("", ""),
])
def test_an_lldp_neighbour_is_a_pi_a_switch_the_gateway_or_a_device(gateway_host, given, shown):
    assert page.neighbour(given) == shown


@pytest.mark.django_db
def test_no_host_name_reaches_the_page_or_the_json(gateway_host, monkeypatch):
    views = make_views()
    views[0].name = "gwhost.example.org"
    views[0].ports[0].label = "eth-uplink.gwhost"
    views[0].ports[1].lldp_name = "gwhost.example.org"
    views[0].ports[2].label = "uplink.upstreamhost.example.org"
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    c = Client(HTTP_HOST=HOSTS[0])
    for body in (c.get("/management/switches/").content.decode(), c.get("/management/switches.json").content.decode()):
        assert "gwhost" not in body.lower() and "upstreamhost" not in body and "example.org" not in body
        assert "eth-uplink.gateway" in body


@pytest.mark.django_db
def test_a_fleet_pi_is_still_linked_by_its_lldp_name(monkeypatch):
    machine("100000002d8aa80c", "pi-sw1-p1")
    views = make_views()
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    ports = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)["switches"][0]["ports"]
    assert ports[0]["lldp_name"] == "pi-sw1-p1" and ports[0]["lldp_url"].endswith("/100000002d8aa80c/")


@pytest.mark.django_db
def test_get_only(reader):
    assert Client(HTTP_HOST=HOSTS[0]).post("/management/switches/").status_code == 405
    assert Client(HTTP_HOST=HOSTS[0]).post("/management/switches.json").status_code == 405


@pytest.mark.django_db
def test_a_community_never_reaches_the_page_or_the_json(reader, monkeypatch):
    monkeypatch.setenv("SNMP_SWITCH_COMMUNITY", COMMUNITY)
    monkeypatch.setenv("FPGAS_SWITCH_1_COMMUNITY", COMMUNITY)
    c = Client(HTTP_HOST=HOSTS[0])
    for url in ("/management/switches/", "/management/switches.json"):
        assert COMMUNITY not in c.get(url).content.decode()
