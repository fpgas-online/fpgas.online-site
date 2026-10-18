"""The switch port dashboard (#67): the page and its JSON, on every host, with the reader stubbed.

The reader is `snmp_switch.dashboard` from fpgas.online-poe. Where the installed poe does not have it yet, these
tests use minimal stand-ins with the same fields as its SwitchView and PortView dataclasses."""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

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
        oper_status: int | None = None
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
        pvid: int | None = None
        untagged_vlans: list = field(default_factory=list)
        tagged_vlans: list = field(default_factory=list)

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
        vlans: list = field(default_factory=list)

from fleet.models import Machine
from management.views import switches as page

TT = "tinytapeout.fpgas.online"
HOSTS = ["welland.fpgas.online", "ps1.fpgas.online", TT]
COMMUNITY = "s3cret-community-xyzzy"
READ_AT = "2026-10-09T10:00:00+09:30"

# a lease record that is a DHCPACK (doc["acked_at"] set), as fpgas-lease-event writes it for a new lease or a renewal:
# only an ACK puts a machine on a port (fpga.seen_ports)
ACK = {"event": "add", "acked_at": "2026-10-18T09:00:00+10:30", "address": "10.21.1.2"}


def make_views():
    return [SwitchView(
        index=1, name="switch 1", model="gsm7252ps", reachable=True, error="", read_at=READ_AT, good_at=READ_AT,
        ports=[
            PortView(port=1, label="uplink", link_up=True, speed_mbps=1000, poe_state="delivering", poe_admin_enabled=True,
                     poe_watts=3.5, lldp_name="pi-sw1-p1", lldp_port="eth0", macs=["aa:00:00:00:00:01", "aa:00:00:00:00:02",
                                                                    "aa:00:00:00:00:03", "aa:00:00:00:00:04"],
                     rx_bps=250_000, tx_bps=3_400_000, rx_errors=0, tx_errors=2),
            PortView(port=2, link_up=True, speed_mbps=100, poe_state="delivering", poe_admin_enabled=True, poe_watts=1.0,
                     lldp_name="not-a-fleet-host", lldp_port="gi1"),
            PortView(port=3, poe_state="searching", poe_admin_enabled=True),
        ])]


def in_their_vlans(views):
    """`views` with each port in its own board VLAN, as the inventory says (2000 + 100 * switch + port)."""
    for p in views[0].ports:
        p.pvid, p.untagged_vlans = 2100 + p.port, [2100 + p.port]
    return views


@pytest.fixture(autouse=True)
def welland_inventory(monkeypatch):
    """Switch 1 described as welland's is (40 board ports; gateway trunk 47, house uplink 48, switch trunk 50), so
    the page knows which ports are boards. A test that needs another inventory sets page._inventory itself."""
    from types import SimpleNamespace

    switch_1 = SimpleNamespace(index=1, access_ports=40, gateway_trunk_port=47, downstream_trunk_ports=(50,),
                               house_uplink_port=48)
    monkeypatch.setattr(page, "_inventory", lambda: {1: switch_1})
    # the PoE policy reads the same switches file as the page (pibfpgas.poe re-checks the access-port bound, #147)
    monkeypatch.setattr("pibfpgas.poe._spec", lambda switch: (page._inventory() or {}).get(switch))


@pytest.fixture
def reader(monkeypatch):
    views = in_their_vlans(make_views())
    monkeypatch.setattr(page, "_reader", lambda: lambda cache, *a, **k: views)
    return views


def page_script(html):
    """The page's own script and the row builder it loads (port-row.js, shared with a Pi's own page): what draws the
    table after a refresh."""
    inline = html.split("<script>")[1].split("</script>")[0]
    assert "management/port-row.js" in html.split("<script>")[0]
    return inline + (Path(page.__file__).parents[1] / "static" / "management" / "port-row.js").read_text()


def machine(serial, hostname, seen_ago=0):
    return Machine.objects.create(serial=serial, site="welland", hostname=hostname,
                                  last_seen=timezone.now() - timedelta(minutes=seen_ago))


# revision codes rpi-hwid reads (the label's "Pi 5 2 GB", ...)
REVISIONS = {"Pi 5": "b04170", "Pi 4B": "b03111", "Pi 3B+": "a020d3"}


def seat(hostname, serial, model="Pi 5", board="acorn", result="pass", seen_ago=0, lease=None, lease_ago=0,
         reason=""):
    """A registered machine as the fleet registry has it (a label from its revision code, a boot check of `board`
    with `result`, seen `seen_ago` minutes ago, checking in), and the gateway's lease record of it on `lease` ((switch,
    port)) `lease_ago` minutes ago: the observation that puts it on that port."""
    from fleet.models import BootEvent, GatewayRecord, HardwareSnapshot

    seen = timezone.now() - timedelta(minutes=seen_ago)
    m = Machine.objects.create(serial=serial, site="welland", hostname=hostname, online=True, last_seen=seen,
                               last_boot_id="b1", last_uptime_s=3600)
    m.latest_snapshot = HardwareSnapshot.objects.create(machine=m, fingerprint=serial, document={
        "machine": {"serial": serial, "revision_code": REVISIONS[model]}})
    m.save()
    detail = {"result": result, "board0": f"{board} - {result}"}
    if reason:
        detail["board0_reason"] = reason
    BootEvent.objects.create(machine=m, boot_id="b1", stage="fpga-verified", ts=seen, detail=detail)
    if lease is not None:
        GatewayRecord.objects.create(site="welland", kind="lease", switch=lease[0], port=lease[1], hostname=hostname,
                                     at=timezone.now() - timedelta(minutes=lease_ago), doc=ACK, machine=m)
    return m


@pytest.mark.django_db
@pytest.mark.parametrize("host", HOSTS)
def test_the_page_and_json_answer_without_a_login_on_every_host(host, reader):
    c = Client(HTTP_HOST=host)
    r = c.get("/management/switches/")
    assert r.status_code == 200
    html = r.content.decode()
    assert "<title>Switch ports &mdash; Management &mdash; fpgas.online</title>" in html
    assert '<a href="/management/switches/" aria-current="page">Switch ports</a>' in html  # the one navigation
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
def test_a_port_with_nothing_connected_reads_down_with_the_searching_symbol_and_empty_cells(reader):
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    row = row_of(html, 3)
    # round 5: the empty Link badge (a button: the port is an access port), its words for a screen reader
    assert ('class="mgmt-link-badge mgmt-link-button mgmt-badge-none" data-cls="mgmt-badge-none" data-text=""' in row
            and '<span class="mgmt-link-glyph" aria-hidden="true"></span>' in row
            and '<span class="mgmt-sr"> no link</span>' in row)
    assert 'title="PoE on, searching: nothing drawing power">🔍<span class="mgmt-sr"> PoE on, searching: nothing drawing power</span>' in row
    # no machine on it: the hardware cells, the speed and the watts are empty, not a dash or a word
    for column in ("c-model", "c-board", "c-serial", "c-lease", "c-watts"):
        assert re.search(rf'<td class="[^"]*{column}"[^>]*></td>', row), column


@pytest.mark.django_db
def test_traffic_is_rates_and_a_dash_when_unknown(reader):
    d = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)["switches"][0]["ports"]
    # one unit for the column (kbit/s, in the header's title), a number a cell (#130)
    assert (d[0]["rx"], d[0]["tx"]) == ("250", "3400")
    assert (d[2]["rx"], d[2]["tx"]) == ("–", "–")
    # receive and transmit errors, a column each
    assert (d[0]["rx_errors"], d[0]["tx_errors"]) == ("0", "2") and (d[2]["rx_errors"], d[2]["tx_errors"]) == ("–", "–")
    # Tim, 17 Oct: "Make the numbers in the errors column bold and right red": a count above zero carries the class; a
    # zero, and a count not read, do not
    assert (d[0]["rx_errors_cls"], d[0]["tx_errors_cls"]) == ("", "mgmt-errors")
    assert (d[2]["rx_errors_cls"], d[2]["tx_errors_cls"]) == ("", "")
    # the MACs the switch saw are in the Link mark's title (the review of #137: the machine is named by the hardware
    # columns, so the MACs have no column of their own)
    assert d[0]["macs"] == ["aa:00:00:00:00:01", "aa:00:00:00:00:02", "aa:00:00:00:00:03", "aa:00:00:00:00:04"]
    assert d[0]["link_cell_title"] == ("link up; MACs seen: aa:00:00:00:00:01, aa:00:00:00:00:02, aa:00:00:00:00:03, "
                                       "aa:00:00:00:00:04")
    assert d[2]["link_cell_title"] == "link down; no MAC seen"
    # the link state and its speed, a column each (#130)
    assert (d[0]["link"], d[0]["link_title"], d[0]["speed"]) == ("↑", "link up", "1G")
    assert (d[2]["link"], d[2]["link_title"], d[2]["speed"]) == ("↓", "link down", "")


@pytest.mark.django_db
def test_a_fleet_pis_lldp_name_links_to_its_page_and_other_names_do_not(reader):
    machine("10000000aaaa0001", "pi-sw1-p1.welland.example")
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    # the neighbour, then the port it reports in a column of its own (#130: one value a column)
    assert ('<td class="c-lldp" data-label="LLDP" title="the name it announces"><a href="/fleet/10000000aaaa0001/">'
            'pi-sw1-p1</a></td>\n        <td class="c-lldp-port" data-label="LLDP port">eth0</td>') in html
    # any other neighbour by the name it announces, never "device" (Tim, 16 Oct 2026, #148: "Why is the device's LLDP
    # information hidden for port 11 on switch 2?"); only a fleet Pi's links a page
    assert '<td class="c-lldp" data-label="LLDP" title="the name it announces">not-a-fleet-host</td>' in html
    assert ">device<" not in html
    assert '<td class="c-lldp-port" data-label="LLDP port">gi1</td>' in html


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
    assert '<td class="c-lldp" data-label="LLDP" title="the name it announces">pi-sw1-p1</td>' in html
    assert "/fleet/" not in html


