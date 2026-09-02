"""ttsite views in gateway consumer mode (0, 1 and 2 gateways) against stub gateways."""

import re

import httpx
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from ttsite.models import Board

from ttsite import gateway

WELLAND = "gw.welland.example"
PS1 = "gw.ps1.example"


def make_board(host, slug, kind="asic", switch=1, port=6, live=True, title=None):
    base = f"https://{host}"
    hostname = f"pi-sw{switch}-p{port}" if port is not None else ""
    return {
        "site_id": host.split(".")[1],
        "slug": slug,
        "kind": kind,
        "switch": switch,
        "port": port,
        "hostname": hostname,
        "ip_hidden": True,
        "title": title or slug.upper(),
        "blurb": f"blurb {slug}",
        "description": f"about {slug}",
        "links": [],
        "enabled": True,
        "live": live,
        "stream_url": f"{base}/live/{hostname}.m3u8",
        "serial_ws_url": f"wss://{host}/ws/board/{slug}/serial",
        "api_base": f"{base}/api/board/{slug}",
        "ssh": {"host": host, "port": int(f"{switch}{port:02d}22")} if port is not None else {},
    }


class StubNet:
    """Stub gateways for several hosts behind one MockTransport; records all requests."""

    def __init__(self, sites):
        self.sites = sites  # host -> {"site": {...}, "boards": [...]}
        self.requests = []
        self.overrides = {}  # (host, method, path) -> httpx.Response | Exception
        self.transport = httpx.MockTransport(self.handler)

    def handler(self, request):
        self.requests.append(request)
        host, method, path = request.url.host, request.method, request.url.path
        override = self.overrides.get((host, method, path))
        if isinstance(override, Exception):
            raise override
        if override is not None:
            return override
        site = self.sites.get(host)
        if site is None:
            raise httpx.ConnectError(f"no such host {host}")
        if method == "GET" and path == "/api/site":
            return httpx.Response(200, json={"site": site["site"], "public_base": f"https://{host}", "version": "0"})
        if method == "GET" and path == "/api/boards":
            return httpx.Response(200, json={"boards": site["boards"]})
        m = re.fullmatch(r"/api/board/([^/]+)/(status|designs|power|bitstream|designs/([^/]+)/enable)", path)
        if m:
            board = next((b for b in site["boards"] if b["slug"] == m.group(1)), None)
            if board is None:
                return httpx.Response(404, json={"error": "no such board", "detail": m.group(1)})
            if not board["live"]:
                return httpx.Response(503, json={"error": "not-live", "detail": ""})
            what = m.group(2)
            if what == "status" and method == "GET":
                return httpx.Response(200, json={"reachable": True, "board": {"present": True}, "clients": 1})
            if what == "designs" and method == "GET":
                return httpx.Response(200, json={"enabled": "tt_um_x", "designs": [{"name": "tt_um_x"}]})
            if what == "power" and method == "POST":
                return httpx.Response(200, json={"admin": "on", "action": "cycle"})
            if what == "bitstream" and method == "POST":
                return httpx.Response(201, json={"name": "up", "size": 1, "evicted": []})
            if what.startswith("designs/") and method == "POST":
                return httpx.Response(200, json={"ok": True, "enabled": m.group(3)})
        return httpx.Response(404, json={"error": "not found", "detail": path})

    def calls(self, host):
        return [r for r in self.requests if r.url.host == host]


@pytest.fixture
def c():
    return Client(HTTP_HOST="tinytapeout.fpgas.online")


@pytest.fixture
def net():
    stub = StubNet(
        {
            WELLAND: {
                "site": {"id": "welland", "name": "Welland", "location": "Welland, South Australia"},
                "boards": [
                    make_board(WELLAND, "tt06", port=6),
                    make_board(WELLAND, "fpga-1", kind="fpga", port=12),
                    make_board(WELLAND, "tt03", port=None, live=False),
                ],
            },
            PS1: {
                "site": {"id": "ps1", "name": "PS1", "location": "Chicago, Illinois"},
                "boards": [make_board(PS1, "tt09", switch=1, port=9)],
            },
        }
    )
    gateway.reset()
    gateway._default_transport = stub.transport
    yield stub
    gateway.reset()
    gateway._default_transport = None


@pytest.fixture
def one_gateway(net, settings):
    settings.FPGAS_GATEWAYS = [{"id": "welland", "url": f"https://{WELLAND}", "token": "tok-welland"}]
    return net


@pytest.fixture
def two_gateways(net, settings):
    settings.FPGAS_GATEWAYS = [
        {"id": "welland", "url": f"https://{WELLAND}", "token": "tok-welland"},
        {"id": "ps1", "url": f"https://{PS1}", "token": "tok-ps1"},
    ]
    return net


# -- zero gateways: behaviour unchanged --


