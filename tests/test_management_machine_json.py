"""/fleet/<serial>/machine.json: a host's own page (/fleet/<serial>/) as data, for programs (the fpgas.online MCP).
The page is for people and may change its layout; this is the contract (docs/superpowers/specs/
2026-11-27-machine-json-design.md). One function builds what both show, so they cannot drift."""

import datetime
import html as htmllib
import re

import pytest
from django.test import Client
from django.utils import timezone
from fleet.models import BootEvent, HardwareSnapshot

from tests import test_management_network as net_tests
from tests.test_management_fpgas import REAL, pi
from tests.test_management_netconsole import pi as console_pi
from tests.test_management_netconsole import receiver, write  # noqa: F401 (fixture)
from tests.test_management_network import now as network_now  # noqa: F401 (fixture: the documents' times)
from tests.test_management_switches import HOSTS, reader, seat, welland_inventory  # noqa: F401 (fixtures)

WELLAND = "welland.fpgas.online"
TOP = ("site", "generated_at", "serial", "page", "machine", "boards", "boot_checks", "labels", "network",
       "network_error", "switch_port", "switch_port_error", "registrations", "events", "console", "console_error", "console_not_set_up")
MACHINE = ("hostname", "label", "model", "model_full", "memory", "port", "port_note", "condition", "offered",
           "offered_reason", "board_page", "first_seen", "last_seen", "checked", "uptime_s", "boot_id", "setup_port",
           "cameras")
ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?\+00:00$")
MARKS = ("✓", "✗", "⟳", "⊘", "●")


@pytest.fixture(autouse=True)
def site_address(settings):
    settings.DOMAIN_NAME = WELLAND
    settings.MANAGEMENT_TIME_ZONE = "Australia/Adelaide"
    settings.MANAGEMENT_SITE_NAME = "welland"


def get(serial, query="", status=200):
    response = Client(HTTP_HOST=WELLAND).get(f"/fleet/{serial}/machine.json{query}")
    assert response.status_code == status, response.content[:400]
    assert response["Content-Type"] == "application/json"
    return response.json()


def page(serial):
    response = Client(HTTP_HOST=WELLAND).get(f"/fleet/{serial}/")
    assert response.status_code == 200
    return response.content.decode()


def strings(value, path="$"):
    """Every (path, string) in a JSON value, keys and values both."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from strings(item, f"{path}[{i}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            yield f"{path}.{key}", key
            yield from strings(item, f"{path}.{key}")


def full_pi():
    """A Pi with everything the page shows: a label, two boards, an earlier boot's check, events, a registration."""
    m = pi("pi-sw2-p46", REAL["pi-sw2-p46"], serial="285df3f84af242d0")
    m.latest_snapshot = HardwareSnapshot.objects.create(machine=m, fingerprint="f" * 64, document={
        "machine": {"serial": m.serial, "revision_code": "c04170", "model": "Raspberry Pi 5 Model B Rev 1.0"},
        "peripherals": {"cameras": [{"name": "cam0"}]}})
    m.last_uptime_s = 600
    m.save()
    BootEvent.objects.create(machine=m, boot_id="b0", stage="fpga-verified",
                             ts=timezone.now() - datetime.timedelta(days=1),
                             detail={"result": "pass", "board0": "tt tt-fpga pass"})
    for stage, detail in (("network-online", {}), ("ssh-up", {}),
                          ("fpga-test-finished", {"board": "acorn", "test": "ddr", "result": "fail", "reason": "x"})):
        BootEvent.objects.create(machine=m, boot_id="b1", stage=stage, ts=timezone.now(), detail=detail)
    return m