@pytest.mark.django_db
def test_the_number_of_queries_does_not_grow_with_the_machines_on_the_ports(reader):
    """The fleet hosts, the lease records and the registry are read once a page, not once a port or a machine."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    reader[0].ports[1].lldp_name = "pi-sw1-p2"
    reader[0].ports[2].lldp_name = "pi-sw1-p3"

    def count():
        with CaptureQueriesContext(connection) as queries:
            assert Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").status_code == 200
        return len(queries)

    seat("pi-sw1-p1", "10000000aaaa0001", lease=(1, 1))
    one = count()
    for n in range(2, 4):
        seat(f"pi-sw1-p{n}", f"10000000aaaa000{n}", lease=(1, n))
    seat("pi-sw1-p3", "10000000aaaa0009", model="Pi 4B", board="arty", lease=(1, 3), lease_ago=60)
    assert count() == one


@pytest.mark.django_db
def test_an_unreachable_switch_shows_its_error_and_last_ports(monkeypatch):
    views = make_views()
    views[0].reachable = False
    views[0].error = "not answering since 09:59:45"
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    assert "not answering since 09:59:45" in html and "mgmt-dead" in html
    assert re.search(r'<th scope="row" class="c-port"[^>]*>1</th>', html)


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
    assert ports[0]["port_title"] == f"the switch labels this port {evil}" and ports[1]["lldp_port"] == evil


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
    assert (ports[0]["poe"], ports[0]["poe_title"], ports[0]["watts"]) == ("⚡", "PoE delivering power", "3.5")
    assert (ports[2]["poe"], ports[2]["poe_title"], ports[2]["watts"]) == ("🔍", "PoE on, searching: nothing drawing power", "")


def test_poe_cells_are_one_character_states_and_the_watts_a_column_of_their_own():
    """One form per column (#130): the PoE state as one character, its words on hover; the watts apart."""
    marks = {state: page.poe_cell(state)[:3] for state in ("delivering", "searching", "disabled", "fault")}
    assert marks == {"delivering": ("⚡", "mgmt-poe-on", "PoE delivering power"),
                     "searching": ("🔍", "mgmt-poe-idle", "PoE on, searching: nothing drawing power"),
                     "disabled": ("⊗", "mgmt-poe-off", "PoE off"),
                     "fault": ("!", "mgmt-state-attention", "PoE fault")}
    assert page.poe_cell(None, has_poe=False)[:3] == ("–", "mgmt-test-none", "no PoE on this port")
    for state in ("delivering", "searching", "disabled", "fault"):
        assert len(page.poe_cell(state)[0]) == 1
    assert page.watts("delivering", 8.64) == "8.6" and page.watts("delivering", None) == ""
    assert page.watts("searching", 0.0) == ""


def test_a_poe_state_the_page_does_not_know_is_said_in_the_cell_never_unknown():
    """Fail loud (spec 3): the switch's own word in the cell, and the PoE port with no state says "not read"."""
    text, cls, title, _ = page.poe_cell("melting")
    assert (text, cls) == ("melting", "mgmt-state-attention") and "'melting'" in title and "unknown" not in title
    assert page.poe_cell(None, has_poe=True)[:2] == ("not read", "mgmt-state-attention")


def test_link_speeds_are_short():
    assert [page.link_speed(m) for m in (10, 100, 1000, 2500, 10000, 20000)] == \
        ["10M", "100M", "1G", "2.5G", "10G", "20G"]


COLUMNS = ["Port", "Model", "RAM", "Board", "State", "Serial", "Lease", "Net", "Link", "PoE", "W", "⏻", "VLAN", "GW",
           "LLDP", "LLDP port", "In", "Out", "Rx err", "Tx err", "Role"]


@pytest.mark.django_db
def test_the_columns_are_the_hardware_first_then_the_network_each_with_its_definition(reader):
    """#130: the first columns are /fleet/'s (Port, Model, Board, State), then the network columns, one value a column;
    every header has a title saying what its column is; the table is as wide as its values (mgmt-fit), striped and
    sortable, with table.js loaded."""
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    thead = html[html.index("<thead>"):html.index("</thead>")]
    # what a viewer sees: the visually hidden screen-reader text (.mgmt-sr) takes no width
    seen = re.sub(r'<span class="mgmt-sr">[^<]*</span>', "", thead)
    ths = re.findall(r'<th scope="col"([^>]*)>(.*?)</th>', seen, re.S)
    assert [re.sub(r"<[^>]+>", "", text) for _, text in ths] == COLUMNS
    for attrs, text in ths:
        title = re.search(r'title="([^"]+)"', attrs)
        assert title and len(title.group(1)) > 10, text  # a header without its definition is a shortcut
    # the one-character columns are set upright, as /fleet/'s State and check columns, so they are a mark wide
    upright = [re.sub(r"<[^>]+>", "", text) for attrs, text in ths if "mgmt-checkhead" in attrs]
    assert upright == ["State", "Net", "Link", "PoE"]
    # the power column's symbol is read as its word
    assert '<span aria-hidden="true">⏻</span><span class="mgmt-sr">Power</span>' in thead
    assert 'class="mgmt-table mgmt-ports mgmt-fit mgmt-striped mgmt-sortable"' in html
    assert '<script src="/static/management/table.js" defer></script>' in html
    assert "nowrap" not in thead


@pytest.mark.django_db
def test_each_row_has_a_cell_for_every_column_in_the_page_and_the_script(reader):
    """The served rows and the rows the refresh builds have one cell a column, in the headers' order."""
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    for port in (1, 2, 3):
        assert len(re.findall(r"<td ", row_of(html, port))) == len(COLUMNS) - 1  # the port is the row's <th>
    classes = re.findall(r'<th scope="col" class="(?:mgmt-checkhead )?(c-[\w-]+)"', html)
    assert len(classes) == len(COLUMNS)
    script = page_script(html)
    for cls in classes[1:]:
        assert f'"{cls}' in script or f' {cls}"' in script or f'"mgmt-mark {cls}' in script or \
            f'"mgmt-n {cls}' in script or f'"mgmt-label {cls}' in script, cls


@pytest.fixture
def gateway_host(monkeypatch):
    monkeypatch.setattr(page.socket, "gethostname", lambda: "gwhost")
    monkeypatch.setattr(page.socket, "getfqdn", lambda: "gwhost.example.org")
    page._gateway_names.cache_clear()
    yield
    page._gateway_names.cache_clear()


def test_a_host_with_no_name_of_its_own_is_refused(monkeypatch):
    from django.core.exceptions import ImproperlyConfigured

    monkeypatch.setattr(page.socket, "gethostname", lambda: "localhost")
    monkeypatch.setattr(page.socket, "getfqdn", lambda: "localhost.localdomain")
    page._gateway_names.cache_clear()
    try:
        with pytest.raises(ImproperlyConfigured):
            page.label("bmc.anything")
    finally:
        page._gateway_names.cache_clear()


@pytest.mark.parametrize("given, shown", [
    ("sw-netgear-s3300-1", "sw-netgear-s3300-1"),
    ("sw-netgear-s3300-1.example.org", "sw-netgear-s3300-1"),
    ("gwhost", "gwhost"),
    ("core-router", "core-router"),
])
def test_a_switch_name_follows_the_same_rule(gateway_host, given, shown):
    assert page.switch_name(given) == shown


@pytest.mark.parametrize("given, shown", [
    ("eth-uplink.gwhost", "eth-uplink.gateway"),         # the gateway's name: "gateway"
    ("bmc.GWHOST", "bmc.gateway"),
    ("eth-uplink.gwhost.example.org", "eth-uplink.gateway"),  # its full name too, as one
    ("eth-uplink.pi10.fpgas", "eth-uplink.pi10.fpgas"),  # every other label in full (#89, rule 20)
    ("eth0.rpi5-sqrl", "eth0.rpi5-sqrl"),
    ("1/0/50.sw-netgear-gsm7252ps-s1", "1/0/50.sw-netgear-gsm7252ps-s1"),
    ("eth0.pi-sw1-p10", "eth0.pi-sw1-p10"),
    ("eth-uplink.gwhost2", "eth-uplink.gwhost2"),        # a host whose name only starts like the gateway's
    ("gwhost", "gateway"),
    ("gwhost.eth0", "gateway.eth0"),
    ("GWHOST", "gateway"),
    ("", ""),
])
def test_a_label_is_shown_in_full_with_the_gateways_names_replaced(gateway_host, given, shown):
    assert page.label(given) == shown


def test_the_house_uplinks_device_is_masked_wherever_a_label_names_it(gateway_host):
    from types import SimpleNamespace

    ports = [SimpleNamespace(port=48, label="uplink.upstreamhost.example.org"), SimpleNamespace(port=1, label="x")]
    house = page.house_names(spec(), ports)
    assert page.label("uplink.upstreamhost.example.org", house) == "uplink.house uplink"
    assert page.label("mirror-of.upstreamhost", house) == "mirror-of.house uplink"
    assert page.house_names(spec(house_uplink_port=None), ports) == ()


@pytest.mark.parametrize("given, shown", [
    ("pi-sw1-p10", "pi-sw1-p10"),
    ("pi-sw2-p7.example.org", "pi-sw2-p7"),
    ("sw-netgear-s3300-1", "sw-netgear-s3300-1"),
    ("gwhost.example.org", "gwhost"),  # the gateway too ("Stop hiding the lldp information")
    ("gwhost", "gwhost"),
    ("rpi5-new-13f59c", "rpi5-new-13f59c"),  # welland's switch 2 port 30 in the 9 Oct replay (#148)
    ("some-laptop.example.org", "some-laptop"),
    ("", ""),
])
def test_every_lldp_neighbour_is_shown_by_its_name(gateway_host, given, shown):
    assert page.neighbour(given) == shown


def test_a_neighbour_on_the_gateway_trunk_or_the_house_uplink_is_shown_by_its_name(gateway_host):
    assert page.neighbour("upstreamhost.example.org") == "upstreamhost"
    assert page.neighbour("gwhost.example.org") == "gwhost"


@pytest.mark.django_db
def test_lldp_is_shown_as_reported_and_the_switchs_own_labels_keep_their_rule(gateway_host, monkeypatch):
    """Tim, 16 Oct 2026: "Stop hiding the lldp information." The gateway's and the house uplink's device's LLDP
    names, and the switch's own name, are shown as reported; the switch's own port labels (its configuration, not
    LLDP) keep their rule."""
    views = make_views()
    views[0].name = "gwhost.example.org"
    views[0].ports[0].label = "eth-uplink.gwhost"
    views[0].ports[1].lldp_name = "gwhost.example.org"
    views[0].ports[2].port = 48  # the house uplink (spec())
    views[0].ports[2].label = "uplink.upstreamhost.example.org"
    views[0].ports[2].lldp_name = "upstreamhost.example.org"
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    monkeypatch.setattr(page, "_inventory", lambda: {1: spec()})
    c = Client(HTTP_HOST=HOSTS[0])
    ports = json.loads(c.get("/management/switches.json").content)["switches"][0]["ports"]
    assert (ports[1]["lldp_name"], ports[1]["lldp_title"]) == ("gwhost", "the name it announces: gwhost.example.org")
    assert ports[2]["lldp_name"] == "upstreamhost"
    body = c.get("/management/switches/").content.decode()
    assert "Switch 1: gwhost (GSM7252PS)" in body
    assert "the switch labels this port eth-uplink.gateway" in body  # a port label keeps its rule


@pytest.mark.django_db
def test_a_fleet_pi_is_still_linked_by_its_lldp_name(monkeypatch):
    machine("100000002d8aa80c", "pi-sw1-p1")
    views = make_views()
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    ports = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)["switches"][0]["ports"]
    assert ports[0]["lldp_name"] == "pi-sw1-p1" and ports[0]["lldp_url"].endswith("/100000002d8aa80c/")


