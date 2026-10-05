"""The PoE endpoints (/snmp/status, /snmp/toggle; the snmp_switch app from
the fpgas-online-poe package) act on a board's own port and nothing beyond
(pibfpgas/poe.py), once per port per interval.

Visitors use the boards and press Reset, with no login: every board keeps
its Reset, above all one that has hung, is restarting or is failing its
check. What is refused is a port no board is registered on, and, whatever
has been registered, a trunk, an uplink or a port outside a switch's access
ports.

Run against the installed fpgas-online-poe package with this project's own
settings and URL routing, on both hosts, and with a switch client that
records what it is asked instead of speaking to a switch. If the installed
package did not enforce the policy, the refusals here would not happen:
that is what keeps a site that names a policy from being paired, unnoticed,
with a package that never asks it.

The port numbers are fixture data: nothing here says what is on which port
of a real switch.
"""

import datetime
import json
import textwrap
import types

import pytest
from django.test import Client
from django.utils import timezone
from django.utils.module_loading import import_string
from fleet.models import Machine
from netgear_switch.errors import NetgearSwitchError
from pibfpgas.checks import poe_package_enforces_the_port_policy
from snmp_switch import switches
from snmp_switch.policy import PORT_POLICY_SETTING
from snmp_switch.switches import is_access_port
from ttsite.models import Board

import pib.settings
from fleet import consumer
from tests.fleet_pis import machine, registered, verified_pi, verifying

WELLAND = "welland.fpgas.online"
TINYTAPEOUT = "tinytapeout.fpgas.online"

# The switches file of a per-port-VLAN site (what infra renders for
# fpgas-switch-setup): two switches, each with ports that are not for boards.
SWITCHES = textwrap.dedent("""
    switches:
      - index: 1
        model: gsm7252ps
        mgmt_host: 192.0.2.1
        access_ports: 40
        gateway_trunk_port: 47
        downstream_trunk_ports: [50]
        house_uplink_port: 48
      - index: 2
        model: s3300
        mgmt_host: 192.0.2.2
        access_ports: 48
        gateway_trunk_port: 51
        downstream_trunk_ports: []
        house_uplink_port: 52
    """)
# gateway trunk, uplink and downstream trunk of the first; trunk and uplink of
# the second; then ports outside the first's access ports and off the end
NOT_FOR_BOARDS = [(1, 47), (1, 48), (1, 50), (2, 51), (2, 52), (1, 41), (1, 46), (2, 49), (2, 60), (2, 999)]


def forge(hostname, serial="f0rged"):
    """A registration nobody checked: the three messages anything on the
    site LAN can publish to the fleet broker to look like a board that
    registered on `hostname`, is online and passed its check."""
    topic = f"fpgas/welland/pi/{serial}/"
    assert consumer.dispatch(topic + "registration", json.dumps(
        {"machine": {"serial": serial}, "connection": {"site": "welland", "hostname": hostname}})) == "registration"
    assert consumer.dispatch(topic + "status", json.dumps({"online": True, "boot_id": "x", "uptime_s": 9})) == "status"
    assert consumer.dispatch(topic + "event", json.dumps(
        {"stage": "fpga-verified", "boot_id": "x", "detail": {"result": "pass"}})) == "event"


def post(host, path, body):
    data = body if isinstance(body, str) else json.dumps(body)
    return Client(HTTP_HOST=host).post(path, data=data, content_type="application/json")


@pytest.fixture(autouse=True)
def no_switch_env(monkeypatch):
    import os
    for k in list(os.environ):
        if k.startswith(("SNMP_SWITCH_", "FPGAS_SWITCH")):
            monkeypatch.delenv(k)
    # the view's pause between off and on
    monkeypatch.setattr("snmp_switch.views.time", types.SimpleNamespace(sleep=lambda seconds: None))