# --- the shape ------------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_every_key_of_the_contract_is_there_with_its_type():
    m = full_pi()
    data = get(m.serial)
    assert tuple(data) == TOP
    assert data["site"] == "welland" and data["serial"] == m.serial and ISO.match(data["generated_at"])
    # behind nginx the request is http: the addresses are the site's own, https (as the /fpgas/ links are)
    assert data["page"] == f"https://{WELLAND}/fleet/{m.serial}/"
    assert data["machine"]["board_page"] == f"https://{WELLAND}/fpgas/pi-sw2-p46.html"
    machine = data["machine"]
    assert set(MACHINE) <= set(machine)
    assert machine["hostname"] == "pi-sw2-p46" and machine["label"] == "Pi 5 4 GB 285df3f84af242d0"
    assert machine["model"] == "Pi 5" and machine["memory"] == "4 GB" and machine["port"] == "sw2 p46"
    assert machine["condition"] in ("operational", "attention", "missing", "checking")
    assert isinstance(machine["offered"], bool) and isinstance(machine["setup_port"], bool)
    assert machine["offered_reason"] is None if machine["offered"] else isinstance(machine["offered_reason"], str)
    for key in ("first_seen", "last_seen", "checked"):
        assert ISO.match(machine[key]), key
    assert machine["uptime_s"] == 600 and machine["boot_id"] == "b1" and machine["cameras"] == [{"name": "cam0"}]
    board = data["boards"][0]
    assert set(board) >= {"type", "variant", "id", "label", "result", "reason", "tests", "docs"}
    assert all(set(t) == {"result", "reason", "reported_name"} and isinstance(t["result"], str)
               for t in board["tests"].values())
    checks = {c["boot_id"]: c for c in data["boot_checks"]}  # newest by arrival, as the page: b0 arrived last
    check = checks["b1"]
    assert set(check) >= {"boot_id", "this_boot", "at", "running", "result", "reason", "boards"}
    assert list(checks) == ["b0", "b1"] and check["this_boot"] and not checks["b0"]["this_boot"]
    assert ISO.match(check["at"]) and check["running"] is False
    assert set(check["boards"][0]) >= {"type", "result", "reason", "tests"}
    labels = data["labels"]
    assert set(labels) >= {"missing", "notes", "refusals", "images", "label_input"}
    assert labels["label_input"] == f"https://{WELLAND}/fleet/{m.serial}/rpi-hwid.json"
    assert all(set(row) >= {"label", "field", "sent_by", "sent_by_title"} for row in labels["missing"])
    registration = data["registrations"][0]
    assert registration["fingerprint"] == "f" * 64 and registration["current"] is True
    assert ISO.match(registration["first_seen"]) and ISO.match(registration["last_confirmed"])
    assert registration["document"]["peripherals"] == {"cameras": [{"name": "cam0"}]}
    for event in data["events"]:
        assert set(event) >= {"ts", "kind", "stage", "test", "result", "reason", "detail"} and ISO.match(event["ts"])
    ddr = next(e for e in data["events"] if e["stage"] == "fpga-test-finished")
    assert ddr["test"] == "ddr" and ddr["result"] == "fail" and ddr["reason"] == "x" and ddr["kind"] == "test"
    ssh = next(e for e in data["events"] if e["stage"] == "ssh-up")
    assert ssh["detail"] is None and ssh["test"] is None and ssh["result"] is None


@pytest.mark.django_db
def test_an_unknown_serial_is_a_404_in_json():
    assert get("nope", status=404) == {"error": "no host with this serial has registered"}


@pytest.mark.django_db
def test_it_answers_without_a_login_and_is_not_on_tinytapeout():
    m = pi("pi-sw2-p22")
    assert Client(HTTP_HOST="tinytapeout.fpgas.online").get(f"/fleet/{m.serial}/machine.json").status_code == 404


@pytest.mark.django_db
def test_no_string_carries_html_or_a_mark():
    m = full_pi()
    for path, text in strings(get(m.serial)):
        if path.startswith("$.registrations") or ".detail" in path:
            continue  # as the Pi sent them
        assert not re.search(r"<[a-z/][^>]*>|&[a-z]+;|&#", text), (path, text)
        assert not any(mark in text for mark in MARKS), (path, text)


@pytest.mark.django_db
def test_a_host_unknown_fields_are_null_never_guessed():
    m = pi("pi-sw2-p22", lease=False)  # registered, nothing reported, no registration, no port
    m.last_boot_id = ""
    m.save()
    data = get(m.serial)
    machine = data["machine"]
    assert machine["port"] is None and machine["model"] is None and machine["memory"] is None
    assert machine["checked"] is None and machine["uptime_s"] is None and machine["boot_id"] is None
    assert machine["cameras"] is None and machine["board_page"] is None
    assert data["boards"] == [] and data["boot_checks"] == [] and data["registrations"] == []
    assert data["network"] is None and data["network_error"] is None