def spec(**kw):

    from types import SimpleNamespace

    base = dict(index=1, access_ports=40, gateway_trunk_port=47, downstream_trunk_ports=(50,), house_uplink_port=48)
    return SimpleNamespace(**{**base, **kw})


@pytest.mark.parametrize("port, role", [
    (1, None), (40, None),                    # board ports: their own (masked) label
    (47, "gateway trunk"), (50, "switch trunk"), (48, "house uplink"),
    (42, "infrastructure"), (52, "infrastructure"),
])
def test_an_infrastructure_port_is_labelled_by_its_inventory_role(port, role):
    assert page.port_role(spec(), port) == role
    assert page.port_role(None, port) is None  # no inventory (the legacy switch)


@pytest.mark.django_db
def test_the_house_uplink_reads_house_uplink_and_other_ports_their_full_label(monkeypatch):
    views = make_views()
    views[0].ports[2].port = 48
    views[0].ports[2].label = "a-private-device"
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    monkeypatch.setattr(page, "_inventory", lambda: {1: spec()})
    c = Client(HTTP_HOST=HOSTS[0])
    ports = json.loads(c.get("/management/switches.json").content)["switches"][0]["ports"]
    # the house uplink by its role; its own label (which names a private device) is not on the page at all
    assert (ports[2]["role"], ports[2]["port_title"]) == ("house uplink", "")
    assert (ports[0]["role"], ports[0]["port_title"]) == ("", "the switch labels this port uplink")
    assert "a-private-device" not in c.get("/management/switches/").content.decode()


@pytest.mark.parametrize("port, role", [(3, "gateway trunk"), (5, "switch trunk"), (7, "house uplink"), (4, None)])
def test_a_trunk_inside_the_board_range_is_never_a_board_port(port, role):
    """The review of #84: poe's bound excludes the named ports even inside 1..access_ports, so the page must too."""
    inner = spec(access_ports=40, gateway_trunk_port=3, downstream_trunk_ports=(5,), house_uplink_port=7)
    assert page.port_role(inner, port) == role


@pytest.mark.django_db
def test_no_button_on_a_trunk_inside_the_board_range_even_when_registered(monkeypatch):
    machine("100000002d8aa803", "pi-sw1-p3")  # a forged registration on the gateway trunk
    views = make_views()
    views[0].ports[2].port = 3
    views[0].ports[2].poe_state = "delivering"
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    monkeypatch.setattr(page, "_inventory", lambda: {1: spec(gateway_trunk_port=3)})
    ports = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)["switches"][0]["ports"]
    assert (ports[2]["role"], ports[2]["can_power"]) == ("gateway trunk", False)
    assert ports[2]["machines"] == []  # a registration names no port: only the gateway's lease records do


@pytest.mark.django_db
@pytest.mark.parametrize("inventory", [None, {}])  # unreadable; or read, with no entry for this switch
def test_a_switch_the_inventory_cannot_describe_fails_closed(monkeypatch, inventory):
    machine("100000002d8aa801", "pi-sw1-p1")
    views = make_views()
    views[0].ports[0].label = "a-private-device"
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    monkeypatch.setattr(page, "_inventory", lambda: inventory)
    c = Client(HTTP_HOST=HOSTS[0])
    sw = json.loads(c.get("/management/switches.json").content)["switches"][0]
    assert all(p["port_title"] == "" and p["role"] == "" and not p["can_power"] for p in sw["ports"])
    assert page.NO_INVENTORY_NOTE in sw["status"]
    assert "a-private-device" not in c.get("/management/switches/").content.decode()


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


# --- the PoE button (#67, toggle part) ---------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize("host, switch, hostnames", [
    (HOSTS[0], 1, ["pi-sw1-p1", "pi-sw1-p10.welland.example", "pi-sw2-p3", "pi4", "not-a-pi", "pi-sw1-p100x"]),
    (HOSTS[0], None, ["pi4", "pi40.welland.example", "pi-sw1-p2", "not-a-pi"]),
])
def test_board_ports_agrees_with_board_port_and_asks_the_registry_once(host, switch, hostnames,
                                                                       django_assert_num_queries):
    from django.test import RequestFactory
    from pibfpgas.poe import board_port, board_ports

    for n, name in enumerate(hostnames):
        machine(f"2000000000000{n:03d}", name)
    request = RequestFactory().get("/", HTTP_HOST=host)
    ports = range(0, 60)
    # a site with a switches file switches every access port (coord-67/68): the registry is not asked; a flat site,
    # which has no switches file to bound it, asks it once
    with django_assert_num_queries(0 if switch is not None else 1):
        batch = board_ports(request, switch, ports)
    assert batch == {p for p in ports if board_port(request, switch, p)} and batch
    if switch is not None:  # welland_inventory's switch 1: ports 1-40 but its trunk and uplink, none past 40
        assert batch == set(range(1, 41))


def test_board_ports_on_the_tinytapeout_host_agrees_with_board_port(monkeypatch):

    from django.test import RequestFactory
    from pibfpgas.pis import Pi
    from pibfpgas.poe import board_port, board_ports

    from ttsite import boards

    named = [(None, Pi(port=p, switch=s), None, None) for s, p in ((1, 3), (2, 7), (1, 9))]
    monkeypatch.setattr(boards, "_named", lambda: iter(named))
    request = RequestFactory().get("/", HTTP_HOST=TT)
    # every access port on a site with a switches file (Tim, 16 Oct 2026); a flat site's Tiny Tapeout host still
    # switches only the ports a Pi reported a Tiny Tapeout board on (no file says which ports are access ports)
    batch = board_ports(request, 1, range(0, 20))
    assert batch == set(range(1, 20)) == {p for p in range(0, 20) if board_port(request, 1, p)}  # port 0 is none
    named[:] = [(None, Pi(port=p), None, None) for p in (3, 9)]
    assert board_ports(request, None, range(0, 20)) == {3, 9} == {p for p in range(0, 20) if board_port(request, None, p)}


def row_of(html, port, switch=1):
    """The served row of a port, from its <th> to its end."""
    return re.search(rf'<tr id="switch-{switch}-port-{port}"[^>]*>\s*<th scope="row" class="c-port"[^>]*>{port}</th>'
                     r"(.*?)</tr>", html, re.S).group(1)


@pytest.mark.django_db
def test_every_access_port_can_power_registered_or_not(reader):
    """Tim, 16 Oct 2026 (coord-67, coord-68, and in this lane's window): "All access ports should *always* be
    switchable." A port a board registered on, one whose device never registered, and an empty one each get the
    button; what a press does is its title and its accessible name (#139: a power icon, not text)."""
    machine("3000000000000001", "pi-sw1-p1")  # port 1 is registered; port 2 and 3 are not
    c = Client(HTTP_HOST=HOSTS[0])
    ports = json.loads(c.get("/management/switches.json").content)["switches"][0]["ports"]
    assert [p["can_power"] for p in ports] == [True, True, True]
    assert [p["poe_action"] for p in ports] == ["off", "off", "off"]
    html = c.get("/management/switches/").content.decode()
    # the head's script only builds them; each port also has its Link badge button (round 5)
    assert html.split("</head>")[1].count('<button type="button" class="mgmt-poe-button') == 3
    assert ('class="mgmt-poe-button mgmt-poe-to-off" data-switch="1" data-port="1" data-action="off" '
            'data-powers="pi-sw1-p1" aria-label="turn PoE off on port 1" title="turn PoE off"><span class="mgmt-poe-glyph" aria-hidden="true">⏻</span></button>') \
        in row_of(html, 1)
    for port in (2, 3):
        assert (f'data-port="{port}" data-action="off"' in row_of(html, port)
                and "mgmt-poe-none" not in row_of(html, port))
    assert "no board has registered" not in html


@pytest.mark.django_db
def test_an_infrastructure_port_never_gets_a_button_even_if_a_machine_claims_it(monkeypatch):
    views = make_views()
    views[0].ports[2].port = 48
    machine("3000000000000002", "pi-sw1-p48")  # a forged registration on the house uplink
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    monkeypatch.setattr(page, "_inventory", lambda: {1: spec()})
    c = Client(HTTP_HOST=HOSTS[0])
    ports = json.loads(c.get("/management/switches.json").content)["switches"][0]["ports"]
    assert ports[2]["port"] == 48 and ports[2]["can_power"] is False
    row = row_of(c.get("/management/switches/").content.decode(), 48)
    assert "<button" not in row and "not switched from this page: the port&#x27;s role is house uplink" in row


@pytest.mark.django_db
# The button follows the PoE ADMIN state, never the detection status (site #170, Tim 18 Oct 2026): right after an off
# the switch still detects "searching", and right after an on it says "searching" or "disabled" while the Pi boots.
# A port whose PoE was not read is offered off: "All access ports should *always* be switchable".
@pytest.mark.parametrize("admin, state, action", [
    (True, "delivering", "off"), (True, "searching", "off"), (True, "disabled", "off"), (True, "fault", "off"),
    (True, "other", "off"), (False, "searching", "on"), (False, "disabled", "on"), (False, "delivering", "on"),
    (None, None, "off")])
def test_the_button_says_what_pressing_it_does(monkeypatch, admin, state, action):
    views = make_views()
    views[0].ports[0].poe_state = state
    views[0].ports[0].poe_admin_enabled = admin
    machine("3000000000000001", "pi-sw1-p1")
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    c = Client(HTTP_HOST=HOSTS[0])
    port = json.loads(c.get("/management/switches.json").content)["switches"][0]["ports"][0]
    assert port["can_power"] is True and port["poe_action"] == action
    assert port["power_icon"] == "⏻"
    want = f"turn PoE {action}"
    assert port["power_title"] == want
    row = row_of(c.get("/management/switches/").content.decode(), 1)
    assert f'aria-label="{want} on port 1" title="{want}"><span class="mgmt-poe-glyph" aria-hidden="true">⏻</span></button>' in row and "mgmt-poe-none" not in row


