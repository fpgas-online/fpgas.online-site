import json
import types

import pytest
import requests

from ttsite import daemon


def at(ip):
    """A board's page as the client needs it: the address of the Pi that carries the board now."""
    return types.SimpleNamespace(ip=ip)


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


def test_health_ok(monkeypatch):
    b = at("10.21.1.6")
    calls = {}

    def fake_get(url, timeout):
        calls["url"], calls["timeout"] = url, timeout
        return FakeResp(200, {"board": {"present": True, "device": "/dev/ttboard", "vid_pid": "2e8a:0005"}, "clients": 1})

    monkeypatch.setattr(daemon.SESSION, "get", fake_get)
    h = daemon.health(b)
    assert calls["url"] == "http://10.21.1.6:8765/health" and calls["timeout"] == 3.0
    assert h["reachable"] is True and h["board"]["present"] is True


@pytest.mark.parametrize("resp", [FakeResp(500, {}), FakeResp(200, bad_json=True)])
def test_health_bad_response(monkeypatch, resp):
    b = at("10.21.1.6")
    monkeypatch.setattr(daemon.SESSION, "get", lambda url, timeout: resp)
    h = daemon.health(b)
    assert h["reachable"] is False and "error" in h


def test_health_connection_error(monkeypatch):
    b = at("10.21.1.6")

    def boom(url, timeout):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(daemon.SESSION, "get", boom)
    h = daemon.health(b)
    assert h == {"reachable": False, "error": "refused"}


@pytest.mark.parametrize("body", [["a", "list"], "a string", 42, None])
def test_health_non_dict_json(monkeypatch, body):
    b = at("10.21.1.6")
    monkeypatch.setattr(daemon.SESSION, "get", lambda url, timeout: FakeResp(200, body))
    assert daemon.health(b) == {"reachable": False, "error": "unexpected JSON shape"}


def test_health_of_a_board_no_pi_reports():
    b = at(None)
    assert daemon.health(b)["reachable"] is False


def test_designs_passes_through_status_and_body(monkeypatch):
    b = at("10.21.2.33")
    calls = {}

    def fake_get(url, timeout, **kw):
        calls["url"], calls["timeout"] = url, timeout
        return FakeResp(200, {"enabled": None, "designs": []})

    monkeypatch.setattr(daemon.SESSION, "get", fake_get)
    assert daemon.designs(b) == (200, {"enabled": None, "designs": []})
    assert calls == {"url": "http://10.21.2.33:8765/designs", "timeout": (3.05, 30.0)}


def test_enable_posts_body_and_returns_daemon_error_status(monkeypatch):
    b = at("10.21.2.33")
    calls = {}

    def fake_post(url, data=None, headers=None, files=None, timeout=None):
        calls.update(url=url, data=data, headers=headers, timeout=timeout)
        return FakeResp(409, {"error": "another task is running", "detail": ""})

    monkeypatch.setattr(daemon.SESSION, "post", fake_post)
    assert daemon.enable(b, "tt_um_x", b'{"clock_hz": 10}') == (409, {"error": "another task is running", "detail": ""})
    assert calls["url"] == "http://10.21.2.33:8765/designs/tt_um_x/enable"
    assert calls["data"] == b'{"clock_hz": 10}' and calls["headers"] == {"Content-Type": "application/json"}
    assert calls["timeout"] == (3.05, 30.0)


def test_upload_sends_multipart(monkeypatch):
    import io
    b = at("10.21.2.33")
    calls = {}

    def fake_post(url, data=None, headers=None, files=None, timeout=None):
        calls.update(url=url, data=data, files=files, timeout=timeout)
        return FakeResp(201, {"name": "my", "size": 4, "evicted": []})

    monkeypatch.setattr(daemon.SESSION, "post", fake_post)
    status, body = daemon.upload(b, "my", io.BytesIO(b"\x7e\xaa\x99\x7e"), "my.bin")
    assert (status, body["name"]) == (201, "my")
    assert calls["url"] == "http://10.21.2.33:8765/bitstream" and calls["data"] == {"name": "my"}
    assert calls["files"]["file"][0] == "my.bin" and calls["timeout"] == (3.05, 45.0)


def test_daemon_unreachable_and_bad_json(monkeypatch):
    b = at("10.21.2.33")

    def boom(*a, **k):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(daemon.SESSION, "get", boom)
    assert daemon.designs(b) == (502, {"error": "Pi unreachable", "detail": "no route"})
    monkeypatch.setattr(daemon.SESSION, "get", lambda *a, **k: FakeResp(200, bad_json=True))
    status, body = daemon.designs(b)
    assert status == 502 and body["error"] == "bad response from daemon"


def test_enable_quotes_name_in_url(monkeypatch):
    b = at("10.21.2.33")
    calls = {}

    def fake_post(url, data=None, headers=None, files=None, timeout=None):
        calls["url"] = url
        return FakeResp(200, {})

    monkeypatch.setattr(daemon.SESSION, "post", fake_post)
    daemon.enable(b, "a b", b'{"clock_hz": 10}')
    assert calls["url"].endswith("/designs/a%20b/enable")


def test_enable_sends_empty_body_as_json_object(monkeypatch):
    b = at("10.21.2.33")
    calls = {}

    def fake_post(url, data=None, headers=None, files=None, timeout=None):
        calls["data"] = data
        return FakeResp(200, {})

    monkeypatch.setattr(daemon.SESSION, "post", fake_post)
    daemon.enable(b, "tt_um_x", b"")
    assert calls["data"] == b"{}"
    daemon.enable(b, "tt_um_x", b'{"clock_hz": 10}')
    assert calls["data"] == b'{"clock_hz": 10}'


# --- the VGA library calls (deadline-bound)