# --- the limits -----------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_events_keeps_the_newest_n():
    m = pi("pi-sw2-p33")
    start = timezone.now()
    for n in range(5):
        BootEvent.objects.create(machine=m, boot_id="b1", stage=f"stage-{n}", ts=start + datetime.timedelta(seconds=n))
    assert [e["stage"] for e in get(m.serial)["events"]] == [f"stage-{n}" for n in range(5)]
    assert [e["stage"] for e in get(m.serial, "?events=2")["events"]] == ["stage-3", "stage-4"]
    assert get(m.serial, "?events=0")["events"] == []
    assert len(get(m.serial, "?whatever=1")["events"]) == 5  # an unknown parameter is ignored


@pytest.mark.django_db
@pytest.mark.parametrize("query", ["?events=x", "?events=-1", "?console=1.5"])
def test_a_limit_that_is_not_a_count_is_a_400(query):
    m = pi("pi-sw2-p33")
    assert "error" in get(m.serial, query, status=400)


@pytest.mark.django_db
def test_console_keeps_the_newest_n_lines(receiver):  # noqa: F811
    m = console_pi(uptime_s=600)
    booted = m.booted_at
    write(receiver, "10.21.1.2", [(booted + datetime.timedelta(seconds=n), f"line {n}") for n in range(1, 6)])
    data = get(m.serial)
    assert data["console_error"] is None and data["console_not_set_up"] is None
    console = data["console"]
    assert [line["text"] for line in console["lines"]] == [f"line {n}" for n in range(1, 6)]
    assert all(ISO.match(line["at"]) for line in console["lines"]) and ISO.match(console["since"])
    assert console["receiver_late"] is False
    assert [line["text"] for line in get(m.serial, "?console=2")["console"]["lines"]] == ["line 4", "line 5"]
    every = [f"line {n}" for n in range(1, 6)]
    assert [line["text"] for line in get(m.serial, "?console=5")["console"]["lines"]] == every
    assert [line["text"] for line in get(m.serial, "?console=100")["console"]["lines"]] == every
    # more than there are, but less than twice as many: a negative start would cut the oldest off
    assert [line["text"] for line in get(m.serial, "?console=8")["console"]["lines"]] == every
    assert get(m.serial, "?console=0")["console"]["lines"] == []


@pytest.mark.django_db
def test_a_console_not_set_up_is_said_not_an_error(settings):
    settings.NETCONSOLE_DIR = ""
    m = pi("pi-sw2-p33")
    data = get(m.serial)
    assert data["console"] is None and data["console_error"] is None
    assert data["console_not_set_up"] == "the gateway's netconsole receiver is not set up on this site yet"


@pytest.mark.django_db
def test_no_console_to_read_is_its_error(receiver):  # noqa: F811
    m = console_pi(uptime_s=600, leased=False)  # no address: nothing to read lines of
    data = get(m.serial)
    assert data["console_not_set_up"] is None and data["console"] is None
    assert data["console_error"] == "the gateway has leased the Pi no IPv4 address, so its netconsole sender is not known"


# --- the network ----------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_network_record_is_its_values(network_now):  # noqa: F811
    m = net_tests.pi("pi-sw2-p43", REAL["pi-sw2-p46"], serial="285df3f84af242d0")
    net_tests.net_event(m, net_tests.doc())
    data = get(m.serial)
    network = data["network"]
    assert data["network_error"] is None
    assert network["state"] == "routable" and network["administrative_state"] == "configured"
    assert network["interface"] == "eth0" and network["mac"] == "d8:3a:dd:00:00:01"
    assert network["boot_id"] == "b1" and network["current_boot"] is True and network["last_ack_source"] == "pi"
    for key in ("changed", "recorded", "last_ack", "expires"):
        assert ISO.match(network[key]), key
    assert network["addresses"][0] == {"family": 4, "address": "10.21.2.43", "prefixlen": 16, "scope": "global",
                                       "valid_lifetime_s": 3600, "preferred_lifetime_s": 1800}
    assert network["addresses"][1]["valid_lifetime_s"] is None  # forever
    assert network["default_routes"] == [{"family": 4, "gateway": "10.21.0.1", "metric": 1024, "protocol": "dhcp"}]
    assert network["lease"]["address"] == "10.21.2.43" and network["lease"]["lifetime_s"] == 7200
    # the page shows the same addresses
    html = page(m.serial)
    for a in network["addresses"]:
        assert f"<code>{a['address']}/{a['prefixlen']}</code>" in html