def test_every_poe_port_has_the_power_symbol_and_a_port_without_poe_says_why_it_has_none():
    assert page.power_cell(False, True, "off", None, False, "") == ("", "no PoE on this port")
    assert page.power_cell(True, True, "on", None, False, "PoE off") == ("⏻", "turn PoE on")
    no_inventory = page.power_cell(True, False, "", None, True, "PoE off")
    assert no_inventory[0] == "⏻" and "inventory could not be read" in no_inventory[1]
    assert page.power_cell(True, False, "", "gateway trunk", False, "")[1] == \
        "not switched from this page: the port's role is gateway trunk"


@pytest.mark.django_db
def test_the_policy_is_asked_once_per_switch_not_once_per_port(reader, monkeypatch):
    calls = []
    import pibfpgas.poe as poe

    monkeypatch.setattr(poe, "board_ports", lambda request, switch, ports: calls.append((switch, list(ports))) or set())
    Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json")
    assert calls == [(1, [1, 2, 3])]


@pytest.mark.django_db
def test_the_page_names_the_power_url_and_the_key_explains_the_button(reader):
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    assert 'var powerUrl = "/snmp/power", linkUrl = "/snmp/link";' in html
    assert ('title="Power: a filled green ⏻ (PoE on, or in a fault) turns its PoE off, a hollow red ⏻ (PoE off) '
            'turns it on; greyed where the port is not an access port (an uplink, a trunk, infrastructure), '
            'the reason on hover"') in html


@pytest.mark.django_db
def test_the_script_builds_the_button_without_innerhtml(reader):
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    script = page_script(html)
    assert "innerHTML" not in script and "insertAdjacentHTML" not in script and "outerHTML" not in script
    assert 'createElement("td")' in script and "/snmp/power" in script and '"on": ' not in script
    # round 5: the press, the confirm(), the hold and the toasts are power-button.js's (tests/js/power-button.test.mjs)
    js = (Path(page.__file__).parents[1] / "static" / "management" / "power-button.js").read_text()
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "outerHTML" not in js
    assert "confirm(" in js and "Retry-After" in js
    assert "PowerButton.attach(window, {power: powerUrl, link: linkUrl}, tick)" in script


@pytest.mark.django_db
def test_a_poe_cell_is_one_mark_with_its_words_and_the_watts_beside_it(reader):
    c = Client(HTTP_HOST=HOSTS[0])
    html = c.get("/management/switches/").content.decode()
    assert ('<td class="mgmt-mark c-poe mgmt-poe-idle" data-label="PoE" data-sort="2" title="PoE on, searching: nothing drawing '
            'power">🔍<span class="mgmt-sr"> PoE on, searching: nothing drawing power</span></td>') in row_of(html, 3)
    assert ('<td class="mgmt-mark c-poe mgmt-poe-on" data-label="PoE" data-sort="1" title="PoE delivering power">⚡'
            '<span class="mgmt-sr"> PoE delivering power</span></td>\n        <td class="mgmt-n c-watts" '
            'data-label="W">3.5</td>') in row_of(html, 1)
    script = page_script(html)
    assert 'mark(poe, p.poe, p.poe_title)' in script and 'w.textContent = p.watts' in script


# --- VLAN and GW columns ---


def vlan_port(port=10, pvid=2110, untagged=(2110,), tagged=()):
    p = PortView(port=port, link_up=True, speed_mbps=1000)
    p.pvid, p.untagged_vlans, p.tagged_vlans = pvid, list(untagged), list(tagged)
    return p


def make_netdevs(root, devices):
    """A fake /sys/class/net: {name: (operstate, uevent text)}."""
    root.mkdir()
    for name, (state, uevent) in devices.items():
        (root / name).mkdir()
        (root / name / "operstate").write_text(state + "\n")
        (root / name / "uevent").write_text(uevent)
    return root


VLAN_UEVENT = "DEVTYPE=vlan\nINTERFACE=x\n"


@pytest.fixture
def netdevs(tmp_path, monkeypatch):
    root = make_netdevs(tmp_path / "net", {
        "v2110": ("up", VLAN_UEVENT), "v2111": ("down", VLAN_UEVENT), "lo": ("unknown", "INTERFACE=lo\n"),
        "v9999": ("up", "INTERFACE=v9999\n"),  # named like a VLAN interface but not one
        "vethabc": ("up", "DEVTYPE=veth\n"),
    })
    monkeypatch.setattr(page, "SYS_CLASS_NET", root)
    return root


def vlan_json(monkeypatch, ports, vlans=None, name="sw-test-1"):
    view = SwitchView(index=1, name=name, model="X", reachable=True, error="", read_at=READ_AT, good_at=READ_AT,
                      ports=ports)
    view.vlans = vlans or []
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: [view])
    j = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)
    return j["switches"][0]["ports"]


def test_a_vlan_netdev_is_named_v_id_and_says_devtype_vlan(netdevs):
    assert page.gateway_vlan_netdevs() == {2110: "up", 2111: "down"}


def test_vlan_cell_of_a_board_port():
    assert page.vlan_cell(vlan_port()) == ("2110", "PVID 2110; untagged 2110; tagged none")


def test_vlan_cell_shows_an_extra_untagged_vlan():
    text, title = page.vlan_cell(vlan_port(untagged=(21, 2110)))
    assert text == "2110" and title == "PVID 2110; untagged 21, 2110; tagged none"  # the PVID alone (#137)


def test_vlan_cell_of_a_trunk_counts_the_tagged_vlans_and_cuts_the_title():
    tagged = list(range(2101, 2141)) + list(range(2201, 2249))
    text, title = page.vlan_cell(vlan_port(port=47, pvid=21, untagged=(21,), tagged=tagged))
    assert text == "21"
    assert title.startswith("PVID 21; untagged 21; tagged 2101, 2102, ")
    assert title.endswith("2112, … (+76)") and "2201" not in title
    assert page.vlan_cell(vlan_port(pvid=None, untagged=(), tagged=tagged))[0] == "–"  # no PVID: no VLAN of its own


def test_vlan_cell_names_only_names_the_page_may_show():
    names = page._vlan_names([SwitchView(index=1, name="s", model="m", reachable=True, error="", read_at="",
                                         vlans=[{"vlan_id": 2110, "name": "pi-sw1-p10"},
                                                {"vlan_id": 21, "name": "secrethost.example.org"}])])
    assert names == {2110: "pi-sw1-p10"}
    assert page.vlan_cell(vlan_port(untagged=(21, 2110)), names)[1] == \
        "PVID 2110 (pi-sw1-p10); untagged 21, 2110 (pi-sw1-p10); tagged none"


def test_vlan_cell_without_data_and_with_an_older_poe():
    assert page.vlan_cell(vlan_port(pvid=None, untagged=())) [0] == "–"
    assert page.vlan_cell(PortView.__new__(PortView))[0] == "–"  # no pvid attribute at all
    assert page.vlan_cell(object())[0] == "–"


def test_gw_cell_states():
    devs = {2110: "up", 2111: "down"}
    assert page.gw_cell(2110, devs) == ("v2110 ↑", "gateway interface v2110, up")
    assert page.gw_cell(2111, devs) == ("v2111 ↓", "gateway interface v2111, down")
    assert page.gw_cell(2112, devs)[0] == "none"
    assert page.gw_cell(None, devs)[0] == "–" and page.gw_cell(2110, None)[0] == "–"


@pytest.mark.django_db
def test_columns_in_the_json(monkeypatch, netdevs):
    ports = vlan_json(monkeypatch, [vlan_port(10), vlan_port(11, 2111, (2111,)), vlan_port(12, 2112, (2112,)),
                                    vlan_port(13, None, ()), vlan_port(47, 21, (21,), [2110, 2111])])
    got = [(p["vlan"], p["gw"]) for p in ports]
    assert got == [("2110", "v2110 ↑"), ("2111", "v2111 ↓"), ("2112", "none"), ("–", "–"), ("21", "–")]
    assert ports[0]["vlan_title"] and ports[0]["gw_title"] == "gateway interface v2110, up"
    assert ports[4]["vlan_title"].endswith("tagged 2110, 2111")


@pytest.mark.django_db
def test_gw_is_a_dash_when_the_devices_cannot_be_read(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(page, "SYS_CLASS_NET", tmp_path / "missing")
    monkeypatch.setattr(page, "_netdev_failed", False)
    for _ in range(2):
        ports = vlan_json(monkeypatch, [vlan_port()])
        assert (ports[0]["vlan"], ports[0]["gw"]) == ("2110", "–")
    assert len([r for r in caplog.records if "network devices" in r.getMessage()]) == 1


@pytest.mark.django_db
def test_the_devices_are_read_once_per_request(monkeypatch, netdevs):
    calls = []
    real = page.gateway_vlan_netdevs
    monkeypatch.setattr(page, "gateway_vlan_netdevs", lambda: calls.append(1) or real())
    vlan_json(monkeypatch, [vlan_port(n) for n in range(1, 6)])
    assert calls == [1]


@pytest.mark.django_db
def test_an_older_poe_without_vlan_fields_shows_dashes(monkeypatch, netdevs):
    class Old:  # a PortView from a poe before the VLAN fields (admin_enabled and oper_status are required: the Link
        # badge needs them)
        port, label, link_up, speed_mbps, poe_state, poe_watts = 1, None, False, None, None, None
        admin_enabled, oper_status = True, 2
        lldp_name = lldp_port = rx_bps = tx_bps = rx_errors = tx_errors = None
        macs = []
    ports = vlan_json(monkeypatch, [Old()])
    assert (ports[0]["vlan"], ports[0]["gw"]) == ("–", "–")


@pytest.mark.django_db
def test_no_inventory_means_no_gw(monkeypatch, netdevs):
    monkeypatch.setattr(page, "_inventory", lambda: None)
    assert vlan_json(monkeypatch, [vlan_port()])[0]["gw"] == "–"


@pytest.mark.django_db
def test_the_vlan_columns_in_the_page_and_the_script(gateway_host, monkeypatch, netdevs):
    view = SwitchView(index=1, name="sw-netgear-x-1", model="X", reachable=True, error="", read_at=READ_AT,
                      ports=[vlan_port()])
    view.vlans = [{"vlan_id": 2110, "name": "gwhost"}]
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: [view])
    c = Client(HTTP_HOST=HOSTS[0])
    html = c.get("/management/switches/").content.decode()
    assert ('<td class="mgmt-n c-vlan" data-label="VLAN" data-sort="2110" title="PVID 2110; untagged 2110; tagged none">'
            '2110</td>') in html
    assert '<td class="c-gw" data-label="GW" title="gateway interface v2110, up">v2110 ↑</td>' in html
    assert "title=\"the port&#x27;s VLAN (its PVID); its other untagged and its tagged VLANs on hover\"" in html
    assert "title=\"the gateway&#x27;s interface for the port&#x27;s VLAN: ↑ up, ↓ down\"" in html
    script = page_script(html)
    assert "p.vlan_title" in script and "p.gw_title" in script and "innerHTML" not in script
    for body in (html, c.get("/management/switches.json").content.decode()):
        assert "gwhost" not in body.lower() and "example.org" not in body