@pytest.fixture
def switch_calls(tmp_path, monkeypatch, db):
    """A per-port-VLAN site with two switches. Returns everything the views
    asked a switch: [] means no switch heard of the request."""
    calls = []

    class RecordingSwitch:
        def __init__(self, model, host, **kwargs):
            self.host = host

        def get_poe(self):
            calls.append((self.host, "get"))
            return [types.SimpleNamespace(port=port, admin_enabled=True) for port in range(1, 53)]

        def set_poe(self, port, on):
            calls.append((self.host, "set", port, on))

    cfg = tmp_path / "switches.yml"
    cfg.write_text(SWITCHES)
    monkeypatch.setenv("FPGAS_SWITCHES_CONFIG", str(cfg))
    monkeypatch.setenv("FPGAS_SWITCH_COMMUNITY", "not-a-real-community")
    monkeypatch.setattr(switches, "SyncSwitch", RecordingSwitch)
    return calls


@pytest.fixture
def flat_calls(monkeypatch, db):
    """A legacy flat site: one SNMPv3 switch with no index, Pis registered
    as pi<port>. Returns the SNMP calls made."""
    calls = []

    async def get_state(**params):
        calls.append(("get", params["port"]))
        return {"state": "on"}

    async def set_state(state, **params):
        calls.append(("set", params["port"], state))
        return {"state": {"1": "on", "2": "off"}[state]}

    monkeypatch.setenv("SNMP_SWITCH_HOST", "192.0.2.3")
    monkeypatch.setattr(switches, "mk_params", dict)
    monkeypatch.setattr(switches, "snmp_get_state", get_state)
    monkeypatch.setattr(switches, "snmp_set_state", set_state)
    return calls


def tt(usb_serial, chip="asic"):
    """A Tiny Tapeout board as a Pi's boot check reports it."""
    return ("tt", "tt-fpga", {"usb_serial": usb_serial, "chip": chip})


TT06, TT07, UNLISTED = "06060606aaaa0006", "07070707aaaa0007", "f0f0f0f0aaaa0009"


@pytest.fixture
def tt_boards(db):
    """The Tiny Tapeout site's boards: where each is comes from the Pi that reported it, never from its
    catalogue row. tt03 and kianv-1 are rows no Pi reports; the board on switch 2 port 9 has no row."""
    Board.objects.create(slug="tt06", usb_serial=TT06, kind="asic", title="Tiny Tapeout 6")
    Board.objects.create(slug="tt03", kind="asic", title="Tiny Tapeout 3")
    Board.objects.create(slug="kianv-1", kind="kianv", title="KianV uLinux SoC")
    Board.objects.create(slug="tt07", usb_serial=TT07, kind="asic", title="Tiny Tapeout 7")
    verified_pi("pi-sw1-p6", tt(TT06))
    verified_pi("pi-sw2-p7", tt(TT07))
    verified_pi("pi-sw2-p9", tt(UNLISTED, "fpga"))


CYCLE_SW2_P46 = [("192.0.2.2", "set", 46, False), ("192.0.2.2", "get"), ("192.0.2.2", "set", 46, True), ("192.0.2.2", "get")]


# --- the project is wired to the package's policy hook ----------------------


def test_the_settings_name_the_site_policy_under_the_name_the_package_reads():
    """PORT_POLICY_SETTING and is_access_port are imported from the installed
    fpgas-online-poe: a package from before it enforced a policy, or from
    before it bounded the policy to access ports, fails this file at import."""
    assert PORT_POLICY_SETTING == "SNMP_SWITCH_PORT_POLICY"
    assert callable(is_access_port)
    from pibfpgas.poe import board_port
    assert import_string(getattr(pib.settings, PORT_POLICY_SETTING)) is board_port


def test_the_rate_limit_store_is_shared_by_the_workers_in_production():
    """gunicorn runs several worker processes: per-process memory would give
    each its own limit. (The tests swap in local memory: tests/conftest.py.)"""
    alias = pib.settings.SNMP_SWITCH_RATE_LIMIT_CACHE
    assert pib.settings.CACHES[alias]["BACKEND"] == "django.core.cache.backends.redis.RedisCache"
    assert pib.settings.SNMP_SWITCH_TOGGLE_INTERVAL == 60


def test_the_deploy_check_passes_with_a_package_that_enforces_the_policy():
    assert poe_package_enforces_the_port_policy(None) == []