@pytest.mark.django_db
def test_a_host_with_no_network_record_but_a_gateway_ack(network_now):  # noqa: F811
    m = net_tests.pi("pi-sw2-p43", REAL["pi-sw2-p46"], serial="285df3f84af242d0")
    net_tests.gateway_ack(m, network_now - datetime.timedelta(minutes=3))
    network = get(m.serial)["network"]
    assert network["last_ack_source"] == "gateway" and ISO.match(network["last_ack"])
    assert network["state"] is None and network["addresses"] == [] and network["lease"] is None
    assert network["changed"] is None and network["boot_id"] is None and network["current_boot"] is None


@pytest.mark.django_db
def test_a_bad_network_record_is_its_error_and_the_rest_stands(network_now):  # noqa: F811
    m = net_tests.pi("pi-sw2-p43", REAL["pi-sw2-p46"], serial="285df3f84af242d0")
    net_tests.net_event(m, net_tests.doc(schema=2))
    data = get(m.serial)
    assert data["network"] is None and "schema" in data["network_error"] and data["boards"]


# --- the switch port ------------------------------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("reader")
def test_the_switch_port_is_its_row_as_switches_json_has_it():
    seat("pi-sw1-p1", "10000000aaaa0001", lease=(1, 1))
    switches = Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").json()["switches"]
    row = next(p for p in switches[0]["ports"] if p["port"] == 1)
    data = get("10000000aaaa0001")
    assert data["switch_port"] == row and data["switch_port_error"] is None


@pytest.mark.django_db
@pytest.mark.usefixtures("reader")
def test_a_pi_on_no_switch_port_is_null_with_why():
    seat("pi-sw1-p1", "10000000aaaa0001")
    data = get("10000000aaaa0001")
    assert data["switch_port"] is None
    assert data["switch_port_error"] == "the gateway has leased this Pi an address on no port of the switches it reads"


# --- the page and the JSON agree ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_page_and_the_json_say_the_same_of_one_machine():
    m = full_pi()
    data = get(m.serial)
    html = page(m.serial)
    assert f"<h1>{data['machine']['label']}</h1>" in html
    boards = html.split('<h3 id="boards">Boards</h3>', 1)[1].split("</table>", 1)[0]
    rows = re.findall(r'<td class="mgmt-c-board" data-label="Board"[^>]*>([^<]+)</td>', boards)
    assert rows == [b["type"] for b in data["boards"]]
    # a cell a check column, its title the reason the JSON gives
    columns = re.findall(r'<th scope="col" class="mgmt-checkhead" title="([a-z0-9-]+): ', boards)
    for b in data["boards"]:
        assert list(b["tests"]) == columns
        for name, test in b["tests"].items():
            title = test["reason"].replace("'", "&#x27;").replace('"', "&quot;")
            assert f'data-label="{name}" data-sort="' in boards and f'title="{title}"' in boards, (name, test)
    history = html.split('<h3 id="boot-checks">Boot checks</h3>', 1)[1].split("</table>", 1)[0]
    shown = re.findall(r'<td class="mgmt-mono" title="([^"]+)">', history)
    assert shown == [c["boot_id"] for c in data["boot_checks"]]
    events = html.split('<h2 id="events">Events', 1)[1].split("</table>", 1)[0]
    assert re.findall(r'<span class="mgmt-ev">[a-z]+</span></td><td>([^<]+)</td>', events) == [
        e["stage"] for e in data["events"]]
    registrations = html.split("Registrations</h2>", 1)[1].split("</table>", 1)[0]
    assert re.findall(r'<td class="mgmt-mono" title="([0-9a-f]+)">', registrations) == [
        r["fingerprint"] for r in data["registrations"]]
    assert data["machine"]["condition"] in html and data["machine"]["port"] in html