@pytest.mark.django_db
def test_each_switch_section_is_anchored_by_its_inventory_index(reader):
    """The index page links /management/switches/#switch-<index>: the number in every Pi's name, not the list position."""
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    assert 'id="switch-1"' in html and 'id="switch-0"' not in html
    assert '"switch-" + (s.index !== null ? s.index : n)' in html


# --- #89: the neighbour's port, a neighbouring switch by its number, the button's own column -----------------------


@pytest.mark.django_db
def test_a_pis_port_is_its_interface_name_where_its_port_id_is_its_mac(monkeypatch):
    """lldpd on a Pi sends its MAC as the port id (the MACs column has it) and the interface as the description:
    the page shows the description (fpgas-online/fpgas.online-infra#268 makes the id the name itself)."""
    views = make_views()
    views[0].ports[0].lldp_port = "B8:27:EB:E3:E7:E4"
    views[0].ports[0].lldp_port_desc = "eth0"
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    ports = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)["switches"][0]["ports"]
    assert ports[0]["lldp_port"] == "eth0" and ports[1]["lldp_port"] == "gi1"


@pytest.mark.django_db
def test_a_neighbouring_switch_is_shown_by_the_name_it_announces(monkeypatch):
    views = make_views()
    other = SwitchView(index=2, name="sw-netgear-gsm7252ps-s1", model="GSM7252PS", reachable=True, error="",
                       read_at=READ_AT, good_at=READ_AT, ports=[])
    views[0].ports[1].lldp_name = "sw-netgear-gsm7252ps-s1"
    views[0].ports[1].lldp_port = "1/0/50"
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: [*views, other])
    c = Client(HTTP_HOST=HOSTS[0])
    ports = json.loads(c.get("/management/switches.json").content)["switches"][0]["ports"]
    # the name the neighbour announces, one form (the review of #137: no "switch 2" beside the names)
    assert (ports[1]["lldp_name"], ports[1]["lldp_title"], ports[1]["lldp_port"]) == (
        "sw-netgear-gsm7252ps-s1", "the name it announces", "1/0/50")
    html = c.get("/management/switches/").content.decode()
    assert ('<td class="c-lldp" data-label="LLDP" title="the name it announces">sw-netgear-gsm7252ps-s1</td>\n'
            '        <td class="c-lldp-port" data-label="LLDP port">1/0/50</td>') in html


@pytest.mark.django_db
def test_the_poe_button_has_a_column_of_its_own(monkeypatch):
    machine("10000000aaaa0001", "pi-sw1-p1")
    views = make_views()
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    monkeypatch.setattr(page, "_inventory", lambda: {1: spec()})
    monkeypatch.setattr("pibfpgas.poe.board_ports", lambda request, index, ports: {1})
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    row = row_of(html, 1)
    poe, watts, power = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)[8:11]
    assert "⚡" in poe and "button" not in poe and watts == "3.5"
    # round 5: no message beside the button (they are toasts at the top of the page); the ⏻ is a span that can turn
    assert power.startswith('<button type="button" class="mgmt-poe-button mgmt-poe-to-off"') and \
        'mgmt-poe-msg' not in power and '<span class="mgmt-poe-glyph" aria-hidden="true">⏻</span>' in power
    # each toast is its own role=status / role=alert live region: the stack is not one too (it would be read twice)
    assert '<div class="mgmt-toasts" id="mgmt-toasts"></div>' in html
    script = page_script(html)
    assert 'var power = td("c-power", "", p.power_title)' in script and "poe.appendChild(powerButton" not in script


@pytest.mark.django_db
def test_the_switchs_own_label_is_on_a_board_ports_number_and_the_role_is_a_column(monkeypatch):
    """The review of #137: one form a column. The switch's own label (its configuration, stale in places: poe #14)
    names nothing the hardware columns do not, so it is the port number's title on a board port; the Role column holds
    only the inventory's role words, empty on a board port."""
    views = make_views()
    views[0].ports[0].label = "1/0/51.sw-netgear-gsm7252ps-s1"
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    c = Client(HTTP_HOST=HOSTS[0])
    html = c.get("/management/switches/").content.decode()
    assert re.search(r'<th scope="row" class="c-port" title="the switch labels this port '
                     r'1/0/51\.sw-netgear-gsm7252ps-s1">1</th>', html)
    assert '<th scope="row" class="c-port">3</th>' in html  # no label: no title
    ports = json.loads(c.get("/management/switches.json").content)["switches"][0]["ports"]
    assert "label" not in ports[0] and "label_parts" not in ports[0]
    assert {p["role"] for p in ports} == {""}


@pytest.mark.django_db
def test_the_explanations_are_one_collapsed_key_after_the_switches(reader):
    """#94 item 5: the tables come first; no paragraph above them and no key line per switch."""
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    body = html.split("<h1>Switch ports</h1>", 1)[1]
    assert body.count('<details class="mgmt-key-box"><summary>Key</summary>') == 1
    assert body.index("<table") < body.index('<details class="mgmt-key-box">')
    assert 'class="mgmt-key"' not in body and "<details open" not in body
    assert "mgmt-panel-key" not in body  # no key beside the drawing (#130)



# --- #102 review round -------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_lldp_port_is_shown_as_reported(gateway_host, monkeypatch):
    """The neighbour's port id and description are shown as it sends them, names and all ("Stop hiding the lldp
    information", Tim, 16 Oct 2026); a port id that is the MAC shows its description instead (the MACs are on hover)."""
    views = make_views()
    views[0].ports[2].port = 48  # the house uplink
    views[0].ports[2].label = "uplink.upstreamhost.example.org"
    views[0].ports[0].lldp_port = "B8:27:EB:E3:E7:E4"
    views[0].ports[0].lldp_port_desc = "eth0-gwhost"
    views[0].ports[1].lldp_port = "to.upstreamhost"
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    monkeypatch.setattr(page, "_inventory", lambda: {1: spec()})
    ports = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)["switches"][0]["ports"]
    assert ports[0]["lldp_port"] == "eth0-gwhost" and ports[1]["lldp_port"] == "to.upstreamhost"


@pytest.mark.parametrize("given, shown", [
    ("gwhost-eth0", "gateway-eth0"), ("eth0_gwhost", "eth0_gateway"), ("gwhost2", "gwhost2"), ("xgwhost", "xgwhost"),
])
def test_a_name_joined_by_a_hyphen_or_underscore_is_still_the_name(gateway_host, given, shown):
    assert page.label(given) == shown


@pytest.mark.django_db
def test_the_house_device_is_masked_on_every_switch(monkeypatch):
    views = make_views()
    views[0].ports[2].port = 48
    views[0].ports[2].label = "uplink.upstreamhost"
    other = SwitchView(index=2, name="sw-2", model="S3300", reachable=True, error="", read_at=READ_AT, good_at=READ_AT,
                       ports=[PortView(port=7, label="mirror.upstreamhost")])
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: [*views, other])
    monkeypatch.setattr(page, "_inventory", lambda: {1: spec(), 2: spec(index=2, house_uplink_port=None)})
    c = Client(HTTP_HOST=HOSTS[0])
    sw2 = json.loads(c.get("/management/switches.json").content)["switches"][1]["ports"]
    assert sw2[0]["port_title"] == "the switch labels this port mirror.house uplink"
    assert "upstreamhost" not in c.get("/management/switches/").content.decode()


@pytest.mark.django_db
def test_the_read_time_is_x_ago_with_the_exact_time_in_the_title(reader, settings):
    settings.MANAGEMENT_TIME_ZONE = "Australia/Adelaide"  # the installation's zone, whatever the test host's is
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    # READ_AT is 10:00 at +09:30 on 9 Oct: 11:00 in Adelaide (+10:30), days ago now
    assert re.search(r'read <span class="mgmt-readat" title="2026-10-09 11:00:00">\d+ d ago</span>', html)
    script = page_script(html)
    assert "readat.textContent = s.read_ago; readat.title = s.read_at;" in script


def test_on_a_phone_a_port_is_a_block_of_its_hardware_state_link_and_poe():
    """#130: the phone layout for the new columns: each port a block of labelled values, the hardware and its State
    first; the network detail columns (VLAN, GW, LLDP port, MACs, traffic, errors, label) and the serial left out by
    their class, never by position; an empty value takes no room."""
    import pathlib

    from django.apps import apps

    from tests.test_management_headers import _rules

    css = (pathlib.Path(apps.get_app_config("management").path) / "static/management/management.css").read_text()
    phone = css[css.index("/* On a phone (#130)"):]
    phone = phone[:phone.index("\n}\n") + 3]
    rules = _rules(phone)
    hidden = next(sels for sels, body in rules if "display: none" in body and ".mgmt-ports td.c-vlan" in sels)
    assert set(hidden) == {*(f".mgmt-ports td.c-{c}" for c in (
        "vlan", "gw", "lldp-port", "rx", "tx", "rx-errors", "tx-errors", "serial")), ".mgmt-ports td:empty"}
    assert "nth-child" not in phone  # a column is found by its class, so a new column cannot shift the layout
    # an empty board port is left out on a phone, and the heading line's count of them is shown there only
    assert any(".mgmt-ports tr.mgmt-empty-port" in sels and "display: none" in body for sels, body in rules)
    assert any(".mgmt-empty-count:not([hidden])" in sels and "display: inline" in body for sels, body in rules)
    assert any(".mgmt-ports td[data-label]::before" in sels and "attr(data-label)" in body for sels, body in rules)
    assert any(".mgmt-ports tr" in sels and "flex-wrap: wrap" in body for sels, body in rules)


@pytest.mark.django_db
def test_on_a_phone_every_shown_cell_says_its_column(reader):
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    for cell in re.findall(r"<td [^>]*>", row_of(html, 1)):
        if "c-power" not in cell:  # the button says what it does
            assert "data-label=" in cell, cell