def test_the_deploy_check_fails_beside_a_package_that_does_not(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "snmp_switch.policy", None)  # as if the module did not exist
    errors = poe_package_enforces_the_port_policy(None)
    assert [e.id for e in errors] == ["pibfpgas.E001"] and "not a board's" in errors[0].msg


def test_the_deploy_check_fails_beside_a_package_with_the_policy_but_no_access_port_bound(monkeypatch):
    """The first version of the package's fix asked the policy and nothing
    else: a forged registration reached trunks and uplinks through it."""
    monkeypatch.delattr(switches, "is_access_port")
    assert [e.id for e in poe_package_enforces_the_port_policy(None)] == ["pibfpgas.E001"]


def test_the_deploy_check_is_registered():
    """So that `manage.py migrate`, which every deploy runs, stops on it."""
    from django.core.checks.registry import registry
    assert poe_package_enforces_the_port_policy in registry.get_checks()


@pytest.mark.parametrize("host", [WELLAND, TINYTAPEOUT])
@pytest.mark.parametrize("path", ["/snmp/status", "/snmp/toggle"])
def test_the_package_refuses_everything_when_no_policy_is_named(switch_calls, settings, host, path):
    verified_pi("pi-sw2-p46")
    settings.SNMP_SWITCH_PORT_POLICY = None
    r = post(host, path, {"port": "46", "switch": 2})
    assert r.status_code == 503
    assert "SNMP_SWITCH_PORT_POLICY is not set" in r.json()["error"]
    assert switch_calls == []


# --- the welland site: a port a board is registered on ----------------------


@pytest.mark.django_db
def test_a_listed_boards_port_is_power_cycled(switch_calls):
    verified_pi("pi-sw2-p46", ("acorn", "cle-215+"))
    # exactly what the board page's Reset button sends (dcws.js poe_body)
    r = post(WELLAND, "/snmp/toggle", {"port": "46", "switch": 2})
    assert r.status_code == 200
    assert r.json() == {"46": ["on", "on"]}  # the recording switch always reports on
    assert switch_calls == CYCLE_SW2_P46


@pytest.mark.django_db
def test_a_listed_boards_port_reports_its_state(switch_calls):
    verified_pi("pi-sw2-p46")
    r = post(WELLAND, "/snmp/status", {"port": "46", "switch": 2})
    assert (r.status_code, r.json()) == (200, {"state": "on"})
    assert switch_calls == [("192.0.2.2", "get")]


@pytest.mark.parametrize("path", ["/snmp/status", "/snmp/toggle"])
@pytest.mark.parametrize("switch, port", NOT_FOR_BOARDS + [(2, 45), (1, 30), (2, 4), (2, 6)])
def test_an_uplink_trunk_out_of_range_or_empty_port_is_refused(switch_calls, path, switch, port):
    """With boards registered on switch 2 port 46 and switch 1 port 6: not
    their numbers on the other switch (1, 46 and 2, 6), not a neighbour, not
    a port whose number only begins like theirs (2, 4)."""
    verified_pi("pi-sw2-p46")
    verified_pi("pi-sw1-p6")
    r = post(WELLAND, path, {"port": str(port), "switch": switch})
    assert r.status_code == 403
    assert r.json() == {"error": f"switch {switch} port {port} is not a board this site offers; "
                                 "nothing was sent to the switch"}
    assert switch_calls == []


# Every board keeps its Reset, whatever state it is in: the site has no page
# for these boards just now, and each of them is one a visitor (or the board's
# own page, left open) needs to be able to power-cycle.


def cycled(host, switch, port):
    mgmt = f"192.0.2.{switch}"
    return [(mgmt, "set", port, False), (mgmt, "get"), (mgmt, "set", port, True), (mgmt, "get")]