@pytest.mark.django_db
def test_a_running_check_is_running_in_the_json():
    m = pi("pi-sw2-p33", verifying=True)
    data = get(m.serial)
    assert data["machine"]["condition"] == "checking"
    check = data["boot_checks"][0]
    assert check["running"] is True and check["result"] == "verifying" and check["boards"] == []


# --- the review's rulings (2026-11-27) ------------------------------------------------------------------------------


def _cls(word):
    return {"pass": "mgmt-test-pass", "not_run": "mgmt-test-notrun", "not_applicable": "mgmt-test-none"}.get(
        word, "mgmt-test-fail")


@pytest.mark.django_db
def test_each_boards_cells_on_the_page_are_its_tests_in_the_json_in_order():
    detail = {"result": "fail", "board0": "acorn - fail", "board0_identity_dna": "0x00200c8664b04854",
              "board0_tests": "pin-id=pass ddr=fail sdk=pass", "board0_reason": "ddr fail: no DRAM answers",
              "board1": "fomu evt pass", "board1_tests": "uart=pass"}
    m = pi("pi-sw1-p17", detail, serial="00000000cc479fd1")
    data = get(m.serial)
    boards = page(m.serial).split('<h3 id="boards">Boards</h3>', 1)[1].split("</table>", 1)[0]
    rows = re.findall(r"<tr>\s*<td class=\"mgmt-c-board\".*?</tr>", boards, re.S)
    assert len(rows) == len(data["boards"]) == 2
    for row, board in zip(rows, data["boards"]):
        cells = re.findall(r'<td class="mgmt-mark" data-label="([^"]+)" data-sort="\d" title="([^"]*)">'
                           r'<span class="([^"]+)">', row)
        assert cells[0][0] == "Result"  # the board's result, then a cell a check column
        assert [(name, htmllib.unescape(title), cls) for name, title, cls in cells[1:]] == [
            (name, t["reason"], _cls(t["result"])) for name, t in board["tests"].items()]


@pytest.mark.django_db
def test_each_boot_checks_cells_on_the_page_are_its_boards_tests_in_the_json():
    m = full_pi()
    data = get(m.serial)
    history = page(m.serial).split('<h3 id="boot-checks">Boot checks</h3>', 1)[1].split("</table>", 1)[0]
    rows = re.findall(r"<tr>\s*<td class=\"mgmt-mono\".*?</tr>", history, re.S)
    assert len(rows) == len(data["boot_checks"]) == 2
    for row, check in zip(rows, data["boot_checks"]):
        cells = re.findall(r'<td class="mgmt-mark" data-label="([^"]+)" data-sort="\d" title="([^"]*)">', row)
        assert cells and check["boards"]
        names = list(check["boards"][0]["tests"])
        assert [name for name, _ in cells] == names
        for name, title in cells:
            assert htmllib.unescape(title) == "; ".join(b["tests"][name]["reason"] for b in check["boards"])


@pytest.mark.django_db
def test_the_boot_on_the_setup_port_is_failed_for_the_setup_port(settings):
    settings.SETUP_PORT = 1
    m = pi("pi-sw2-p1", REAL["pi-sw2-p46"], serial="1000000000000001")
    data = get(m.serial)
    check = data["boot_checks"][0]
    assert check["this_boot"] and check["result"] == "fail" and check["reason"] == "setup port"
    assert data["machine"]["setup_port"] is True and data["machine"]["offered"] is False


@pytest.mark.django_db
def test_an_events_test_is_its_whole_name_and_the_page_keeps_its_cut():
    m = pi("pi-sw2-p33")
    name = "t" * 60
    BootEvent.objects.create(machine=m, boot_id="b1", stage="fpga-test-finished", ts=timezone.now(),
                             detail={"test": name, "result": "pass"})
    assert get(m.serial)["events"][0]["test"] == name
    assert f"<td>{'t' * 40}</td>" in page(m.serial)


@pytest.mark.django_db
def test_events_of_one_time_are_kept_by_arrival():
    m = pi("pi-sw2-p33")
    same = timezone.now()
    for n in range(4):
        BootEvent.objects.create(machine=m, boot_id="b1", stage=f"stage-{n}", ts=same)
    assert [e["stage"] for e in get(m.serial)["events"]] == [f"stage-{n}" for n in range(4)]
    assert [e["stage"] for e in get(m.serial, "?events=2")["events"]] == ["stage-2", "stage-3"]