@pytest.mark.django_db
def test_the_page_says_when_the_read_that_gave_its_ports_started(reader):
    """power-button.js keeps a press's confirmed state until a reading that started after the switch confirmed it: the
    start of the read (read_at), never good_at (stamped after the ports were read), and none for a switch that is not
    answering (its ports are from an earlier read)."""
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    read_from = round(datetime.fromisoformat(READ_AT).timestamp() * 1000)
    assert f'id="switch-1" data-read-from="{read_from}"' in html
    later = "2026-10-09T10:00:07+09:30"
    reader[:] = [SwitchView(index=1, name="sw", model="X", reachable=False, error="timeout", read_at=READ_AT,
                            good_at="2026-10-09T09:59:00+09:30"),
                 SwitchView(index=2, name="sw", model="X", reachable=True, error="", read_at=READ_AT, good_at=later)]
    switches = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)["switches"]
    assert [s["read_from_ms"] for s in switches] == ["", read_from]
    reader[:] = [SwitchView(index=1, name="sw", model="X", reachable=True, error="", read_at="2026-10-09T10:00:00")]
    with pytest.raises(ValueError, match="no offset"):
        Client(HTTP_HOST=HOSTS[0], raise_request_exception=True).get("/management/switches.json")


@pytest.mark.django_db
def test_an_error_count_above_zero_is_marked_in_the_served_table(reader):
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    row = row_of(html, 1)
    assert '<td class="mgmt-n c-rx-errors" data-label="Rx err">0</td>' in row
    assert '<td class="mgmt-n c-tx-errors mgmt-errors" data-label="Tx err">2</td>' in row


# --- #130: the hardware on each port, from the gateway's observation ---------------------------------------------


def ports_json(host=HOSTS[0], n=0):
    return json.loads(Client(HTTP_HOST=host).get("/management/switches.json").content)["switches"][n]["ports"]


@pytest.mark.django_db
def test_a_port_shows_the_machine_the_gateway_last_leased_an_address_on_it(reader):
    """One machine: its /fleet/ model, board type and State mark with its word, its serial linking its page, and the
    lease that puts it on the port, with its time."""
    seat("pi-sw1-p1", "10000000aaaa0001", model="Pi 5", board="acorn", lease=(1, 1), lease_ago=5)
    port = ports_json()[0]
    (m,) = port["machines"]
    assert (m["model"], m["board"], m["condition"], m["mark"]) == ("Pi 5", "Acorn", "operational", "●")
    assert m["model_title"] == "Pi 5 2 GB 10000000aaaa0001" and m["url"] == "/fleet/10000000aaaa0001/"
    assert m["lease_ago"] == "5 min ago" and m["state_title"] == "operational"
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    row = row_of(html, 1)
    assert ('<td class="c-model" data-label="Model" data-sort="0 Pi 5"><div title="Pi 5 2 GB 10000000aaaa0001">Pi 5'
            '</div></td>') in row
    assert '<td class="c-board" data-label="Board" data-sort="0 Acorn"><div title="Acorn">Acorn</div></td>' in row
    assert ('<div class="mgmt-state-operational" title="operational">●<span class="mgmt-sr"> operational</span></div>'
            in row)
    assert '<a href="/fleet/10000000aaaa0001/">10000000aaaa0001</a>' in row
    assert re.search(r'<div title="\d{4}-\d\d-\d\d \d\d:\d\d:\d\d">5 min ago</div>', row)


@pytest.mark.django_db
def test_a_machine_is_on_the_port_the_gateway_saw_it_on_not_the_one_its_name_says(reader):
    """Never a remembered port table: the host name pi-sw1-p3 names port 3, the gateway's newest lease says port 1."""
    m = seat("pi-sw1-p3", "10000000aaaa0003", lease=(1, 3), lease_ago=60)
    from fleet.models import GatewayRecord
    GatewayRecord.objects.create(site="welland", kind="lease", switch=1, port=1, hostname="pi-sw1-p3",
                                 at=timezone.now(), doc=ACK, machine=m)
    ports = ports_json()
    assert [mm["serial"] for mm in ports[0]["machines"]] == ["10000000aaaa0003"] and ports[2]["machines"] == []
    seat("pi-sw1-p2", "10000000aaaa0002")  # registered, but the gateway has leased it nothing: on no port
    assert ports_json()[1]["machines"] == []


@pytest.mark.django_db
def test_a_port_shows_only_the_machine_seen_there_last(reader):
    """Tim, 18 Oct 2026: "When a device is switched to a port which already had a device, two devices appear on the
    switch port. As there is suppose to be a strict one device per port, only the latest should." As on sw2 p35 after
    his swap: the machine leased an address there most recently is the port's; the others were moved out."""
    seat("pi-sw1-p2", "10000000aaaa0021", model="Pi 4B", board="arty", result="fail", reason="pin-id fail",
         lease=(1, 2), lease_ago=600)
    seat("pi-sw1-p2", "10000000aaaa0022", model="Pi 5", board="acorn", lease=(1, 2), lease_ago=1)
    seat("pi-sw1-p2", "10000000aaaa0023", model="Pi 3B+", board="netv2", seen_ago=60 * 8, lease=(1, 2),
         lease_ago=60 * 8)
    port = ports_json()[1]
    assert [m["serial"] for m in port["machines"]] == ["10000000aaaa0022"]
    assert port["state_sort"] == 3  # operational
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    row = row_of(html, 2)
    for column in ("c-model", "c-board", "c-state", "c-serial", "c-lease"):
        cell = re.search(rf'<td class="[^"]*{column}"[^>]*>(.*?)</td>', row).group(1)
        assert cell.count("<div") == 1, column
    assert "10000000aaaa0021" not in html and "10000000aaaa0023" not in html
    # the panel's box: that machine with its own ●, and, apart, the Net ✗ (port 2 links at 100M under that Pi 5)
    sw = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)["switches"][0]
    box = next(p for p in sw["panel"] if p["port"] == 2)
    assert (box["kind"], box["mark"], box["lines"], box["net"]) == ("operational", "●", ["Pi 5", "Acorn", ""], "✗")
    assert "more" not in box
    assert box["title"].count("leased an address here") == 1
    assert "not as it should be: link 100M; a Pi 5 links at 1G" in box["title"]


@pytest.mark.django_db
def test_a_machine_moved_out_of_a_port_and_back_is_the_ports_again(reader):
    """The swap on sw2 p35 the other way round: the old machine's new lease there makes it the port's again."""
    from fleet.models import GatewayRecord

    old = seat("pi-sw1-p2", "10000000aaaa0031", lease=(1, 2), lease_ago=120)
    seat("pi-sw1-p2", "10000000aaaa0032", lease=(1, 2), lease_ago=60)
    assert [m["serial"] for m in ports_json()[1]["machines"]] == ["10000000aaaa0032"]
    GatewayRecord.objects.create(site="welland", kind="lease", switch=1, port=2, hostname="pi-sw1-p2",
                                 at=timezone.now(), doc=ACK, machine=old)
    assert [m["serial"] for m in ports_json()[1]["machines"]] == ["10000000aaaa0031"]


@pytest.mark.django_db
def test_a_lease_ending_after_the_swap_does_not_hand_the_port_back(reader):
    """The review of #172: only a DHCPACK puts a machine on a port; the swapped-out Pi's lease expiring (a `del`)
    after the swapped-in Pi's ACK says nothing about who is on the port."""
    from fleet.models import GatewayRecord

    old = seat("pi-sw1-p2", "10000000aaaa0051", lease=(1, 2), lease_ago=120)
    seat("pi-sw1-p2", "10000000aaaa0052", lease=(1, 2), lease_ago=60)
    GatewayRecord.objects.create(site="welland", kind="lease", switch=1, port=2, hostname="pi-sw1-p2",
                                 at=timezone.now(), doc={"event": "del", "acked_at": None, "address": "10.21.1.2"},
                                 machine=old)
    assert [m["serial"] for m in ports_json()[1]["machines"]] == ["10000000aaaa0052"]


@pytest.mark.django_db
def test_a_device_not_registered_yet_takes_the_port_from_the_one_before_it(reader):
    """One device a port: a newcomer ACKed on the port (no registered machine yet) leaves the old Pi on no port, and
    the port is drawn as unregistered (a link and power, no registered machine)."""
    from fleet.models import GatewayRecord

    seat("pi-sw1-p2", "10000000aaaa0061", lease=(1, 2), lease_ago=60)
    GatewayRecord.objects.create(site="welland", kind="lease", switch=1, port=2, hostname="raspberrypi",
                                 at=timezone.now(), doc=ACK, machine=None)
    port = ports_json()[1]
    assert port["machines"] == [] and port["unregistered"] == "?"


@pytest.mark.django_db
def test_every_page_agrees_on_the_port_of_a_pi_moved_out(reader):
    """The review of #172: /fleet/ and the swapped-out Pi's own page say what the switch port dashboard says."""
    from management import fpga

    old = seat("pi-sw1-p2", "10000000aaaa0071", lease=(1, 2), lease_ago=120)
    new = seat("pi-sw1-p2", "10000000aaaa0072", lease=(1, 2), lease_ago=60)
    seen = fpga.seen_ports()
    assert old.pk not in seen and (seen[new.pk].switch, seen[new.pk].port) == (1, 2)
    c = Client(HTTP_HOST=HOSTS[0])
    fleet = c.get("/fleet/").content.decode()
    assert fleet.count(">sw1 p2<") == 1
    page_old = c.get("/fleet/10000000aaaa0071/").content.decode()
    assert re.search(r'>Port</a></th><td[^>]*>–</td>', page_old)
    page_new = c.get("/fleet/10000000aaaa0072/").content.decode()
    assert re.search(r'>Port</a></th><td[^>]*>sw1 p2', page_new)


@pytest.mark.django_db
def test_two_leases_in_the_same_second_the_later_record_wins(reader):
    from fleet.models import GatewayRecord

    at = timezone.now()
    first = seat("pi-sw1-p2", "10000000aaaa0041")
    second = seat("pi-sw1-p2", "10000000aaaa0042")
    for m in (first, second):
        GatewayRecord.objects.create(site="welland", kind="lease", switch=1, port=2, hostname="pi-sw1-p2", at=at,
                                     doc=ACK, machine=m)
    assert [m["serial"] for m in ports_json()[1]["machines"]] == ["10000000aaaa0042"]