@pytest.mark.parametrize("result", ["fail", "missing"])
def test_a_registered_board_that_is_failing_its_check_can_be_reset(switch_calls, result):
    registered("s1", "pi-sw2-p44", result)
    assert Client(HTTP_HOST=WELLAND).get("/fpgas/pi-sw2-p44.html").status_code == 404  # not listed
    assert post(WELLAND, "/snmp/status", {"port": "44", "switch": 2}).status_code == 200
    switch_calls.clear()
    assert post(WELLAND, "/snmp/toggle", {"port": "44", "switch": 2}).status_code == 200
    assert switch_calls == cycled(WELLAND, 2, 44)


def test_a_hung_board_can_be_reset(switch_calls):
    """It passed its check, then stopped: no beat for an hour, so the pages
    dropped it long ago. This is the board Reset is for."""
    m = verified_pi("pi-sw2-p46")
    m.last_seen = timezone.now() - datetime.timedelta(hours=1)
    m.save()
    assert Client(HTTP_HOST=WELLAND).get("/fpgas/pi-sw2-p46.html").status_code == 404
    assert post(WELLAND, "/snmp/toggle", {"port": "46", "switch": 2}).status_code == 200
    assert switch_calls == cycled(WELLAND, 2, 46)


def test_a_board_that_went_offline_long_ago_can_be_reset(switch_calls):
    """No age cut-off: its last will was heard a month ago."""
    m = verified_pi("pi-sw2-p46")
    m.online = False
    m.last_seen = timezone.now() - datetime.timedelta(days=30)
    m.save()
    assert post(WELLAND, "/snmp/toggle", {"port": "46", "switch": 2}).status_code == 200
    assert switch_calls == cycled(WELLAND, 2, 46)


def test_a_board_that_is_restarting_can_be_reset_and_asked_its_state(switch_calls):
    """Its check is running again, so it is not listed until it passes: the
    board page left open still gets its "Check PoE" answer."""
    verifying(machine("s3", "pi-sw2-p46"))
    assert Client(HTTP_HOST=WELLAND).get("/fpgas/pi-sw2-p46.html").status_code == 404
    assert post(WELLAND, "/snmp/status", {"port": "46", "switch": 2}).json() == {"state": "on"}
    assert post(WELLAND, "/snmp/toggle", {"port": "46", "switch": 2}).status_code == 200


def test_a_board_that_registered_and_has_run_no_check_can_be_reset(switch_calls):
    machine("s4", "pi-sw1-p12")
    assert post(WELLAND, "/snmp/toggle", {"port": "12", "switch": 1}).status_code == 200
    assert switch_calls == cycled(WELLAND, 1, 12)


def test_a_board_this_sites_pages_do_not_show_can_be_reset(switch_calls):
    """It carries only a Tiny Tapeout ASIC board, which the /fpgas/ pages do
    not list (pibfpgas.pis.listed); it is a registered board all the same."""
    verified_pi("pi-sw1-p12", ("tt", "tt-asic"))
    assert post(WELLAND, "/snmp/toggle", {"port": "12", "switch": 1}).status_code == 200


def test_several_machines_registered_on_one_port_is_still_yes(switch_calls):
    registered("old", "pi-sw2-p46", "fail")
    verified_pi("pi-sw2-p46", serial="new")
    assert post(WELLAND, "/snmp/toggle", {"port": "46", "switch": 2}).status_code == 200


def test_a_registered_name_with_a_domain_names_its_port(switch_calls):
    machine("s5", "pi-sw2-p46.welland.fpgas.online")
    assert post(WELLAND, "/snmp/toggle", {"port": "46", "switch": 2}).status_code == 200


@pytest.mark.parametrize("hostname", [
    "pi-sw2-p046", "pi-sw02-p46", "pi-sw2-p46x", "pi-sw2-p460", "pi-sw2-p4", "xpi-sw2-p46", "PI-SW2-P46",
    "pi-sw2-p46\n", "pi46", "opi-sw2-p46", "", "gateway",
])
def test_only_the_one_spelling_of_a_ports_name_names_it(switch_calls, hostname):
    machine("s6", hostname)
    assert post(WELLAND, "/snmp/toggle", {"port": "46", "switch": 2}).status_code == 403
    assert switch_calls == []


