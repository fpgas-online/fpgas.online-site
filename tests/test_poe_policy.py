"""The PoE endpoints (/snmp/status, /snmp/toggle; the snmp_switch app from
the fpgas-online-poe package) act only on a port with a board the site
offers (pibfpgas/poe.py), once per port per interval.

Run against the installed fpgas-online-poe package with this project's own
settings and URL routing, on both hosts, and with a switch client that
records what it is asked instead of speaking to a switch. If the installed
package did not enforce the policy, the refusals here would not happen:
that is what keeps a site that names a policy from being paired, unnoticed,
with a package that never asks it.

The port numbers are fixture data: nothing here says what is on which port
of a real switch.
"""

import json
import textwrap
import types

import pytest
from django.test import Client
from django.utils.module_loading import import_string
from pibfpgas.checks import poe_package_enforces_the_port_policy
from snmp_switch import switches
from snmp_switch.policy import PORT_POLICY_SETTING
from ttsite.models import Board

import pib.settings
from tests.fleet_pis import registered, verified_pi

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
NOT_FOR_BOARDS = [(1, 47), (1, 48), (1, 50), (2, 51), (2, 52)]


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


@pytest.fixture
def tt_boards(db):
    Board.objects.create(slug="tt06", switch=1, port=6, kind="asic", shuttle="tt06", title="Tiny Tapeout 6")
    Board.objects.create(slug="tt03", switch=1, port=3, kind="asic", title="Tiny Tapeout 3", enabled=False)
    Board.objects.create(slug="kianv-1", port=None, kind="kianv", title="KianV uLinux SoC")
    Board.objects.create(slug="tt07", switch=2, port=7, kind="asic", shuttle="tt07", title="Tiny Tapeout 7")


CYCLE_SW2_P46 = [("192.0.2.2", "set", 46, False), ("192.0.2.2", "get"), ("192.0.2.2", "set", 46, True), ("192.0.2.2", "get")]


# --- the project is wired to the package's policy hook ----------------------


def test_the_settings_name_the_site_policy_under_the_name_the_package_reads():
    """PORT_POLICY_SETTING is imported from the installed fpgas-online-poe: a
    package from before it enforced a policy fails this file at import."""
    assert PORT_POLICY_SETTING == "SNMP_SWITCH_PORT_POLICY"
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
    assert [e.id for e in errors] == ["pibfpgas.E001"] and "any switch port" in errors[0].msg


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


# --- the welland site: a Pi the /fpgas/ pages list --------------------------


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
@pytest.mark.parametrize("switch, port", NOT_FOR_BOARDS + [(2, 45), (1, 46), (2, 999)])
def test_an_uplink_trunk_empty_or_unknown_port_is_refused(switch_calls, path, switch, port):
    """(1, 46): the listed board's port number on the other switch."""
    verified_pi("pi-sw2-p46")
    r = post(WELLAND, path, {"port": str(port), "switch": switch})
    assert r.status_code == 403
    assert r.json() == {"error": f"switch {switch} port {port} is not a board this site offers; "
                                 "nothing was sent to the switch"}
    assert switch_calls == []


@pytest.mark.parametrize("path", ["/snmp/status", "/snmp/toggle"])
@pytest.mark.parametrize("result", ["fail", "missing"])
def test_a_registered_board_that_is_not_listed_is_refused(switch_calls, path, result):
    """Registered and checking in, but its FPGA check did not pass this boot:
    the site has no page for it, so no Reset button."""
    registered("s1", "pi-sw2-p44", result)
    assert Client(HTTP_HOST=WELLAND).get("/fpgas/pi-sw2-p44.html").status_code == 404
    r = post(WELLAND, path, {"port": "44", "switch": 2})
    assert r.status_code == 403
    assert switch_calls == []


@pytest.mark.parametrize("path", ["/snmp/status", "/snmp/toggle"])
def test_an_offered_pi_with_no_board_shown_here_is_refused(switch_calls, path):
    """Passed its check, but carries only a Tiny Tapeout ASIC board, which
    this site's pages do not list (pibfpgas.pis.listed)."""
    verified_pi("pi-sw1-p12", ("tt", "tt-asic"))
    r = post(WELLAND, path, {"port": "12", "switch": 1})
    assert r.status_code == 403
    assert switch_calls == []


