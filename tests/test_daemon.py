import pytest
import requests
from ttsite.models import Board

from ttsite import daemon


class FakeResp:
    def __init__(self, status=200, body=None, bad_json=False):
        self.status_code = status
        self._body = body
        self._bad = bad_json
        self.text = "<not json>" if bad_json else ""

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
@pytest.mark.parametrize("body", [["a", "list"], "a string", 42, None])
def test_health_non_dict_json(monkeypatch, body):
    b = Board.objects.create(slug="tt06", port=6, kind="asic", title="t")
    monkeypatch.setattr(daemon.requests, "get", lambda url, timeout: FakeResp(200, body))
    assert daemon.health(b) == {"reachable": False, "error": "unexpected JSON shape"}


@pytest.mark.django_db
def test_health_unwired_board():
    b = Board.objects.create(slug="k", port=None, kind="kianv", title="t")
    assert daemon.health(b)["reachable"] is False


@pytest.mark.django_db
def test_designs_passes_through_status_and_body(monkeypatch):
    b = Board.objects.create(slug="fpga-1", switch=2, port=33, kind="fpga", title="f")
    calls = {}

    def fake_get(url, timeout, **kw):
        calls["url"], calls["timeout"] = url, timeout
        return FakeResp(200, {"enabled": None, "designs": []})

    monkeypatch.setattr(daemon.requests, "get", fake_get)
    assert daemon.designs(b) == (200, {"enabled": None, "designs": []})
    assert calls == {"url": "http://10.21.2.33:8765/designs", "timeout": (3.05, 30.0)}


@pytest.mark.django_db
def test_enable_posts_body_and_returns_daemon_error_status(monkeypatch):
    b = Board.objects.create(slug="fpga-1", switch=2, port=33, kind="fpga", title="f")
    calls = {}

    def fake_post(url, data=None, headers=None, files=None, timeout=None):
        calls.update(url=url, data=data, headers=headers, timeout=timeout)
        return FakeResp(409, {"error": "another task is running", "detail": ""})

    monkeypatch.setattr(daemon.requests, "post", fake_post)
    assert daemon.enable(b, "tt_um_x", b'{"clock_hz": 10}') == (409, {"error": "another task is running", "detail": ""})
    assert calls["url"] == "http://10.21.2.33:8765/designs/tt_um_x/enable"
    assert calls["data"] == b'{"clock_hz": 10}' and calls["headers"] == {"Content-Type": "application/json"}
    assert calls["timeout"] == (3.05, 30.0)


@pytest.mark.django_db
def test_upload_sends_multipart(monkeypatch):
    import io
    b = Board.objects.create(slug="fpga-1", switch=2, port=33, kind="fpga", title="f")
    calls = {}

    def fake_post(url, data=None, headers=None, files=None, timeout=None):
        calls.update(url=url, data=data, files=files, timeout=timeout)
        return FakeResp(201, {"name": "my", "size": 4, "evicted": []})

    monkeypatch.setattr(daemon.requests, "post", fake_post)
    status, body = daemon.upload(b, "my", io.BytesIO(b"\x7e\xaa\x99\x7e"), "my.bin")
    assert (status, body["name"]) == (201, "my")
    assert calls["url"] == "http://10.21.2.33:8765/bitstream" and calls["data"] == {"name": "my"}
    assert calls["files"]["file"][0] == "my.bin" and calls["timeout"] == (3.05, 45.0)


@pytest.mark.django_db
def test_daemon_unreachable_and_bad_json(monkeypatch):
    b = Board.objects.create(slug="fpga-1", switch=2, port=33, kind="fpga", title="f")

    def boom(*a, **k):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(daemon.requests, "get", boom)
    assert daemon.designs(b) == (502, {"error": "Pi unreachable", "detail": "no route"})
    monkeypatch.setattr(daemon.requests, "get", lambda *a, **k: FakeResp(200, bad_json=True))
    status, body = daemon.designs(b)
    assert status == 502 and body["error"] == "bad response from daemon"


@pytest.mark.django_db
def test_enable_quotes_name_in_url(monkeypatch):
    b = Board.objects.create(slug="fpga-1", switch=2, port=33, kind="fpga", title="f")
    calls = {}

    def fake_post(url, data=None, headers=None, files=None, timeout=None):
        calls["url"] = url
        return FakeResp(200, {})

    monkeypatch.setattr(daemon.requests, "post", fake_post)
    daemon.enable(b, "a b", b'{"clock_hz": 10}')
    assert calls["url"].endswith("/designs/a%20b/enable")


@pytest.mark.django_db
def test_enable_sends_empty_body_as_json_object(monkeypatch):
    b = Board.objects.create(slug="fpga-1", switch=2, port=33, kind="fpga", title="f")
    calls = {}

    def fake_post(url, data=None, headers=None, files=None, timeout=None):
        calls["data"] = data
        return FakeResp(200, {})

    monkeypatch.setattr(daemon.requests, "post", fake_post)
    daemon.enable(b, "tt_um_x", b"")
    assert calls["data"] == b"{}"
    daemon.enable(b, "tt_um_x", b'{"clock_hz": 10}')
    assert calls["data"] == b'{"clock_hz": 10}'