@pytest.mark.parametrize("path", ["/snmp/status", "/snmp/toggle"])
def test_a_policy_that_cannot_answer_is_not_a_yes(switch_calls, monkeypatch, path):
    """The registry cannot be read (a locked database, say): an error, loud
    in the log, and nothing sent to the switch."""
    verified_pi("pi-sw2-p46")

    def locked(switch, port):
        raise RuntimeError("database is locked")

    monkeypatch.setattr("pibfpgas.poe.registered_on", locked)
    client = Client(HTTP_HOST=WELLAND, raise_request_exception=False)
    r = client.post(path, data=json.dumps({"port": 46, "switch": 2}), content_type="application/json")
    assert r.status_code == 500
    assert switch_calls == []


# --- a registration nobody checked -------------------------------------------
#
# The registry holds what boards say about themselves, and the broker takes
# it from anything on the site LAN. The package bounds what that can reach.


@pytest.mark.parametrize("path", ["/snmp/status", "/snmp/toggle"])
@pytest.mark.parametrize("switch, port", NOT_FOR_BOARDS)
def test_a_forged_registration_cannot_open_a_trunk_an_uplink_or_a_port_outside_the_access_ports(
        switch_calls, path, switch, port):
    forge(f"pi-sw{switch}-p{port}")
    r = post(WELLAND, path, {"port": port, "switch": switch})
    assert r.status_code == 403
    assert r.json() == {"error": f"switch {switch} port {port} is not a board this site offers; "
                                 "nothing was sent to the switch"}
    assert switch_calls == []


def test_a_forged_registration_reaches_an_access_port_and_no_further(switch_calls):
    """What forging can do: name an access port. That is another board,
    which any visitor may reset anyway, or (here) an empty port."""
    assert post(WELLAND, "/snmp/toggle", {"port": 30, "switch": 1}).status_code == 403
    forge("pi-sw1-p30")
    assert post(WELLAND, "/snmp/toggle", {"port": 30, "switch": 1}).status_code == 200
    assert switch_calls == cycled(WELLAND, 1, 30)


def test_a_forged_registration_on_a_switch_the_site_does_not_have_is_a_400(switch_calls):
    forge("pi-sw3-p5")
    assert post(WELLAND, "/snmp/toggle", {"port": 5, "switch": 3}).status_code == 400
    assert switch_calls == []


def test_a_flat_site_has_no_switch_description_to_bound_a_registration(flat_calls):
    """Recorded, not wanted: the legacy single switch has no switches file,
    so nothing says which of its ports are access ports, and a port a
    registration names is accepted whatever it is."""
    forge("pi48")
    assert post(WELLAND, "/snmp/toggle", {"port": 48}).status_code == 200
    assert flat_calls == [("set", "48", "2"), ("set", "48", "1")]


# --- the legacy flat scheme (pi<port>, one switch with no index) ------------


def test_flat_scheme_power_cycles_a_registered_board(flat_calls, settings):
    settings.DOMAIN_NAME = WELLAND
    settings.PI_PW = "cGFzc3dvcmQ="
    verified_pi("pi9")
    # what the flat site's board page sends: the port alone
    assert 'PiStatus("9", null, "pi9")' in Client(HTTP_HOST=WELLAND).get("/fpgas/pi9.html").content.decode()
    r = post(WELLAND, "/snmp/toggle", {"port": "9"})
    assert (r.status_code, r.json()) == (200, {"9": ["off", "on"]})
    assert flat_calls == [("set", "9", "2"), ("set", "9", "1")]


@pytest.mark.parametrize("path", ["/snmp/status", "/snmp/toggle"])
def test_flat_scheme_refuses_a_port_with_no_registered_board(flat_calls, path):
    verified_pi("pi9")
    verified_pi("pi90")
    for port in ("10", "11", "48", "900"):
        r = post(WELLAND, path, {"port": port})
        assert r.status_code == 403
        assert f"port {port} is not a board this site offers" in r.json()["error"]
    assert flat_calls == []


