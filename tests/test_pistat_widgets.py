"""The board-page ping button must target the Pi's real address on
VLAN-per-port sites (10.21.<switch>.<port>), falling back to the legacy
flat scheme only when no Pi row says otherwise."""

import pytest
from django.test import Client
from pibfpgas.models import Pi


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


@pytest.mark.django_db
def test_ping_uses_vlan_per_port_address(ping_argv):
    Pi.objects.create(port=34, switch=2, location="welland")
    Client().post("/pistat/ping/pi34")
    assert ping_argv[0][-1] == "10.21.2.34"


@pytest.mark.django_db
def test_ping_legacy_row_and_unknown_pi_fall_back(ping_argv):
    Pi.objects.create(port=34, switch=None, location="ps1")
    Client().post("/pistat/ping/pi34")
    Client().post("/pistat/ping/pi7")   # no row at all
    assert ping_argv[0][-1] == "10.21.0.134"
    assert ping_argv[1][-1] == "10.21.0.107"