def test_no_gateways_index_uses_local_db_and_welland_fallback(c, db, settings):
    settings.FPGAS_GATEWAYS = []
    Board.objects.create(slug="tt06", port=6, kind="asic", shuttle="tt06", title="Tiny Tapeout 6")
    html = c.get("/").content.decode()
    assert '<a class="tt-btn" href="/board/tt06/">Use this board</a>' in html
    assert "hosted at Welland, South Australia" in html
    assert c.get("/board/tt06/").status_code == 200


def test_no_gateways_board_page_keeps_welland_and_snmp_power(c, db, settings):
    settings.FPGAS_GATEWAYS = []
    Board.objects.create(slug="tt06", port=6, kind="asic", shuttle="tt06", title="Tiny Tapeout 6")
    html = c.get("/board/tt06/").content.decode()
    assert "Welland, switch 1 port 6 (pi-sw1-p6)" in html
    assert 'data-ws-path="/ws/board/tt06/serial"' in html
    assert "data-power-url" not in html and "data-gw-api-base" not in html
    assert 'data-api-base="/api/board/tt06"' in html


def test_no_gateways_power_proxy_route_is_a_404(c, db, settings):
    settings.FPGAS_GATEWAYS = []
    assert c.post("/api/board/tt06/power").status_code == 404


# -- one gateway --


def test_index_one_gateway_lists_boards_from_the_gateway(c, one_gateway):
    html = c.get("/").content.decode()
    assert "TT06" in html and "FPGA-1" in html and "TT03" in html
    assert '<a class="tt-btn" href="/board/welland/tt06/">Use this board</a>' in html
    assert '<a href="/board/welland/tt03/">Read about it' in html
    assert '<span class="tt-site">Welland</span>' in html
    # absolute stream URL from the API, verbatim
    assert f"https://{WELLAND}/live/pi-sw1-p6.m3u8" in html
    assert "hosted at Welland, South Australia" in html
    assert "10.21." not in html


def test_legacy_board_route_redirects_to_the_owning_site(c, one_gateway):
    r = c.get("/board/tt06/")
    assert r.status_code == 302 and r.headers["Location"] == "/board/welland/tt06/"
    r = c.get("/board/tt06/status.json")
    assert r.status_code == 302 and r.headers["Location"] == "/board/welland/tt06/status.json"
    assert c.get("/board/nope/").status_code == 404


def test_board_page_uses_gateway_urls_verbatim(c, one_gateway, settings):
    settings.TTSITE_COMMANDER_VERSION = "0.1.0"
    html = c.get("/board/welland/tt06/").content.decode()
    assert f'<source src="https://{WELLAND}/live/pi-sw1-p6.m3u8"' in html
    assert f'data-ws-url="wss://{WELLAND}/ws/board/tt06/serial"' in html
    assert f'data-gw-api-base="https://{WELLAND}/api/board/tt06"' in html
    # the page JS drives the site's own proxy endpoints
    assert 'data-api-base="/api/board/welland/tt06"' in html
    assert 'data-status-url="/board/welland/tt06/status.json"' in html
    assert 'data-power-url="/api/board/welland/tt06/power"' in html
    assert "Welland, switch 1 port 6 (pi-sw1-p6)" in html
    assert "Power-cycle board" in html
    assert "10.21." not in html


def test_board_page_unknown_site_or_slug_is_404(c, one_gateway):
    assert c.get("/board/ps1/tt09/").status_code == 404  # ps1 not configured here
    assert c.get("/board/welland/nope/").status_code == 404


def test_status_json_proxies_the_gateway(c, one_gateway):
    r = c.get("/board/welland/tt06/status.json")
    assert r.status_code == 200
    assert r.json()["reachable"] is True and r.json()["board"] == {"present": True}
    assert c.get("/board/welland/nope/status.json").status_code == 404
    # non-live board: the gateway's 503 not-live becomes a calm 'coming soon'-style answer
    r = c.get("/board/welland/tt03/status.json")
    assert r.status_code == 200 and r.json() == {"reachable": False, "error": "not-live"}


def test_status_json_when_the_gateway_is_down(c, one_gateway):
    one_gateway.overrides[(WELLAND, "GET", "/api/board/tt06/status")] = httpx.ConnectError("refused")
    r = c.get("/board/welland/tt06/status.json")
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is False and "welland" in body["error"]


def test_designs_proxy_forwards_and_carries_the_token(c, one_gateway):
    r = c.get("/api/board/welland/fpga-1/designs")
    assert r.status_code == 200 and r.json()["enabled"] == "tt_um_x"
    req = one_gateway.calls(WELLAND)[-1]
    assert req.url.path == "/api/board/fpga-1/designs"
    assert req.headers["Authorization"] == "Bearer tok-welland"
    # the unscoped route still works: the slug has exactly one owner
    assert c.get("/api/board/fpga-1/designs").status_code == 200


def test_enable_proxy_forwards_body_token_and_status(c, one_gateway):
    r = c.post(
        "/api/board/welland/fpga-1/designs/tt_um_a/enable", data='{"clock_hz": 5}', content_type="application/json"
    )
    assert r.status_code == 200 and r.json() == {"ok": True, "enabled": "tt_um_a"}
    req = one_gateway.calls(WELLAND)[-1]
    assert req.url.path == "/api/board/fpga-1/designs/tt_um_a/enable"
    assert req.headers["Authorization"] == "Bearer tok-welland"
    assert req.content == b'{"clock_hz": 5}'