@pytest.mark.django_db
def test_a_port_with_a_link_or_power_and_no_registered_machine_is_marked_not_registered(reader):
    ports = ports_json()
    # port 2: a link and power, a neighbour the fleet does not know; port 3: nothing (down, searching)
    assert ports[1]["machines"] == [] and ports[1]["unregistered"] == "?"
    assert ports[1]["unregistered_title"] == ("not registered: a link up and PoE power drawn, and the gateway has "
                                              "leased no registered machine an address on this port")
    assert ports[2]["unregistered"] == "" and ports[2]["state_sort"] == 9
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    assert '<div class="mgmt-state-unregistered" title="not registered: a link up' in row_of(html, 2)
    assert "mgmt-state-unregistered" not in row_of(html, 3)


@pytest.mark.django_db
def test_an_infrastructure_port_is_never_unregistered_and_shows_its_role_word(monkeypatch):
    views = make_views()
    views[0].ports[1].port = 46  # infrastructure (beyond the 40 board ports), link up and power drawn
    views[0].ports[2].port = 50  # the switch trunk
    views[0].ports[2].label = "1/0/50.sw-netgear-gsm7252ps-s1"
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    monkeypatch.setattr(page, "_inventory", lambda: {1: spec()})
    ports = ports_json()
    assert (ports[1]["role"], ports[1]["unregistered"]) == ("infrastructure", "")
    # the role word, and the switch's own label (which can name a private device) nowhere
    assert (ports[2]["role"], ports[2]["port_title"]) == ("switch trunk", "")
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    assert '<td class="c-role" data-label="Role">switch trunk</td>' in row_of(html, 50)
    assert '<td class="c-role" data-label="Role"></td>' in row_of(html, 1)
    assert "sw-netgear-gsm7252ps-s1" not in row_of(html, 50)


# --- #130: Net, the port's reading against what it should be ------------------------------------------------------


@pytest.mark.django_db
def test_net_is_a_tick_when_the_port_is_as_it_should_be(reader):
    seat("pi-sw1-p1", "10000000aaaa0001", model="Pi 5", lease=(1, 1))
    port = ports_json()[0]
    assert (port["net"], port["net_ok"]) == ("✓", True)
    assert port["net_title"] == ("as it should be: VLANs against the inventory, PoE, link and speed for the Pi 5 on it, "
                                 "error counters (no earlier reading to compare with yet)")


@pytest.mark.django_db
def test_net_is_a_cross_for_a_slow_link_under_a_pi_with_the_reason(reader):
    reader[0].ports[1].speed_mbps = 100
    seat("pi-sw1-p2", "10000000aaaa0002", model="Pi 5", lease=(1, 2))
    port = ports_json()[1]
    assert (port["net"], port["net_ok"]) == ("✗", False)
    assert port["net_title"] == "not as it should be: link 100M; a Pi 5 links at 1G"


@pytest.mark.django_db
def test_net_is_a_cross_for_a_link_down_under_a_pi_that_is_not_missing_but_not_under_a_missing_one(reader):
    seat("pi-sw1-p3", "10000000aaaa0003", model="Pi 4B", board="arty", lease=(1, 3))
    port = ports_json()[2]
    assert port["net_ok"] is False
    assert port["net_title"] == ("not as it should be: link down under Pi 4 Model B 2 GB 10000000aaaa0003 "
                                 "(operational)")
    Machine.objects.filter(serial="10000000aaaa0003").update(last_seen=timezone.now() - timedelta(hours=8))
    port = ports_json()[2]
    assert port["machines"][0]["condition"] == "missing" and port["net_ok"] is True  # the State says it already


@pytest.mark.django_db
@pytest.mark.parametrize("poe, words", [("fault", "PoE fault"), ("melting", "PoE state 'melting' not known")])
def test_net_is_a_cross_for_a_poe_fault_or_a_poe_state_the_page_cannot_read(reader, poe, words):
    reader[0].ports[2].poe_state = poe
    port = ports_json()[2]
    assert port["net_ok"] is False and port["net_title"] == f"not as it should be: {words}"


@pytest.mark.django_db
def test_net_names_an_inventory_failure_and_only_mentions_a_known_cause(monkeypatch):
    views = make_views()
    for p in views[0].ports:
        p.pvid, p.untagged_vlans = 2100 + p.port, [2100 + p.port, 21]  # every board port also in VLAN 21: poe #16
    views[0].ports[1].pvid = 1  # port 2: a new fault
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    monkeypatch.setattr(page, "_inventory", lambda: {1: spec()})
    ports = ports_json()
    # a ✓ never reads like a fault: as it should be apart from the known issue above, VLANs not claimed as compared
    assert ports[0]["net_ok"] is True
    assert ports[0]["net_title"] == ("as it should be apart from the known issue above (poe #16): PoE, error counters "
                                     "(no earlier reading to compare with yet)")
    assert ports[1]["net_ok"] is False
    assert ports[1]["net_title"] == ("not as it should be: port 2 PVID 1, not 2102; and the known issue above "
                                     "(poe #16)")
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    assert ('<td class="mgmt-mark c-net" data-label="Net" data-sort="0" title="not as it should be: port 2 PVID 1, '
            'not 2102') in row_of(html, 2)
    assert '<span class="mgmt-test-fail">✗</span>' in row_of(html, 2)
    assert '<span class="mgmt-test-pass">✓</span>' in row_of(html, 1)


def test_the_speeds_net_compares_are_for_the_fleet_short_model_names():
    """LINK_SPEEDS is keyed by fpga.short_model's forms, so a model the table names is compared, never skipped."""
    from management.fpga import short_model

    for label in ("Pi 3 Model B+", "Pi 4 Model B", "Pi 5", "Orange Pi PC", "Pi Compute Module 4"):
        assert short_model(label) in page.LINK_SPEEDS, label


# --- the review of #137: error counters judged against the previous reading -----------------------------------------


@pytest.mark.django_db
def test_an_error_counter_that_rose_since_the_previous_reading_is_net_cross(reader, settings):
    """The page keeps each switch's previous reading's counters (the "poe" cache, beside the reading): a counter that
    rose is Net ✗ with how much and since when; the cells keep the totals; with no earlier reading the title says so."""
    settings.MANAGEMENT_TIME_ZONE = "Australia/Adelaide"
    port = lambda: ports_json()[0]  # noqa: E731
    first = port()
    assert first["net_ok"] is True and "error counters (no earlier reading to compare with yet)" in first["net_title"]
    assert port()["net_ok"] is True  # the same reading again: still nothing to compare with
    # the next reading: rx errors 0 -> 5
    reader[0].read_at = reader[0].good_at = "2026-10-09T10:00:15+09:30"
    reader[0].ports[0].rx_errors = 5
    later = port()
    assert later["net_ok"] is False and later["net"] == "✗"
    assert later["net_title"] == "not as it should be: rx errors +5 since 2026-10-09 11:00:00"
    assert later["rx_errors"] == "5"  # the cell keeps the total
    assert port()["net_ok"] is False  # every request within that reading compares with the same previous one
    # the reading after: nothing rose
    reader[0].read_at = reader[0].good_at = "2026-10-09T10:00:30+09:30"
    calm = port()
    assert calm["net_ok"] is True and "error counters (none rose since 2026-10-09 11:00:15)" in calm["net_title"]
    # the switch restarted its count: said in the title, not a ✗
    reader[0].read_at = reader[0].good_at = "2026-10-09T10:00:45+09:30"
    reader[0].ports[0].rx_errors = 0
    back = port()
    assert back["net_ok"] is True
    assert "rx error count went back since 2026-10-09 11:00:30 (the switch restarted its count)" in back["net_title"]


def test_error_change_compares_rx_and_tx_apart_and_skips_a_counter_not_read():
    p = PortView(port=1, rx_errors=10, tx_errors=7)
    assert page.error_change(p, "", None) == ([], "error counters (no earlier reading to compare with yet)")
    rose, _ = page.error_change(p, "2026-10-09T10:00:00+00:00", (4, 7))
    assert len(rose) == 1 and rose[0].startswith("rx errors +6 since ")
    rose, _ = page.error_change(p, "2026-10-09T10:00:00+00:00", (10, 2))
    assert len(rose) == 1 and rose[0].startswith("tx errors +5 since ")
    assert page.error_change(PortView(port=1), "2026-10-09T10:00:00+00:00", (1, 1))[0] == []


# --- the review of #137: a phone leaves out empty board ports and counts them --------------------------------------


@pytest.mark.django_db
def test_empty_board_ports_are_marked_and_counted_for_the_phone(reader):
    sw = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)["switches"][0]
    # port 3: nothing registered, no link, no power, as it should be; ports 1 and 2 have a link and power
    assert [p["empty"] for p in sw["ports"]] == [False, False, True] and sw["empty_ports"] == 1
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    assert '<tr id="switch-1-port-3" class="mgmt-empty-port">' in html and '<tr id="switch-1-port-1">' in html
    assert ('<span class="mgmt-empty-count"> &middot; <span class="mgmt-empty-n">1</span> empty board ports not '
            'listed</span>') in html
    script = page_script(html)
    assert 'tr.className = "mgmt-empty-port"' in script and '.mgmt-empty-n").textContent = s.empty_ports' in script


@pytest.mark.django_db
def test_a_port_not_as_it_should_be_or_with_a_machine_is_never_empty(reader):
    reader[0].ports[2].pvid = 1  # port 3: not as the inventory says
    sw = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)["switches"][0]
    assert sw["ports"][2]["empty"] is False and sw["empty_ports"] == 0


@pytest.mark.django_db
def test_the_hardware_columns_sort_empty_ports_after_the_ones_with_a_machine(reader):
    seat("pi-sw1-p2", "10000000aaaa0002", lease=(1, 2))
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    for column in ("c-model", "c-board", "c-serial"):
        sorts = [re.search(rf'<td class="{column}" data-label="[^"]+" data-sort="([^"]*)"', row_of(html, n)).group(1)
                 for n in (1, 2, 3)]
        assert sorts[1].startswith("0 ") and sorts[0] == sorts[2] == "1", (column, sorts)
    leases = [int(re.search(r'class="c-lease" data-label="Lease" data-sort="(\d+)"', row_of(html, n)).group(1))
              for n in (1, 2, 3)]
    assert leases[1] < leases[0] == leases[2]


def test_the_tinytapeout_sites_greyed_symbol_names_its_own_policy():
    """pibfpgas.poe.board_ports on the Tiny Tapeout site switches a port a Pi reported a Tiny Tapeout board on, not a
    registered one (the review of #143): its greyed symbol says so."""
    icon, why = page.power_cell(True, False, "", None, False, "PoE on", tt_site=True)
    assert icon == "⏻" and why.startswith("not switched: no Tiny Tapeout board was reported on this port")


def test_the_poe_and_power_headers_name_the_marks_the_cells_use():
    titles = page.head_titles()
    assert "🔍 PoE on and searching" in titles["poe"] and "○" not in titles["poe"]
    assert "a filled green ⏻ (PoE on, or in a fault) turns its PoE off, a hollow red ⏻ (PoE off)" in titles["power"]