class Clock:
    def __init__(self, t=500.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(daemon, "now", c)
    return c


def test_library_state_passes_through(monkeypatch, clock):
    b = at("10.21.2.33")
    calls = {}
    state = {"max_bytes": 16777216, "used_bytes": 0, "entries": []}

    def fake_get(url, timeout=None, **kw):
        calls.update(url=url, timeout=timeout)
        return FakeResp(200, state)

    monkeypatch.setattr(daemon.SESSION, "get", fake_get)
    assert daemon.library_state(b, deadline=clock() + 50) == (200, state)
    assert calls == {"url": "http://10.21.2.33:8765/library", "timeout": (3.05, 30.0)}


def test_library_state_old_daemon_is_unsupported(monkeypatch, clock):
    b = at("10.21.2.33")
    monkeypatch.setattr(daemon.SESSION, "get", lambda *a, **k: FakeResp(404, bad_json=True))
    with pytest.raises(daemon.Unsupported):
        daemon.library_state(b, deadline=clock() + 50)
    # a new daemon on a board that is not an FPGA answers a JSON 404: passed through, not "too old"
    body = {"error": "not an FPGA board", "detail": ""}
    monkeypatch.setattr(daemon.SESSION, "get", lambda *a, **k: FakeResp(404, body))
    assert daemon.library_state(b, deadline=clock() + 50) == (404, body)


def test_library_state_unreachable_is_502(monkeypatch, clock):
    def boom(*a, **k):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(daemon.SESSION, "get", boom)
    assert daemon.library_state(at("10.21.2.33"), deadline=clock() + 50) == (
        502, {"error": "Pi unreachable", "detail": "no route"})  # fmt: skip


def test_library_push_sends_meta_and_file_parts(monkeypatch, clock):
    b = at("10.21.2.33")
    data = b"\xff\x00\x00\xff\x7e\xaa\x99\x7e" + bytes(4088)
    sha = "ab" * 32
    calls = {}
    answer = {"id": "tt05_x", "bin_bytes": 4096, "bin_sha256": sha, "evicted": [], "hides_upload": False}

    def fake_post(url, data=None, headers=None, files=None, timeout=None):
        calls.update(url=url, data=data, files=files, timeout=timeout)
        return FakeResp(201, answer)

    monkeypatch.setattr(daemon.SESSION, "post", fake_post)
    assert daemon.library_push(b, "tt05_x", data, sha, deadline=clock() + 100) == (201, answer)
    assert calls["url"] == "http://10.21.2.33:8765/library/tt05_x" and calls["data"] is None
    assert calls["timeout"] == (3.05, 45.0)
    assert set(calls["files"]) == {"meta", "file"}
    meta = calls["files"]["meta"]
    assert json.loads(meta[1]) == {"id": "tt05_x", "bin_sha256": sha, "bin_bytes": 4096}
    assert meta[2] == "application/json"
    assert calls["files"]["file"][1] == data and calls["files"]["file"][2] == "application/octet-stream"
    # on the wire: two form-data parts named meta and file
    wire = requests.Request("POST", calls["url"], files=calls["files"]).prepare().body
    assert b'name="meta"' in wire and b'name="file"' in wire and data in wire


def test_daemon_calls_cap_read_timeout_at_deadline(monkeypatch, clock):
    b = at("10.21.2.33")
    seen = []

    def fake(url, data=None, headers=None, files=None, timeout=None):
        seen.append(timeout)
        return FakeResp(200, {"entries": []})

    monkeypatch.setattr(daemon.SESSION, "get", fake)
    monkeypatch.setattr(daemon.SESSION, "post", fake)
    daemon.library_state(b, deadline=clock() + 12)
    daemon.library_push(b, "tt05_x", b"x", "ab" * 32, deadline=clock() + 12)
    daemon.enable(b, "tt05_x", b"{}", deadline=clock() + 12)
    daemon.enable(b, "tt05_x", b"{}", deadline=clock() + 100)
    daemon.enable(b, "tt05_x", b"{}")
    assert seen == [(3.05, 12), (3.05, 12), (3.05, 12), (3.05, 30.0), (3.05, 30.0)]

    # at or past the deadline nothing is sent
    seen.clear()
    for status, body in [
        daemon.library_state(b, deadline=clock()),
        daemon.library_push(b, "tt05_x", b"x", "ab" * 32, deadline=clock() - 1),
        daemon.enable(b, "tt05_x", b"{}", deadline=clock()),
    ]:
        assert status == 504 and body["error"] == "the load ran out of time; try again"
    assert seen == []


def test_daemon_session_ignores_proxy_environment():
    assert isinstance(daemon.SESSION, requests.Session) and daemon.SESSION.trust_env is False


def test_out_of_time_body_is_fresh_each_call(clock):
    b = at("10.21.2.33")
    _, first = daemon.enable(b, "tt05_x", b"{}", deadline=clock())
    first["pushed"] = True
    _, second = daemon.library_state(b, deadline=clock())
    assert second == {"error": "the load ran out of time; try again", "detail": ""}


def test_daemon_calls_send_connection_close(monkeypatch):
    """N4: no daemon connection is kept for reuse."""
    import socket
    import threading

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    monkeypatch.setattr(daemon, "DAEMON_PORT", srv.getsockname()[1])
    seen = []

    def serve():
        conn, _ = srv.accept()
        try:
            request = b""
            while b"\r\n\r\n" not in request:
                request += conn.recv(65536)
            seen.append(request)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Type: application/json\r\n\r\n{}")
        finally:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        assert daemon.designs(at("127.0.0.1")) == (200, {})
    finally:
        thread.join(2)
        srv.close()
    assert "connection: close" in seen[0].decode("latin-1").lower().split("\r\n")
