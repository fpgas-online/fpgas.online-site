"""ttsite.gateway client against a stub gateway (httpx.MockTransport)."""

import json

import httpx
import pytest
from ttsite.gateway import Gateway, GatewayBoard, GatewayError

from ttsite import gateway

SITE = {
    "site": {"id": "welland", "name": "Welland", "location": "Welland, South Australia", "timezone": "Australia/Adelaide"},
    "public_base": "https://gw.welland.example",
    "version": "0.1.0",
}
BOARD = {
    "site_id": "welland",
    "slug": "tt06",
    "kind": "asic",
    "switch": 1,
    "port": 6,
    "hostname": "pi-sw1-p6",
    "ip_hidden": True,
    "title": "Tiny Tapeout 6",
    "blurb": "b",
    "description": "d",
    "links": [],
    "enabled": True,
    "live": True,
    "stream_url": "https://gw.welland.example/live/pi-sw1-p6.m3u8",
    "serial_ws_url": "wss://gw.welland.example/ws/board/tt06/serial",
    "api_base": "https://gw.welland.example/api/board/tt06",
    "ssh": {"host": "gw.welland.example", "port": 10622},
}


class Stub:
    """Programmable stub gateway; records every request it sees."""

    def __init__(self):
        self.requests = []
        self.routes = {
            ("GET", "/api/site"): httpx.Response(200, json=SITE),
            ("GET", "/api/boards"): httpx.Response(200, json={"boards": [BOARD]}),
        }
        self.transport = httpx.MockTransport(self.handler)

    def handler(self, request):
        self.requests.append(request)
        route = self.routes.get((request.method, request.url.path))
        if route is None:
            return httpx.Response(404, json={"error": "not found", "detail": request.url.path})
        if isinstance(route, Exception):
            raise route
        return route


@pytest.fixture
def stub():
    return Stub()


@pytest.fixture
def gw(stub):
    g = Gateway("welland", "https://gw.welland.example", "tok-welland", transport=stub.transport)
    yield g
    g.close()


def test_site_and_boards_parse(gw):
    assert gw.site()["public_base"] == "https://gw.welland.example"
    assert gw.site_info()["location"] == "Welland, South Australia"
    assert gw.site_name() == "Welland"
    assert gw.boards()[0]["slug"] == "tt06"


def test_site_name_falls_back_to_id(gw, stub):
    stub.routes[("GET", "/api/site")] = httpx.Response(200, json={"site": {}, "public_base": "x"})
    assert gw.site_name() == "welland"


def test_site_and_boards_and_status_are_cached(gw, stub):
    stub.routes[("GET", "/api/board/tt06/status")] = httpx.Response(200, json={"reachable": True})
    for _ in range(3):
        gw.site()
        gw.boards()
        gw.board_status("tt06")
    assert len(stub.requests) == 3


def test_failure_is_cached_too(gw, stub):
    stub.routes[("GET", "/api/site")] = httpx.ConnectError("refused")
    for _ in range(2):
        with pytest.raises(GatewayError) as e:
            gw.site()
        assert e.value.kind == GatewayError.UNREACHABLE
    assert len(stub.requests) == 1


def test_unreachable_maps_to_typed_error(gw, stub):
    stub.routes[("GET", "/api/boards")] = httpx.ConnectTimeout("slow")
    with pytest.raises(GatewayError) as e:
        gw.boards()
    assert e.value.kind == GatewayError.UNREACHABLE
    assert e.value.gateway_id == "welland"


def test_non_json_maps_to_bad_response(gw, stub):
    stub.routes[("GET", "/api/boards")] = httpx.Response(200, text="<html>oops</html>")
    with pytest.raises(GatewayError) as e:
        gw.boards()
    assert e.value.kind == GatewayError.BAD_RESPONSE


def test_http_error_on_get_maps_to_typed_error(gw, stub):
    stub.routes[("GET", "/api/boards")] = httpx.Response(500, json={"error": "boom"})
    with pytest.raises(GatewayError) as e:
        gw.boards()
    assert e.value.kind == GatewayError.HTTP and e.value.status == 500
    assert "boom" in str(e.value)


def test_boards_shape_must_be_a_list(gw, stub):
    stub.routes[("GET", "/api/boards")] = httpx.Response(200, json={"boards": "nope"})
    with pytest.raises(GatewayError) as e:
        gw.boards()
    assert e.value.kind == GatewayError.BAD_RESPONSE