def test_flat_scheme_resets_a_board_that_is_failing_its_check(flat_calls):
    registered("s2", "pi11", "fail")
    assert post(WELLAND, "/snmp/toggle", {"port": "11"}).status_code == 200
    assert flat_calls == [("set", "11", "2"), ("set", "11", "1")]


def test_a_flat_registration_does_not_open_a_per_port_vlan_port(switch_calls):
    verified_pi("pi46")
    assert post(WELLAND, "/snmp/toggle", {"port": "46", "switch": 2}).status_code == 403
    assert post(WELLAND, "/snmp/toggle", {"port": "46", "switch": 1}).status_code == 403
    assert switch_calls == []


def test_a_vlan_per_port_pi_does_not_open_a_flat_port(flat_calls):
    """pi-sw2-p9 is not pi9: on a flat switch the request names no switch,
    and matches only a Pi registered in the flat scheme."""
    verified_pi("pi-sw2-p9")
    assert post(WELLAND, "/snmp/toggle", {"port": "9"}).status_code == 403
    assert flat_calls == []


# --- one power cycle per port per interval ----------------------------------


def test_a_second_toggle_inside_the_interval_is_a_429_and_the_switch_is_not_called(switch_calls):
    verified_pi("pi-sw2-p46")
    verified_pi("pi-sw2-p45")
    assert post(WELLAND, "/snmp/toggle", {"port": "46", "switch": 2}).status_code == 200
    assert switch_calls == CYCLE_SW2_P46
    r = post(WELLAND, "/snmp/toggle", {"port": "46", "switch": 2})
    assert r.status_code == 429
    assert 1 <= int(r["Retry-After"]) <= 60
    assert r.json() == {"error": f"switch 2 port 46 was power-cycled a moment ago; "
                                 f"try again in {r['Retry-After']} seconds. Nothing was sent to the switch"}
    assert switch_calls == CYCLE_SW2_P46
    # the limit is per port: the next board along is not held up
    assert post(WELLAND, "/snmp/toggle", {"port": "45", "switch": 2}).status_code == 200
    # and reading the state is not limited
    assert post(WELLAND, "/snmp/status", {"port": "46", "switch": 2}).status_code == 200


def test_toggle_is_refused_when_the_rate_limit_store_is_missing(switch_calls, settings):
    verified_pi("pi-sw2-p46")
    settings.SNMP_SWITCH_RATE_LIMIT_CACHE = "no-such-cache"
    r = post(WELLAND, "/snmp/toggle", {"port": "46", "switch": 2})
    assert r.status_code == 503
    assert "rate limit store" in r.json()["error"]
    assert switch_calls == []


def test_a_port_left_off_by_a_switch_that_missed_on_can_be_reset_again_at_once(switch_calls, monkeypatch):
    """The switch takes "off" and then does not answer "on", three times
    over: the answer says the port may be off, and the next Reset is not
    told to wait a minute while the board sits without power."""
    verified_pi("pi-sw2-p46")
    failing = [True]
    real_set = switches.LibraryPort.set

    def set_(self, on):
        if on and failing[0]:
            switch_calls.append((self.switch.host, "set", self.port, True, "no answer"))
            raise NetgearSwitchError("timeout")
        return real_set(self, on)

    monkeypatch.setattr(switches.LibraryPort, "set", set_)
    r = post(WELLAND, "/snmp/toggle", {"port": "46", "switch": 2})
    assert r.status_code == 502
    assert "The port may be off: press Reset again" in r.json()["error"]
    assert [c for c in switch_calls if c[1] == "set"] == \
        [("192.0.2.2", "set", 46, False)] + [("192.0.2.2", "set", 46, True, "no answer")] * 3
    failing[0] = False
    assert post(WELLAND, "/snmp/toggle", {"port": "46", "switch": 2}).status_code == 200


def test_on_is_tried_again_when_the_switch_misses_it_once(switch_calls, monkeypatch):
    verified_pi("pi-sw2-p46")
    missed = []
    real_set = switches.LibraryPort.set

    def set_(self, on):
        if on and not missed:
            missed.append(self.port)
            raise NetgearSwitchError("timeout")
        return real_set(self, on)

    monkeypatch.setattr(switches.LibraryPort, "set", set_)
    r = post(WELLAND, "/snmp/toggle", {"port": "46", "switch": 2})
    assert (r.status_code, r.json()) == (200, {"46": ["on", "on"]})
    assert missed == [46] and ("192.0.2.2", "set", 46, True) in switch_calls


