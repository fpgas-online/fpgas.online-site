import pytest
from django.test import Client
from ttsite.models import Board

from ttsite import daemon


@pytest.fixture
def c():
    return Client(HTTP_HOST="tinytapeout.fpgas.online")


@pytest.fixture
def boards(db):
    Board.objects.create(slug="tt06", port=6, kind="asic", shuttle="tt06", title="Tiny Tapeout 6")
    Board.objects.create(slug="tt03", port=3, kind="asic", shuttle="tt03", title="Tiny Tapeout 3", enabled=False)
    Board.objects.create(slug="kianv-1", port=None, kind="kianv", shuttle="tt06", title="KianV uLinux SoC")
    Board.objects.create(slug="fpga-1", port=12, kind="fpga", title="TT FPGA emulation board 1")


def test_index_lists_sections(c, boards):
    html = c.get("/").content.decode()
    assert "Tiny Tapeout 6" in html and "KianV uLinux SoC" in html and "TT FPGA emulation board 1" in html
    assert html.count("coming soon") >= 8          # 10 ASIC slots, tt06 live, tt03 disabled => 9 non-live incl. tt03
    assert "/board/tt06/" in html


def test_board_page_live(c, boards, settings):
    settings.TTSITE_COMMANDER_VERSION = "0.1.0"
    html = c.get("/board/tt06/").content.decode()
    assert "/live/pi-sw1-p6.m3u8" in html
    assert "tt-commander/0.1.0/tt-commander-embed.js" in html
    assert "/ws/board/tt06/serial" in html
    assert "ws/pistat/pi-sw1-p6/" in html and "ws/pistat/pi6/" in html
    assert "tinytapeout.com/chips/tt06/" in html


def test_board_page_without_bundle_shows_notice(c, boards):
    html = c.get("/board/tt06/").content.decode()
    assert "Commander bundle not deployed" in html
    assert "tt-commander-embed.js" not in html


@pytest.mark.parametrize("slug", ["tt03", "kianv-1"])
def test_board_page_coming_soon(c, boards, slug):
    r = c.get(f"/board/{slug}/")
    assert r.status_code == 200
    html = r.content.decode()
    assert "coming soon" in html.lower()
    assert "tt-commander-embed.js" not in html and ".m3u8" not in html


def test_board_404(c, boards):
    assert c.get("/board/nope/").status_code == 404


def test_status_json_proxies_health(c, boards, monkeypatch):
    monkeypatch.setattr(daemon, "health", lambda b, timeout=3.0: {"reachable": True, "board": {"present": True}})
    r = c.get("/board/tt06/status.json")
    assert r.status_code == 200 and r.json()["reachable"] is True


def test_status_json_unreachable_is_200(c, boards, monkeypatch):
    monkeypatch.setattr(daemon, "health", lambda b, timeout=3.0: {"reachable": False, "error": "refused"})
    r = c.get("/board/tt06/status.json")
    assert r.status_code == 200 and r.json() == {"reachable": False, "error": "refused"}


def test_status_json_cached(c, boards, monkeypatch):
    calls = []
    monkeypatch.setattr(daemon, "health", lambda b, timeout=3.0: calls.append(1) or {"reachable": True})
    c.get("/board/tt06/status.json")
    c.get("/board/tt06/status.json")
    assert len(calls) == 1


def test_docs_page(c):
    html = c.get("/docs/").content.decode()
    assert "tinytapeout.com/guides/get-started-demoboard" in html
    assert "tt-micropython-firmware" in html