def test_board_status_passes_status_through(gw, stub):
    stub.routes[("GET", "/api/board/tt06/status")] = httpx.Response(503, json={"error": "not-live"})
    assert gw.board_status("tt06") == (503, {"error": "not-live"})


def test_designs_enable_bitstream_power_pass_through(gw, stub):
    stub.routes[("GET", "/api/board/tt06/designs")] = httpx.Response(200, json={"designs": [], "enabled": None})
    stub.routes[("POST", "/api/board/tt06/designs/tt_um_a/enable")] = httpx.Response(200, json={"ok": True})
    stub.routes[("POST", "/api/board/tt06/bitstream")] = httpx.Response(201, json={"name": "x", "size": 1})
    stub.routes[("POST", "/api/board/tt06/power")] = httpx.Response(200, json={"admin": "on"})
    stub.routes[("GET", "/api/board/tt06/power")] = httpx.Response(200, json={"admin": "on"})
    assert gw.designs("tt06") == (200, {"designs": [], "enabled": None})
    assert gw.enable("tt06", "tt_um_a", b'{"clock_hz": 5}') == (200, {"ok": True})
    assert gw.bitstream("tt06", b"\x00", "x") == (201, {"name": "x", "size": 1})
    assert gw.power("tt06", "cycle") == (200, {"admin": "on"})
    assert gw.power("tt06") == (200, {"admin": "on"})


def test_requests_carry_the_bearer_token(gw, stub):
    stub.routes[("POST", "/api/board/tt06/power")] = httpx.Response(200, json={"admin": "on"})
    gw.power("tt06", "cycle")
    req = stub.requests[-1]
    assert req.headers["Authorization"] == "Bearer tok-welland"
    assert json.loads(req.content) == {"action": "cycle"}


def test_enable_sends_forwarded_body_and_defaults_to_empty_object(gw, stub):
    stub.routes[("POST", "/api/board/tt06/designs/n/enable")] = httpx.Response(200, json={"ok": True})
    gw.enable("tt06", "n")
    assert stub.requests[-1].content == b"{}"
    assert stub.requests[-1].headers["Content-Type"] == "application/json"
    gw.enable("tt06", "n", b'{"clock_hz": 1}')
    assert stub.requests[-1].content == b'{"clock_hz": 1}'


def test_slug_and_name_are_url_quoted(gw, stub):
    status, body = gw.designs("a/b")
    # the stub's 404 pass-through proves the slash was quoted into one path segment
    assert status == 404
    assert stub.requests[-1].url.raw_path == b"/api/board/a%2Fb/designs"


def test_gateway_board_adapter_defaults():
    g = Gateway("welland", "https://gw.welland.example")
    try:
        b = GatewayBoard(BOARD, g, "Welland")
        assert (b.site_id, b.site_name, b.slug) == ("welland", "Welland", "tt06")
        assert b.live and b.get_kind_display() == "TT ASIC"
        assert b.page_url == "/board/welland/tt06/"
        assert b.stream_url == BOARD["stream_url"]
        assert b.serial_ws_url == BOARD["serial_ws_url"]
        assert b.api_base == BOARD["api_base"]
        # fields outside the gateway contract fall back quietly
        assert b.shuttle == "" and b.pcb == "" and b.pmods == []
        soon = GatewayBoard({"slug": "tt03", "kind": "asic", "port": None, "enabled": True}, g, "Welland")
        assert not soon.live and soon.title == "tt03"
    finally:
        g.close()


def test_gateways_from_settings_empty_and_configured(settings):
    settings.FPGAS_GATEWAYS = []
    assert gateway.gateways_from_settings() == []
    settings.FPGAS_GATEWAYS = [
        {"id": "welland", "url": "https://gw.welland.example/", "token": "t1"},
        {"id": "ps1", "url": "https://gw.ps1.example", "token": "t2"},
    ]
    try:
        gws = gateway.gateways_from_settings()
        assert [g.id for g in gws] == ["welland", "ps1"]
        assert gws[0].url == "https://gw.welland.example"  # trailing slash trimmed
        assert gws[0].token == "t1"
        # memoised: same instances (and thus caches) on the next call
        assert gateway.gateways_from_settings() == gws
    finally:
        gateway.reset()


def test_gateways_from_settings_rejects_bad_entries(settings):
    from django.core.exceptions import ImproperlyConfigured

    settings.FPGAS_GATEWAYS = [{"url": "https://x"}]
    with pytest.raises(ImproperlyConfigured):
        gateway.gateways_from_settings()
