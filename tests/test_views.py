from pathlib import Path

import pytest
from django.core.cache import cache
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
    Board.objects.create(slug="tt07", switch=2, port=7, kind="asic", shuttle="tt07", title="Tiny Tapeout 7")


def test_index_lists_sections(c, boards):
    html = c.get("/").content.decode()
    assert "Tiny Tapeout 6" in html and "KianV uLinux SoC" in html and "TT FPGA emulation board 1" in html
    assert html.count("coming soon") >= 8          # 10 ASIC slots, tt06 live, tt03 disabled => 9 non-live incl. tt03
    assert "/board/tt06/" in html


def test_index_coming_soon_cards_link_to_their_board_page(c, boards):
    html = c.get("/").content.decode()
    # kianv-1 has a row but no port: it still has a page worth reading
    assert '<a href="/board/kianv-1/">Read about it' in html
    # tt03 is an ASIC slot with a row: chip page AND board page
    assert '<a href="/board/tt03/">Read about it' in html
    assert "tinytapeout.com/chips/tt03/" in html
    # an empty ASIC slot has no row, so only the chip page
    assert "/board/tt01/" not in html
    assert "tinytapeout.com/chips/tt01/" in html


def test_board_page_live(c, boards, settings):
    settings.TTSITE_COMMANDER_VERSION = "0.1.0"
    html = c.get("/board/tt06/").content.decode()
    assert "/live/pi-sw1-p6.m3u8" in html
    assert "tt-commander/0.1.0/tt-commander-embed.js" in html
    assert "/ws/board/tt06/serial" in html
    assert 'data-pistat-groups="pi-sw1-p6 pi6"' in html
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


def test_status_json_coming_soon_never_calls_daemon(c, boards, monkeypatch):
    def boom(b, timeout=3.0):  # pragma: no cover - must never run
        raise AssertionError("daemon.health called for a non-live board")

    monkeypatch.setattr(daemon, "health", boom)
    r = c.get("/board/kianv-1/status.json")
    assert r.status_code == 200
    assert r.json() == {"reachable": False, "error": "coming soon"}
    assert cache.get("ttsite:health:kianv-1") is None


def test_status_json_writes_pending_placeholder_before_calling_daemon(c, boards, monkeypatch):
    def slow_health(b, timeout=3.0):
        assert cache.get("ttsite:health:tt06") == {"reachable": False, "error": "pending"}
        return {"reachable": True}

    monkeypatch.setattr(daemon, "health", slow_health)
    assert c.get("/board/tt06/status.json").json() == {"reachable": True}
    assert cache.get("ttsite:health:tt06") == {"reachable": True}


def test_coming_soon_page_has_no_status_polling(c, boards):
    html = c.get("/board/kianv-1/").content.decode()
    assert "data-status-url" not in html
    assert 'id="tt-status"' not in html


def test_docs_page(c):
    html = c.get("/docs/").content.decode()
    assert "tinytapeout.com/guides/get-started-demoboard" in html
    assert "tt-micropython-firmware" in html


def test_chrome_nav_and_footer(c, boards):
    html = c.get("/").content.decode()
    for item in ["Boards", "ASIC", "KianV", "FPGA", "Docs", "tinytapeout.com"]:
        assert item in html
    assert "not operated by Tiny Tapeout Ltd" in html
    assert "ttsite/ttlogo_400.png" in html
    css = Path(__file__).resolve().parents[1] / "ttsite" / "src" / "ttsite" / "static" / "ttsite" / "ttsite.css"
    assert "#544ead" in css.read_text()


def test_board_page_data_attributes(c, boards, settings):
    settings.TTSITE_COMMANDER_VERSION = "0.1.0"
    html = c.get("/board/tt06/").content.decode()
    assert 'id="ttsite-board"' in html
    assert 'data-slug="tt06"' in html and 'data-kind="asic"' in html and 'data-port="6"' in html
    assert 'data-ws-path="/ws/board/tt06/serial"' in html
    assert 'data-status-url="/board/tt06/status.json"' in html
    assert 'data-pistat-groups="pi-sw1-p6 pi6"' in html
    assert "Power-cycle board" in html
    assert "ttsite/board.js" in html


def test_power_button_only_on_switch_one(c, boards):
    """/snmp/toggle only knows switch 1, so a switch-2 board must not offer the button."""
    assert 'id="tt-power"' in c.get("/board/tt06/").content.decode()
    assert 'id="tt-power"' not in c.get("/board/tt07/").content.decode()