@pytest.mark.django_db
def test_events_with_no_boot_id_are_the_newest_oldest_first_though_the_page_lists_them_newest_first(monkeypatch):
    from management.views import machine as view

    monkeypatch.setattr(view, "EVENTS", 3)  # the newest EVENTS of any boot
    m = pi("pi-sw2-p33")
    m.last_boot_id = ""
    m.save()
    start = timezone.now()
    for n in range(4):
        BootEvent.objects.create(machine=m, boot_id="x", stage=f"stage-{n}", ts=start + datetime.timedelta(seconds=n))
    assert [e["stage"] for e in get(m.serial)["events"]] == ["stage-1", "stage-2", "stage-3"]
    assert [e["stage"] for e in get(m.serial, "?events=2")["events"]] == ["stage-2", "stage-3"]
    assert [e["stage"] for e in get(m.serial, "?events=9")["events"]] == ["stage-1", "stage-2", "stage-3"]
    events = page(m.serial).split('<h2 id="events">Events', 1)[1].split("</table>", 1)[0]
    assert re.findall(r"<td>(stage-\d)</td>", events) == ["stage-3", "stage-2", "stage-1"]  # the page: unchanged


@pytest.mark.django_db
def test_the_json_does_not_lay_out_the_pages_html(monkeypatch):
    from fleet import jsonview

    m = full_pi()
    monkeypatch.setattr(jsonview, "render", lambda value: pytest.fail("machine.json laid out HTML"))
    get(m.serial)


@pytest.mark.django_db
def test_a_reason_there_is_none_of_is_null():
    m = full_pi()
    data = get(m.serial)
    assert data["boards"][0]["reason"] is None
    assert all(b["reason"] is None for c in data["boot_checks"] for b in c["boards"])
    for path, text in strings(data):
        if not (path.startswith("$.registrations") or ".detail" in path or path.startswith("$.switch_port")):
            assert text != "", path


@pytest.mark.django_db
def test_a_network_record_sent_with_no_boot_id_has_a_null_boot_id(network_now):  # noqa: F811
    m = net_tests.pi("pi-sw2-p43", REAL["pi-sw2-p46"], serial="285df3f84af242d0")
    net_tests.net_event(m, net_tests.doc(), boot_id="")
    assert get(m.serial)["network"]["boot_id"] is None


@pytest.mark.django_db
def test_a_limit_past_what_the_database_counts_is_all(receiver):  # noqa: F811
    m = console_pi(uptime_s=600)
    booted = m.booted_at
    write(receiver, "10.21.1.2", [(booted + datetime.timedelta(seconds=n), f"line {n}") for n in range(1, 4)])
    for n in range(3):
        BootEvent.objects.create(machine=m, boot_id="b1", stage=f"stage-{n}", ts=booted + datetime.timedelta(seconds=n))
    huge = "99999999999999999999"
    assert [e["stage"] for e in get(m.serial, f"?events={huge}")["events"]] == [f"stage-{n}" for n in range(3)]
    assert [line["text"] for line in get(m.serial, f"?console={huge}")["console"]["lines"]] == [
        f"line {n}" for n in range(1, 4)]


@pytest.mark.django_db
def test_a_tests_key_is_the_pages_column_and_its_reported_name_is_as_sent():
    detail = {"result": "pass", "board0": "acorn - pass", "board0_identity_dna": "0x00200c8664b04854",
              "board0_tests": "pin-id=pass ddr=pass"}
    m = pi("pi-sw1-p17", detail, serial="00000000cc479fd1")
    data = get(m.serial)
    tests = data["boards"][0]["tests"]
    assert tests["wiring"]["reported_name"] == "pin-id" and tests["ddr"]["reported_name"] == "ddr"
    assert all(t["reported_name"] is None for name, t in tests.items() if name not in ("wiring", "ddr"))
    history = data["boot_checks"][0]["boards"][0]["tests"]
    assert history["wiring"]["reported_name"] == "pin-id" and history["ddr"]["reported_name"] == "ddr"