# --- the legacy bulk routes --------------------------------------------------


@pytest.mark.parametrize("host", [WELLAND, TINYTAPEOUT])
@pytest.mark.parametrize("path", ["/snmp/toggle_all", "/snmp/off_all"])
def test_the_bulk_routes_are_gone(flat_calls, host, path):
    client = Client(HTTP_HOST=host)
    assert client.get(path).status_code == 404
    assert client.post(path, data="{}", content_type="application/json").status_code == 404
    assert flat_calls == []


# --- malformed requests -------------------------------------------------------


@pytest.mark.parametrize("host", [WELLAND, TINYTAPEOUT])
@pytest.mark.parametrize("body", [
    {}, {"switch": 2}, {"port": "forty-six", "switch": 2}, {"port": -46, "switch": 2}, {"port": "-46", "switch": 2},
    {"port": 0, "switch": 2}, {"port": 10 ** 30, "switch": 2}, {"port": "9" * 5000, "switch": 2},
    {"port": 46.0, "switch": 2}, {"port": "046", "switch": 2}, {"port": [46], "switch": 2},
    {"port": "46"},  # two switches: which one?
    {"port": "46", "switch": 3}, {"port": "46", "switch": "two"}, {"port": "46", "switch": -2},
    [46], "46", "not json at all {",
])
def test_a_malformed_request_is_a_clean_400(switch_calls, tt_boards, host, body):
    verified_pi("pi-sw2-p46")
    for path in ("/snmp/status", "/snmp/toggle"):
        r = post(host, path, body if not isinstance(body, str) or body.startswith("not") else json.dumps(body))
        assert r.status_code == 400, body
        assert r.json()["error"]
    assert switch_calls == []


# --- the Tiny Tapeout site: the Pi of a board a boot check named, on either switch ---


def test_tt_board_page_button_power_cycles_its_board(switch_calls, tt_boards):
    html = Client(HTTP_HOST=TINYTAPEOUT).get("/board/tt06/").content.decode()
    assert 'id="tt-power"' in html and 'data-port="6"' in html and 'data-switch="1"' in html
    # what board.js sends from those attributes
    r = post(TINYTAPEOUT, "/snmp/toggle", {"port": "6", "switch": 1})
    assert r.status_code == 200
    assert switch_calls == [("192.0.2.1", "set", 6, False), ("192.0.2.1", "get"),
                            ("192.0.2.1", "set", 6, True), ("192.0.2.1", "get")]
    assert post(TINYTAPEOUT, "/snmp/toggle", {"port": "6", "switch": 1}).status_code == 429


def test_tt_board_on_the_second_switch_has_the_button_and_is_power_cycled(switch_calls, tt_boards):
    html = Client(HTTP_HOST=TINYTAPEOUT).get("/board/tt07/").content.decode()
    assert 'id="tt-power"' in html and 'data-port="7"' in html and 'data-switch="2"' in html
    r = post(TINYTAPEOUT, "/snmp/toggle", {"port": "7", "switch": 2})
    assert r.status_code == 200
    assert switch_calls == cycled(TINYTAPEOUT, 2, 7)


@pytest.mark.parametrize("path", ["/snmp/status", "/snmp/toggle"])
@pytest.mark.parametrize("switch, port, why", [
    (1, 3, "tt03 is a catalogue row no Pi reports: no button, and no port to name"),
    (2, 6, "tt06's port number, on the other switch"),
    (1, 7, "tt07's port number, on the other switch"),
    (1, 5, "no board"),
    (1, 47, "gateway trunk"), (1, 48, "uplink"), (1, 50, "trunk"), (2, 51, "trunk"), (2, 52, "uplink"),
    (2, 46, "a board registered with the fleet, which this site does not show"),
])
def test_tt_site_refuses_every_port_that_is_not_one_of_its_boards(switch_calls, tt_boards, path, switch, port, why):
    verified_pi("pi-sw2-p46")
    r = post(TINYTAPEOUT, path, {"port": str(port), "switch": switch})
    assert r.status_code == 403, why
    assert "is not a board this site offers" in r.json()["error"]
    assert switch_calls == []