def test_on_and_off_buttons_differ_in_shape_not_only_colour():
    """The coordinator's hold on #143: a filled button for a powered port, a hollow one for a port that is off."""
    import pathlib
    css = (pathlib.Path(page.__file__).parents[1] / "static" / "management" / "management.css").read_text()
    off = css.split(".mgmt-poe-button.mgmt-poe-to-off {", 1)[1].split("}", 1)[0]
    on = css.split(".mgmt-poe-button.mgmt-poe-to-on {", 1)[1].split("}", 1)[0]
    assert "background: #1b7a1b" in off and "color: #fff" in off
    assert "background: #fff" in on and "color: #b00020" in on


@pytest.mark.django_db
def test_an_lldp_neighbours_chassis_id_is_on_hover(monkeypatch):
    """#148: the neighbour's name in the cell, its chassis id (a MAC for a Pi) on hover."""
    views = make_views()
    views[0].ports[2].lldp_name, views[0].ports[2].lldp_port = "rpi5-new-13f59c", "eth0"
    views[0].ports[2].lldp_chassis = "98:fe:54:13:f5:9c"
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    port = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)["switches"][0]["ports"][2]
    assert (port["lldp_name"], port["lldp_title"], port["lldp_port"]) == (
        "rpi5-new-13f59c", "the name it announces; chassis 98:fe:54:13:f5:9c", "eth0")


@pytest.mark.django_db
def test_a_chassis_with_no_name_is_still_said_and_the_panel_box_says_what_the_row_does(monkeypatch):
    """Nothing LLDP reports is hidden (#148): a neighbour that sends a chassis id and no system name has its chassis in
    the cell's title; a port's box title carries the LLDP cell and its title."""
    views = make_views()
    views[0].ports[2].lldp_name, views[0].ports[2].lldp_chassis = None, "98:fe:54:13:f5:4e"
    views[0].ports[1].lldp_name, views[0].ports[1].lldp_chassis = "tweed.welland.example", "00:11:22:33:44:55"
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    data = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)["switches"][0]
    ports = data["ports"]
    assert (ports[2]["lldp_name"], ports[2]["lldp_title"]) == ("", "chassis 98:fe:54:13:f5:4e")
    assert ports[1]["lldp_title"] == "the name it announces: tweed.welland.example; chassis 00:11:22:33:44:55"
    from management import panel
    assert ("LLDP: tweed; the name it announces: tweed.welland.example; chassis 00:11:22:33:44:55"
            in panel.title(ports[1]))



@pytest.mark.django_db
def test_each_machine_shows_its_ram_and_the_column_sorts_by_size(monkeypatch):
    """Tim, 16 Oct 2026 (#151): "Why doesn't the switch page also report the RPi memory?" The RAM in /fleet/'s form, a
    line a machine as Model has, sorted by size (MB), a port with no machine after every machine."""
    views = make_views()
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    machines = {(1, 1): [{"model": "Pi 5", "memory": "8 GB", "memory_mb": 8192, "memory_title": ""}],
                (1, 2): [{"model": "Pi 3B+", "memory": "1 GB", "memory_mb": 1024, "memory_title": ""}]}
    base = {"model_title": "", "board": "Acorn", "board_title": "", "condition": "operational", "mark": "●",
            "state_title": "operational", "severity": 3, "serial": "s", "url": "", "label": "l", "lease_ago": "1 min",
            "lease_at": "2026-10-16 10:00:00", "lease_sort": 1}
    monkeypatch.setattr(page, "on_ports", lambda request: {k: [{**base, **m} for m in v] for k, v in machines.items()})
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    assert '<td class="c-memory" data-label="RAM" data-sort="8192"><div>8 GB</div></td>' in row_of(html, 1)
    assert '<td class="c-memory" data-label="RAM" data-sort="1024"><div>1 GB</div></td>' in row_of(html, 2)
    assert '<td class="c-memory" data-label="RAM" data-sort="1000000000"></td>' in row_of(html, 3)
    assert "the Pi&#x27;s memory, as its label says" in html  # the header's title
    script = page_script(html)
    assert 'td("c-memory", "RAM", "", p.memory_sort)' in script and "[model, memory, board," in script


def test_a_memory_the_page_cannot_size_says_why_on_hover(monkeypatch):
    from types import SimpleNamespace

    from django.utils import timezone

    from management import fpga

    class Fields:
        serial, short, note, label, why, memory = "s", "Pi 5", "", "Pi 5 ? s", "", "?"

        @property
        def memory_mb(self):
            raise ValueError("memory '?' is not a size")

    monkeypatch.setattr(fpga, "pi_fields", lambda machine: Fields())
    monkeypatch.setattr(fpga, "pi_label", lambda machine: "Pi 5 ? s")
    host = SimpleNamespace(machine=None, boards=[], board_type="no board", condition="operational", why="", setup_port=False)
    m = page._machine(None, host, SimpleNamespace(at=timezone.now()))
    assert (m["memory"], m["memory_mb"], m["memory_title"]) == ("?", 0, "memory '?' is not a size")


# --- the Link badge (Tim, 17 Oct 2026, round 5): Link + Speed as one 3-character badge, a click switches the link ---


@dataclass
class BadgePort:
    admin_enabled: bool = True
    link_up: bool = True
    oper_status: int | None = 1
    speed_mbps: int | None = 1000
    rx_errors: int | None = 0
    tx_errors: int | None = 0


DOWN = {"link_up": False, "oper_status": 2, "speed_mbps": None}


@pytest.mark.parametrize("port, want", [
    (BadgePort(**DOWN), ("", "mgmt-badge-none")),                                   # empty / transparent: no link
    (BadgePort(link_up=False, oper_status=7, speed_mbps=None), ("err", "mgmt-badge-err")),  # red: a link fault
    (BadgePort(speed_mbps=100), ("100", "mgmt-badge-fast")),                       # green 100
    (BadgePort(speed_mbps=1000), ("1G", "mgmt-badge-fast")),                       # green 1G
    (BadgePort(speed_mbps=10), ("10", "mgmt-badge-slow")),                         # yellow 10
    (BadgePort(admin_enabled=False, **DOWN), ("off", "mgmt-badge-off")),            # disabled
    (BadgePort(admin_enabled=False, link_up=False, oper_status=7, speed_mbps=None), ("off", "mgmt-badge-off")),  # wins
    (BadgePort(speed_mbps=10000), ("10G", "mgmt-badge-fast")),
    (BadgePort(speed_mbps=2500), ("2G5", "mgmt-badge-fast")),
    (BadgePort(speed_mbps=None), ("↑", "mgmt-badge-fast")),  # up, no speed read: as switch-events.js draws it
    (BadgePort(oper_status=None), ("1G", "mgmt-badge-fast")),  # a backend that cannot tell: no fault
])
def test_each_of_tims_six_badges(port, want):
    text, cls, words, _ = page.link_badge(port)
    assert (text, cls) == want
    assert len(text) <= 3 and words


@pytest.mark.parametrize("code, name", [(3, "testing"), (4, "unknown"), (5, "dormant"), (6, "not present"),
                                        (7, "lower layer down"), (9, "a state IF-MIB does not define")])
def test_red_means_a_link_fault_only(code, name):
    """Tim's welland-r5-1 answer, 17 Oct 2026: "Link fault only": red only when the switch reports the link as neither
    up nor down; the words say which state, with its IF-MIB number."""
    text, cls, words, _ = page.link_badge(BadgePort(link_up=False, oper_status=code, speed_mbps=None))
    assert (text, cls) == ("err", "mgmt-badge-err")
    assert words == f"link fault: the switch reports the link {name} (ifOperStatus {code})"


def test_rising_error_counters_do_not_turn_the_badge_red():
    """The error counts have their own Rx err / Tx err columns (bold bright red above zero); the badge stays the
    link's."""
    assert page.link_badge(BadgePort(rx_errors=500, tx_errors=7))[1] == "mgmt-badge-fast"
    assert page.link_fault(1) == page.link_fault(2) == page.link_fault(None) == ""


@pytest.mark.django_db
def test_the_badge_is_a_button_on_an_access_port_and_a_mark_elsewhere(monkeypatch):
    views = make_views()
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    monkeypatch.setattr(page, "_inventory", lambda: {1: spec()})
    monkeypatch.setattr("pibfpgas.poe.board_ports", lambda request, index, ports: {1})
    c = Client(HTTP_HOST=HOSTS[0])
    ports = {p["port"]: p for p in json.loads(c.get("/management/switches.json").content)["switches"][0]["ports"]}
    assert ports[1]["link_action"] == "disable" and "a click disables the port at the switch" in ports[1]["badge_title"]
    html = c.get("/management/switches/").content.decode()
    assert 'data-action="disable"' in row_of(html, 1) and 'class="mgmt-link-badge mgmt-link-button' in row_of(html, 1)
    others = [p for n, p in ports.items() if n != 1]
    assert others and all(p["link_action"] == "" for p in others)
    n = others[0]["port"]
    assert "mgmt-link-button" not in row_of(html, n) and 'class="mgmt-link-badge mgmt-badge-' in row_of(html, n)


@pytest.mark.django_db
def test_a_port_disabled_at_the_switch_offers_enable(monkeypatch):
    views = make_views()
    views[0].ports[0].admin_enabled = False
    views[0].ports[0].link_up = False
    monkeypatch.setattr(page, "_reader", lambda: lambda cache: views)
    monkeypatch.setattr(page, "_inventory", lambda: {1: spec()})
    monkeypatch.setattr("pibfpgas.poe.board_ports", lambda request, index, ports: {1})
    p = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)["switches"][0]["ports"][0]
    assert (p["badge"], p["badge_cls"], p["link_action"]) == ("off", "mgmt-badge-off", "enable")


@pytest.mark.django_db
def test_a_machine_on_the_setup_port_reads_failed_reason_setup_port(reader, settings):
    # the setup / config port (fleet.services.on_setup_port; tests/test_setup_port.py): its row says why it is not offered
    settings.SETUP_PORT = 1
    seat("pi-sw1-p1", "setup", lease=(1, 1))
    ports = Client(HTTP_HOST="welland.fpgas.online").get("/management/switches.json").json()["switches"][0]["ports"]
    (m,) = next(p for p in ports if p["port"] == 1)["machines"]
    assert m["serial"] == "setup" and m["condition"] == "attention" and m["setup_port"] is True
    assert m["state_title"] == "needs attention: setup port"