def test_bitstream_proxy_forwards_the_upload(c, one_gateway):
    f = SimpleUploadedFile("d.bin", b"\x7e\xaa\x99\x7e", content_type="application/octet-stream")
    r = c.post("/api/board/welland/fpga-1/bitstream", {"name": "my_design", "file": f})
    assert r.status_code == 201 and r.json()["name"] == "up"
    req = one_gateway.calls(WELLAND)[-1]
    assert req.url.path == "/api/board/fpga-1/bitstream"
    assert req.headers["Authorization"] == "Bearer tok-welland"
    assert b"my_design" in req.content and b"\x7e\xaa\x99\x7e" in req.content
    # local validation still applies before anything reaches the gateway
    big = SimpleUploadedFile("big.bin", b"\x00" * (256 * 1024 + 1))
    assert c.post("/api/board/welland/fpga-1/bitstream", {"name": "big", "file": big}).status_code == 400
    assert c.post("/api/board/welland/fpga-1/bitstream", {"name": "nofile"}).status_code == 400


def test_power_proxy_forwards_action_and_token(c, one_gateway):
    r = c.post("/api/board/welland/tt06/power", data='{"action": "cycle"}', content_type="application/json")
    assert r.status_code == 200 and r.json()["admin"] == "on"
    req = one_gateway.calls(WELLAND)[-1]
    assert req.url.path == "/api/board/tt06/power"
    assert req.headers["Authorization"] == "Bearer tok-welland"
    # empty body defaults to cycle; junk actions are rejected locally
    assert c.post("/api/board/welland/tt06/power").status_code == 200
    r = c.post("/api/board/welland/tt06/power", data='{"action": "explode"}', content_type="application/json")
    assert r.status_code == 400
    assert c.post("/api/board/welland/nope/power").status_code == 404  # gateway's 404 passes through
    assert c.post("/api/board/nosite/tt06/power").status_code == 404


def test_gateway_errors_map_to_502_and_503(c, one_gateway):
    one_gateway.overrides[(WELLAND, "GET", "/api/board/fpga-1/designs")] = httpx.ConnectError("refused")
    r = c.get("/api/board/welland/fpga-1/designs")
    assert r.status_code == 503 and "welland" in r.json()["detail"]
    one_gateway.overrides[(WELLAND, "GET", "/api/board/fpga-1/designs")] = httpx.Response(200, text="<html>")
    r = c.get("/api/board/welland/fpga-1/designs")
    assert r.status_code == 502 and "welland" in r.json()["detail"]


# -- two gateways (aggregator mode: same code, N gateways) --


def test_index_two_gateways_merges_boards_stably(c, two_gateways):
    html = c.get("/").content.decode()
    assert '<span class="tt-site">Welland</span>' in html and '<span class="tt-site">PS1</span>' in html
    assert '/board/welland/tt06/' in html and '/board/ps1/tt09/' in html
    # configured gateway order is preserved within each section
    asic = html[html.index('id="asic"'): html.index('id="fpga"')]
    assert asic.index("TT06") < asic.index("TT09")
    assert "hosted at Welland, South Australia and Chicago, Illinois" in html
    assert f"https://{PS1}/live/pi-sw1-p9.m3u8" in html


def test_two_gateways_routes_reach_the_owning_gateway(c, two_gateways):
    assert c.get("/board/ps1/tt09/").status_code == 200
    r = c.post("/api/board/ps1/tt09/power", data='{"action": "on"}', content_type="application/json")
    assert r.status_code == 200
    req = two_gateways.calls(PS1)[-1]
    assert req.headers["Authorization"] == "Bearer tok-ps1"
    # legacy route redirects to the unique owner even with two gateways
    r = c.get("/board/tt09/")
    assert r.status_code == 302 and r.headers["Location"] == "/board/ps1/tt09/"


def test_two_gateways_ambiguous_slug_on_the_legacy_route_is_404(c, two_gateways):
    two_gateways.sites[PS1]["boards"].append(make_board(PS1, "tt06", port=2))
    assert c.get("/board/tt06/").status_code == 404
    # ...but the site-scoped routes stay unambiguous
    assert c.get("/board/welland/tt06/").status_code == 200
    assert c.get("/board/ps1/tt06/").status_code == 200


def test_one_gateway_down_still_lists_the_other(c, two_gateways):
    for path in ("/api/site", "/api/boards"):
        two_gateways.overrides[(WELLAND, "GET", path)] = httpx.ConnectError("refused")
    html = c.get("/").content.decode()
    assert "TT09" in html and '<span class="tt-site">PS1</span>' in html
    assert "TT06" not in html
    # the down site's id still shows in the hosted-at line
    assert "hosted at welland and Chicago, Illinois" in html