def test_a_tt_boards_pi_is_a_registered_board_on_the_welland_site_too(switch_calls, tt_boards):
    """A Tiny Tapeout board is now found from its Pi's registration, so that Pi is a registered board like
    any other and the welland site may reset it (it resets any registered board, whatever it carries). The
    other way round stays closed: the Tiny Tapeout site refuses a Pi that reported no Tiny Tapeout board
    (test_tt_site_refuses_every_port_that_is_not_one_of_its_boards, switch 2 port 46)."""
    assert post(WELLAND, "/snmp/toggle", {"port": "6", "switch": 1}).status_code == 200
    assert switch_calls == cycled(WELLAND, 1, 6)


def test_tt_button_and_policy_use_the_same_answer(tt_boards):
    from pibfpgas.poe import board_port

    from ttsite import boards
    request = types.SimpleNamespace(get_host=lambda: TINYTAPEOUT)
    pages = boards.pages()
    assert {p.slug for p in pages} == {"tt06", "tt03", "kianv-1", "tt07", "tt-" + UNLISTED}
    for page in pages:
        html = Client(HTTP_HOST=TINYTAPEOUT).get(f"/board/{page.slug}/").content.decode()
        shown = 'id="tt-power"' in html
        assert shown == page.can_power_cycle == (page.live is not None), page.slug
        if shown:
            assert f'data-port="{page.port}"' in html and f'data-switch="{page.switch}"' in html
            assert board_port(request, page.switch, page.port), page.slug


def test_a_tt_board_moved_to_another_port_takes_its_reset_with_it(switch_calls, tt_boards):
    """Tim moved a board on 5 October 2026: nothing but the Pi's own registration says where it is."""
    Machine.objects.filter(hostname="pi-sw2-p7").update(hostname="pi-sw2-p13")
    html = Client(HTTP_HOST=TINYTAPEOUT).get("/board/tt07/").content.decode()
    assert 'id="tt-power"' in html and 'data-port="13"' in html and 'data-switch="2"' in html
    assert post(TINYTAPEOUT, "/snmp/toggle", {"port": "13", "switch": 2}).status_code == 200
    assert switch_calls == cycled(TINYTAPEOUT, 2, 13)
    assert post(TINYTAPEOUT, "/snmp/status", {"port": "7", "switch": 2}).status_code == 403  # where it was


def test_a_tt_board_with_no_catalogue_row_can_be_reset(switch_calls, tt_boards):
    html = Client(HTTP_HOST=TINYTAPEOUT).get(f"/board/tt-{UNLISTED}/").content.decode()
    assert 'id="tt-power"' in html and 'data-port="9"' in html and 'data-switch="2"' in html
    assert post(TINYTAPEOUT, "/snmp/toggle", {"port": "9", "switch": 2}).status_code == 200
    assert switch_calls == cycled(TINYTAPEOUT, 2, 9)


def test_a_tt_board_whose_pi_has_stopped_reporting_can_still_be_reset(switch_calls, tt_boards):
    """A hung board is the one to reset: its Pi's last report still says which board it carries and its last
    registration says where, long after its last status beat."""
    long_ago = timezone.now() - datetime.timedelta(days=3)
    Machine.objects.filter(hostname="pi-sw1-p6").update(online=False, last_seen=long_ago)
    html = Client(HTTP_HOST=TINYTAPEOUT).get("/board/tt06/").content.decode()
    assert 'id="tt-power"' in html and 'data-port="6"' in html and 'data-switch="1"' in html
    assert "has stopped reporting" in html
    assert post(TINYTAPEOUT, "/snmp/toggle", {"port": "6", "switch": 1}).status_code == 200
    assert switch_calls == cycled(TINYTAPEOUT, 1, 6)