def test_board_links_reject_dangerous_schemes(c, boards):
    Board.objects.filter(slug="tt06").update(
        links=[{"label": "x", "url": "javascript:alert(1)"},
               {"label": "ok", "url": "https://example.invalid/a"}],
        pmods=[{"name": "p", "url": "javascript:alert(2)"}],
    )
    html = c.get("/board/tt06/").content.decode()
    assert "javascript:alert" not in html
    assert html.count('href="#"') == 2
    assert 'href="https://example.invalid/a"' in html


def test_board_page_video_js_has_sri(c, boards):
    html = c.get("/board/tt06/").content.decode()
    tags = [line for line in html.splitlines() if "vjs.zencdn.net" in line]
    assert tags
    for tag in tags:
        assert 'integrity="sha384-' in tag


def test_index_orders_fpga_section_before_kianv(c, boards):
    html = c.get("/").content.decode()
    assert html.index('id="fpga"') < html.index('id="kianv"')
    nav = html[: html.index("<main")]
    assert nav.index('href="/#fpga"') < nav.index('href="/#kianv"')


def test_api_designs_proxies(c, boards, monkeypatch):
    monkeypatch.setattr(daemon, "designs", lambda b: (200, {"enabled": "x", "designs": []}))
    r = c.get("/api/board/fpga-1/designs")
    assert r.status_code == 200 and r.json() == {"enabled": "x", "designs": []}


def test_api_designs_rejects_non_fpga_and_unknown(c, boards):
    assert c.get("/api/board/tt06/designs").status_code == 404
    assert c.get("/api/board/tt06/designs").json()["error"] == "not an fpga board"
    assert c.get("/api/board/nope/designs").status_code == 404
    assert c.get("/api/board/kianv-1/designs").status_code == 404  # not live


def test_api_enable_forwards_body_and_status(c, boards, monkeypatch):
    seen = {}

    def fake_enable(b, name, body):
        seen["name"], seen["body"] = name, body
        return 502, {"error": "REPL task failed", "detail": "x"}

    monkeypatch.setattr(daemon, "enable", fake_enable)
    r = c.post("/api/board/fpga-1/designs/tt_um_a/enable", data='{"clock_hz": 5}', content_type="application/json")
    assert r.status_code == 502 and r.json()["error"] == "REPL task failed"
    assert seen == {"name": "tt_um_a", "body": b'{"clock_hz": 5}'}
    assert c.get("/api/board/fpga-1/designs/tt_um_a/enable").status_code == 405


def test_fpga_board_page_has_gallery_and_upload_form(c, boards):
    html = c.get("/board/fpga-1/").content.decode()
    assert 'id="tt-gallery"' in html and 'id="tt-upload"' in html
    assert 'data-api-base="/api/board/fpga-1"' in html
    assert 'name="csrfmiddlewaretoken"' in html
    assert "tinytapeout-fpga-demos" in html


def test_asic_board_page_has_no_gallery(c, boards):
    html = c.get("/board/tt06/").content.decode()
    assert 'id="tt-gallery"' not in html and 'id="tt-upload"' not in html


def test_api_bitstream_forwards_upload_and_rejects_oversize(c, boards, monkeypatch):
    from django.core.files.uploadedfile import SimpleUploadedFile

    seen = {}

    def fake_upload(b, name, fileobj, filename):
        seen.update(name=name, filename=filename, data=fileobj.read())
        return 201, {"name": name, "size": len(seen["data"]), "evicted": []}

    monkeypatch.setattr(daemon, "upload", fake_upload)
    f = SimpleUploadedFile("d.bin", b"\x7e\xaa\x99\x7e" + b"\x00" * 10, content_type="application/octet-stream")
    r = c.post("/api/board/fpga-1/bitstream", {"name": "my_design", "file": f})
    assert r.status_code == 201 and r.json()["name"] == "my_design"
    assert seen["filename"] == "d.bin" and seen["data"].startswith(b"\x7e\xaa\x99\x7e")
    big = SimpleUploadedFile("big.bin", b"\x00" * (256 * 1024 + 1))
    r = c.post("/api/board/fpga-1/bitstream", {"name": "big", "file": big})
    assert r.status_code == 400 and "large" in r.json()["error"]
    r = c.post("/api/board/fpga-1/bitstream", {"name": "nofile"})
    assert r.status_code == 400
