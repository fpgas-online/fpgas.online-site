import pytest
import requests
from ttsite.models import Board

from ttsite import daemon


class FakeResp:
    def __init__(self, status=200, body=None, bad_json=False):
        self.status_code = status
        self._body = body
        self._bad = bad_json

    def json(self):
        if self._bad:
            raise ValueError("bad json")
        return self._body


@pytest.mark.django_db
def test_health_ok(monkeypatch):
    b = Board.objects.create(slug="tt06", port=6, kind="asic", title="t")
    calls = {}

    def fake_get(url, timeout):
        calls["url"], calls["timeout"] = url, timeout
        return FakeResp(200, {"board": {"present": True, "device": "/dev/ttboard", "vid_pid": "2e8a:0005"}, "clients": 1})

    monkeypatch.setattr(daemon.requests, "get", fake_get)
    h = daemon.health(b)
    assert calls["url"] == "http://10.21.1.6:8765/health" and calls["timeout"] == 3.0
    assert h["reachable"] is True and h["board"]["present"] is True


@pytest.mark.django_db
@pytest.mark.parametrize("resp", [FakeResp(500, {}), FakeResp(200, bad_json=True)])
def test_health_bad_response(monkeypatch, resp):
    b = Board.objects.create(slug="tt06", port=6, kind="asic", title="t")
    monkeypatch.setattr(daemon.requests, "get", lambda url, timeout: resp)
    h = daemon.health(b)
    assert h["reachable"] is False and "error" in h


@pytest.mark.django_db
def test_health_connection_error(monkeypatch):
    b = Board.objects.create(slug="tt06", port=6, kind="asic", title="t")

    def boom(url, timeout):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(daemon.requests, "get", boom)
    h = daemon.health(b)
    assert h == {"reachable": False, "error": "refused"}


@pytest.mark.django_db
def test_health_unwired_board():
    b = Board.objects.create(slug="k", port=None, kind="kianv", title="t")
    assert daemon.health(b)["reachable"] is False
