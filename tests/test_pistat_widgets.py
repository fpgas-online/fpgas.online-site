"""The board-page ping button must target the Pi's real address: the page
posts {"port", "switch"} (as it does to /snmp/), and the address derives
from them -- 10.21.<switch>.<port> on VLAN-per-port sites, the legacy flat
10.21.0.<100+port> when no switch is sent -- and only for a Pi the board
pages offer."""

import json

import pytest
from django.test import Client

from tests.fleet_pis import verified_pi


class FakeProc:
    def __init__(self, *args, **kwargs):
        self.stdout = self  # readline() provider

    def readline(self):
        return b""

    def poll(self):
        return 0


@pytest.fixture
def ping_argv(monkeypatch):
    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append(cmd)
        return FakeProc()

    monkeypatch.setattr("pistat.views.subprocess.Popen", fake_popen)
    return calls


def ping(name, body):
    return Client().post(f"/pistat/ping/{name}", data=json.dumps(body),
                         content_type="application/json")


@pytest.mark.django_db
def test_ping_uses_vlan_per_port_address(ping_argv):
    verified_pi("pi-sw2-p34")
    verified_pi("pi-sw1-p34")
    ping("pi34", {"port": "34", "switch": 2})
    ping("pi34", {"port": "34", "switch": 1})
    assert [argv[-1] for argv in ping_argv] == ["10.21.2.34", "10.21.1.34"]


@pytest.mark.django_db
def test_ping_with_no_switch_is_the_legacy_flat_address(ping_argv):
    verified_pi("pi34")
    verified_pi("pi7")
    ping("pi34", {"port": "34"})
    ping("pi7", {"port": "7", "switch": None})
    assert [argv[-1] for argv in ping_argv] == ["10.21.0.134", "10.21.0.107"]


@pytest.mark.django_db
@pytest.mark.parametrize("name, body", [
    ("pi34", {"switch": 3}),  # a switch with no such Pi on it
    ("pi99", {"switch": 2}),
    ("pi34", {"switch": 300}),
    ("pi34", {}),  # pi34 on a flat site: not registered here
])
def test_ping_only_reaches_a_pi_the_pages_offer(ping_argv, name, body):
    """The endpoint is open to anyone: it must not probe arbitrary addresses."""
    verified_pi("pi-sw2-p34")
    assert ping(name, body).status_code == 404
    assert ping_argv == []


@pytest.mark.django_db
@pytest.mark.parametrize("body", [{"switch": "2; rm -rf /"}, {"switch": True}, ["switch"]])
def test_ping_refuses_a_switch_that_is_not_a_number(ping_argv, body):
    verified_pi("pi-sw2-p34")
    assert ping("pi34", body).status_code == 400
    assert ping_argv == []