def test_a_board_that_stopped_checking_in_is_refused(switch_calls):
    m = verified_pi("pi-sw2-p46")
    m.online = False
    m.save()
    assert post(WELLAND, "/snmp/toggle", {"port": "46", "switch": 2}).status_code == 403
    assert switch_calls == []


# --- the legacy flat scheme (pi<port>, one switch with no index) ------------


def test_flat_scheme_power_cycles_a_listed_board(flat_calls, settings):
    settings.DOMAIN_NAME = WELLAND
    settings.PI_PW = "cGFzc3dvcmQ="
    verified_pi("pi9")
    # what the flat site's board page sends: the port alone
    assert 'PiStatus("9", null, "pi9")' in Client(HTTP_HOST=WELLAND).get("/fpgas/pi9.html").content.decode()
    r = post(WELLAND, "/snmp/toggle", {"port": "9"})
    assert (r.status_code, r.json()) == (200, {"9": ["off", "on"]})
    assert flat_calls == [("set", "9", "2"), ("set", "9", "1")]


@pytest.mark.parametrize("path", ["/snmp/status", "/snmp/toggle"])
def test_flat_scheme_refuses_a_port_with_no_listed_board(flat_calls, path):
    verified_pi("pi9")
    registered("s2", "pi11", "fail")
    for port in ("10", "11", "48"):
        r = post(WELLAND, path, {"port": port})
        assert r.status_code == 403
        assert f"port {port} is not a board this site offers" in r.json()["error"]
    assert flat_calls == []


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
    assert r.json() == {"error": f"switch 2 port 46 was power-cycled a moment ago; try again in {r['Retry-After']} s. "
                                 "Nothing was sent to the switch"}
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


# --- the Tiny Tapeout site: a board whose page shows the button -------------


def test_tt_board_page_button_power_cycles_its_board(switch_calls, tt_boards):
    html = Client(HTTP_HOST=TINYTAPEOUT).get("/board/tt06/").content.decode()
    assert 'id="tt-power"' in html and 'data-port="6"' in html and 'data-switch="1"' in html
    # what board.js sends from those attributes
    r = post(TINYTAPEOUT, "/snmp/toggle", {"port": "6", "switch": 1})
    assert r.status_code == 200
    assert switch_calls == [("192.0.2.1", "set", 6, False), ("192.0.2.1", "get"),
                            ("192.0.2.1", "set", 6, True), ("192.0.2.1", "get")]
    assert post(TINYTAPEOUT, "/snmp/toggle", {"port": "6", "switch": 1}).status_code == 429


@pytest.mark.parametrize("path", ["/snmp/status", "/snmp/toggle"])
@pytest.mark.parametrize("switch, port, why", [
    (1, 3, "tt03 is disabled: no button on its page"),
    (2, 7, "tt07 is on the second switch: no button on its page"),
    (2, 6, "tt06's port number, on the other switch"),
    (1, 7, "tt07's port number, on the other switch"),
    (1, 5, "no board"),
    (1, 47, "gateway trunk"), (1, 48, "uplink"), (1, 50, "trunk"), (2, 51, "trunk"), (2, 52, "uplink"),
    (2, 46, "a board the welland site lists, not this one"),
])
def test_tt_site_refuses_every_port_without_a_button(switch_calls, tt_boards, path, switch, port, why):
    verified_pi("pi-sw2-p46")
    r = post(TINYTAPEOUT, path, {"port": str(port), "switch": switch})
    assert r.status_code == 403, why
    assert "is not a board this site offers" in r.json()["error"]
    assert switch_calls == []


def test_a_tt_site_board_is_not_thereby_a_welland_site_board(switch_calls, tt_boards):
    """Each site answers for its own pages: tt06 has a button on the Tiny
    Tapeout site, and the welland site lists no Pi on that port."""
    r = post(WELLAND, "/snmp/toggle", {"port": "6", "switch": 1})
    assert r.status_code == 403
    assert switch_calls == []


def test_tt_button_and_policy_use_the_same_answer(tt_boards):
    from pibfpgas.poe import board_port
    request = types.SimpleNamespace(get_host=lambda: TINYTAPEOUT)
    for board in Board.objects.all():
        html = Client(HTTP_HOST=TINYTAPEOUT).get(f"/board/{board.slug}/").content.decode()
        shown = 'id="tt-power"' in html
        assert shown == board.can_power_cycle
        if board.port is not None:
            assert board_port(request, board.switch, board.port) == shown, board.slug
