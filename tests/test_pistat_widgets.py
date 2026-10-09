"""The board-page ping button names the Pi by its hostname (pi-sw2-p34, or
pi34 at a flat site), the same name as its status log group. The address
derives from it -- 10.21.<switch>.<port> on VLAN-per-port sites, the legacy
flat 10.21.0.<100+port> -- and only a Pi the board pages offer is pinged:
the endpoint is open to anyone."""

import asyncio

import pytest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.test import Client

from tests.fleet_pis import verified_pi


class FakeProc:
    def __init__(self, *args, **kwargs):
        self.stdout = self  # readline() provider
        self.lines = [b"64 bytes from 10.21.2.34: icmp_seq=1\n"]

    def readline(self):
        return self.lines.pop(0) if self.lines else b""

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


def ping(name):
    return Client().post(f"/pistat/ping/{name}")


@pytest.mark.django_db
def test_ping_uses_vlan_per_port_address(ping_argv):
    verified_pi("pi-sw2-p34")
    verified_pi("pi-sw1-p34")
    ping("pi-sw2-p34")
    ping("pi-sw1-p34")
    assert [argv[-1] for argv in ping_argv] == ["10.21.2.34", "10.21.1.34"]


@pytest.mark.django_db
def test_ping_a_flat_site_pi_uses_the_legacy_address(ping_argv):
    verified_pi("pi34")
    ping("pi34")
    assert [argv[-1] for argv in ping_argv] == ["10.21.0.134"]


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["pi-sw3-p34", "pi-sw2-p99", "pi34", "pi-sw2-p034", "gateway"])
def test_ping_only_reaches_a_pi_the_pages_offer(ping_argv, name):
    verified_pi("pi-sw2-p34")
    assert ping(name).status_code == 404
    assert ping_argv == []


# transaction=True: the view runs on a sync_to_async worker thread whose own
# DB connection must see the committed rows (as in test_fleet_consumer)
@pytest.mark.django_db(transaction=True)
def test_ping_output_goes_to_the_pis_status_log_group(ping_argv):
    verified_pi("pi-sw2-p34")

    async def listen_and_ping():
        layer = get_channel_layer()
        channel = await layer.new_channel()
        await layer.group_add("pistat_pi-sw2-p34", channel)
        response = await sync_to_async(ping)("pi-sw2-p34")
        return response, await asyncio.wait_for(layer.receive(channel), timeout=1)

    response, message = asyncio.run(listen_and_ping())
    assert response.status_code == 200
    assert message["message"] == "piview: 64 bytes from 10.21.2.34: icmp_seq=1"
