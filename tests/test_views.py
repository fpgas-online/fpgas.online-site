from pathlib import Path

import pytest
from django.core.cache import cache
from django.test import Client
from ttsite.models import Board

from ttsite import daemon

from .fleet_pis import verified_pi


@pytest.fixture
def c():
    return Client(HTTP_HOST="tinytapeout.fpgas.online")


def tt(usb_serial, chip, **identity):
    """A Tiny Tapeout board as a Pi's boot check reports it (the variant is `tt-fpga` whatever it is)."""
    return ("tt", "tt-fpga", {"usb_serial": usb_serial, "chip": chip, **identity})


TT06, TT07, FPGA1, TT03P5 = "06060606aaaa0006", "07070707aaaa0007", "f0f0f0f0aaaa0012", "03050305aaaa0035"


@pytest.fixture
def boards(db):
    """The catalogue names boards by USB serial and says nothing of where they are; the Pis' boot checks say
    which board each carries. tt03 and kianv-1 are rows no Pi reports."""
    Board.objects.create(slug="tt06", usb_serial=TT06, kind="asic", title="Tiny Tapeout 6")
    Board.objects.create(slug="tt03", kind="asic", shuttle="tt03", title="Tiny Tapeout 3")
    Board.objects.create(slug="kianv-1", kind="kianv", shuttle="tt06", title="KianV uLinux SoC")
    Board.objects.create(slug="fpga-1", usb_serial=FPGA1, kind="fpga", title="TT FPGA emulation board 1")
    Board.objects.create(slug="tt07", usb_serial=TT07, kind="asic", title="Tiny Tapeout 7")
    verified_pi("pi-sw1-p6", tt(TT06, "asic", shuttle="tt06", sdk="2.0.4", mcu="RP2040"))
    verified_pi("pi-sw1-p12", tt(FPGA1, "fpga", sdk="3.1.0", mcu="RP2350", demoboard="TTDBv3 [3.2]"))
    verified_pi("pi-sw2-p7", tt(TT07, "asic", shuttle="tt07", sdk="2.0.4"))


def test_index_lists_sections(c, boards):
    html = c.get("/").content.decode()
    assert "Tiny Tapeout 6" in html and "KianV uLinux SoC" in html and "TT FPGA emulation board 1" in html
    # tt03 and kianv-1 are rows no Pi reports; tt06, tt07 and fpga-1 are live
    assert html.count("not connected") == 2
    assert "/board/tt06/" in html


def test_index_cards_of_boards_no_pi_reports_link_to_their_page(c, boards):
    html = c.get("/").content.decode()
    # kianv-1 is a row no Pi reports: it still has a page worth reading
    assert '<a href="/board/kianv-1/">Read about it' in html
    # tt03 is a catalogue row: chip page AND board page
    assert '<a href="/board/tt03/">Read about it' in html
    assert "tinytapeout.com/chips/tt03/" in html
    # no catalogue row -> no card at all (no more fixed TT01..TT10 slots)
    assert "tt01" not in html


def test_index_asic_card_without_shuttle_has_no_chip_link(c, boards, db):
    Board.objects.create(slug="ttgf", kind="asic", shuttle="", title="Tiny Tapeout GF")
    html = c.get("/").content.decode()
    assert "Tiny Tapeout GF" in html
    assert '<a href="/board/ttgf/">Read about it' in html
    assert "tinytapeout.com/chips//" not in html


def test_nav_active_state_and_scrollspy_hooks(c, boards):
    html = c.get("/").content.decode()
    assert 'class="is-active" aria-current="page">Boards</a>' in html
    assert 'data-section="asic"' in html and 'data-section="fpga"' in html and 'data-section="kianv"' in html
    docs = c.get("/docs/").content.decode()
    assert 'class="is-active" aria-current="page">Docs</a>' in docs
    assert 'aria-current="page">Boards</a>' not in docs


def test_board_page_live(c, boards, settings):
    settings.TTSITE_COMMANDER_VERSION = "0.1.0"
    html = c.get("/board/tt06/").content.decode()
    assert "/live/pi-sw1-p6.m3u8" in html
    assert "tt-commander/0.1.0/tt-commander-embed.js" in html
    assert "/ws/board/tt06/serial" in html
    assert 'data-pistat-groups="pi-sw1-p6"' in html
    assert "tinytapeout.com/chips/tt06/" in html


def test_board_page_without_bundle_shows_notice(c, boards):
    html = c.get("/board/tt06/").content.decode()
    assert "Commander bundle not deployed" in html
    assert "TTSITE_COMMANDER_VERSION is empty" in html
    assert "tt-commander-embed.js" not in html


@pytest.fixture
def legacy_board(db):
    """A board on firmware older than SDK 2: the board's own SDK version picks the Commander, no list does."""
    verified_pi("pi-sw2-p3", tt(TT03P5, "asic", shuttle="tt03p5", sdk="1.2.2"))
    return Board.objects.create(slug="tt03p5", usb_serial=TT03P5, kind="asic", title="Tiny Tapeout 3.5")


def test_board_page_legacy_commander_uses_legacy_bundle(c, boards, legacy_board, settings):
    settings.TTSITE_COMMANDER_VERSION = "0.2.0"
    settings.TTSITE_COMMANDER_LEGACY_VERSION = "0.1.0"
    html = c.get("/board/tt03p5/").content.decode()
    assert "tt-commander/legacy-0.1.0/tt-commander-embed.js" in html
    assert "tt-commander/legacy-0.1.0/tt-commander-embed.css" in html
    assert "tt-commander/0.2.0/" not in html
    # the main-commander board is untouched by the legacy pin
    tt06 = c.get("/board/tt06/").content.decode()
    assert "tt-commander/0.2.0/tt-commander-embed.js" in tt06
    assert "legacy-0.1.0" not in tt06


def test_board_page_legacy_commander_without_bundle_names_legacy_setting(c, boards, legacy_board, settings):
    settings.TTSITE_COMMANDER_VERSION = "0.2.0"  # main bundle deployed; legacy not
    html = c.get("/board/tt03p5/").content.decode()
    assert "TTSITE_COMMANDER_LEGACY_VERSION is empty" in html
    assert "tt-commander-embed.js" not in html


@pytest.mark.parametrize("slug", ["tt03", "kianv-1"])
def test_board_page_of_a_board_no_pi_reports(c, boards, slug):
    r = c.get(f"/board/{slug}/")
    assert r.status_code == 200
    html = r.content.decode()
    assert "not connected" in html.lower()
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


def test_status_json_of_a_board_no_pi_reports_never_calls_daemon(c, boards, monkeypatch):
    def boom(b, timeout=3.0):  # pragma: no cover - must never run
        raise AssertionError("daemon.health called for a non-live board")

    monkeypatch.setattr(daemon, "health", boom)
    r = c.get("/board/kianv-1/status.json")
    assert r.status_code == 200
    assert r.json() == {"reachable": False, "error": "not connected"}


def test_status_json_writes_pending_placeholder_before_calling_daemon(c, boards, monkeypatch):
    def slow_health(b, timeout=3.0):
        assert cache.get("ttsite:health:tt06") == {"reachable": False, "error": "pending"}
        return {"reachable": True}

    monkeypatch.setattr(daemon, "health", slow_health)
    assert c.get("/board/tt06/status.json").json() == {"reachable": True}
    assert cache.get("ttsite:health:tt06") == {"reachable": True}


def test_page_of_a_board_no_pi_reports_has_no_status_polling(c, boards):
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
    assert 'data-pistat-groups="pi-sw1-p6"' in html
    assert "Power-cycle board" in html
    assert "ttsite/board.js" in html


def test_power_button_on_every_board_the_site_shows(c, boards):
    """On either switch; a board no Pi reports has none.
    /snmp/toggle accepts exactly the same boards: tests/test_poe_policy.py."""
    assert 'id="tt-power"' in c.get("/board/tt06/").content.decode()
    tt07 = c.get("/board/tt07/").content.decode()  # on the second switch
    assert 'id="tt-power"' in tt07 and 'data-switch="2"' in tt07 and 'data-port="7"' in tt07
    assert 'id="tt-power"' not in c.get("/board/tt03/").content.decode()
    assert 'id="tt-power"' not in c.get("/board/kianv-1/").content.decode()


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


def test_api_designs_passes_through_daemon_failure_status(c, boards, monkeypatch):
    monkeypatch.setattr(daemon, "designs", lambda b: (502, {"error": "Pi unreachable", "detail": "refused"}))
    r = c.get("/api/board/fpga-1/designs")
    assert r.status_code == 502
    assert r.json() == {"error": "Pi unreachable", "detail": "refused"}


def test_api_designs_is_cached_for_a_few_seconds(c, boards, monkeypatch):
    calls = []

    def fake_designs(b):
        calls.append(1)
        return 200, {"enabled": None, "designs": []}

    monkeypatch.setattr(daemon, "designs", fake_designs)
    c.get("/api/board/fpga-1/designs")
    c.get("/api/board/fpga-1/designs")
    assert len(calls) == 1


def test_api_designs_failure_is_not_cached(c, boards, monkeypatch):
    calls = []

    def fake_designs(b):
        calls.append(1)
        return 502, {"error": "Pi unreachable", "detail": "refused"}

    monkeypatch.setattr(daemon, "designs", fake_designs)
    c.get("/api/board/fpga-1/designs")
    c.get("/api/board/fpga-1/designs")
    assert len(calls) == 2


def test_api_enable_and_bitstream_reject_non_fpga_and_unknown(c, boards):
    assert c.post("/api/board/tt06/designs/x/enable").status_code == 404
    assert c.post("/api/board/tt06/designs/x/enable").json()["error"] == "not an fpga board"
    assert c.post("/api/board/nope/designs/x/enable").status_code == 404
    assert c.post("/api/board/kianv-1/designs/x/enable").status_code == 404  # not live
    assert c.post("/api/board/tt06/bitstream").status_code == 404
    assert c.post("/api/board/tt06/bitstream").json()["error"] == "not an fpga board"
    assert c.post("/api/board/nope/bitstream").status_code == 404
    assert c.post("/api/board/kianv-1/bitstream").status_code == 404  # not live


def test_api_bitstream_rejects_non_post(c, boards):
    assert c.get("/api/board/fpga-1/bitstream").status_code == 405


def test_api_enable_invalidates_designs_cache(c, boards, monkeypatch):
    calls = []

    def fake_designs(b):
        calls.append(1)
        return 200, {"enabled": None, "designs": []}

    monkeypatch.setattr(daemon, "designs", fake_designs)
    monkeypatch.setattr(daemon, "enable", lambda b, name, body: (200, {"ok": True}))
    c.get("/api/board/fpga-1/designs")
    c.post("/api/board/fpga-1/designs/tt_um_a/enable")
    c.get("/api/board/fpga-1/designs")
    assert len(calls) == 2


def test_api_bitstream_invalidates_designs_cache(c, boards, monkeypatch):
    from django.core.files.uploadedfile import SimpleUploadedFile

    calls = []

    def fake_designs(b):
        calls.append(1)
        return 200, {"enabled": None, "designs": []}

    monkeypatch.setattr(daemon, "designs", fake_designs)
    monkeypatch.setattr(daemon, "upload", lambda b, name, fileobj, filename: (201, {"name": name, "size": 1, "evicted": []}))
    c.get("/api/board/fpga-1/designs")
    f = SimpleUploadedFile("d.bin", b"\x00")
    c.post("/api/board/fpga-1/bitstream", {"name": "my_design", "file": f})
    c.get("/api/board/fpga-1/designs")
    assert len(calls) == 2


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


def test_fpga_board_page_not_live_shows_notice_not_gallery(c, boards):
    Board.objects.create(slug="fpga-2", kind="fpga", title="TT FPGA emulation board 2")
    html = c.get("/board/fpga-2/").content.decode()
    assert "this one is not connected" in html
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


# --- the VGA library routes (spec section 3, Routes). The archive client and the daemon are fakes; the clock is
# views.now.

import json  # noqa: E402

import redis  # noqa: E402
import requests  # noqa: E402

from ttsite import library, views  # noqa: E402

LIB_BASE = "https://data.wafer.space/big-storage/tinytapeout-vga-videos/"
LIB_FIXTURE = json.loads((Path(__file__).parent / "data" / "fpga-library.json").read_text())
LIB_INDEX, _ = library.normalise(json.loads(json.dumps(LIB_FIXTURE)), LIB_BASE)
LIB_BODY = json.dumps(LIB_INDEX, separators=(",", ":")).encode()
LIB_ETAG = library._site_etag(LIB_BODY)
ROW0, ROW1 = LIB_INDEX["designs"][0], LIB_INDEX["designs"][1]
LOAD = "/api/board/fpga-1/library/{}/load"
OUT_OF_TIME = {"error": "the load ran out of time; try again", "detail": ""}


def lib_bin(row):
    """The fixture's bin for `row` (tests/test_library.py fixture_bin)."""
    data = bytearray(4096)
    data[:8] = bytes.fromhex("ff0000ff7eaa997e")
    data[8] = row
    return bytes(data)


class LibClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def _no_archive(monkeypatch):
    """No test here reaches the archive: the upload-name check of the older upload tests sees it down."""

    def blocked(*args, **kwargs):
        raise requests.ConnectionError("tests do not reach the archive")

    monkeypatch.setattr(library.SESSION, "get", blocked)


@pytest.fixture
def lib_clock(monkeypatch):
    clock = LibClock()
    monkeypatch.setattr(views, "now", clock)
    return clock


@pytest.fixture
def lib_index(monkeypatch, settings):
    """library.index answers with the fixture; `calls` records each deadline asked for."""
    settings.TTSITE_LIBRARY_BASE_URL = LIB_BASE
    state = {"answer": (LIB_BODY, LIB_ETAG, None), "calls": []}

    def fake_index(deadline):
        state["calls"].append(deadline)
        if isinstance(state["answer"], Exception):
            raise state["answer"]
        return state["answer"]

    monkeypatch.setattr(library, "index", fake_index)
    return state


class FakeLibDaemon:
    """daemon.library_state, library_push and enable, each answering in turn and taking fake time."""

    def __init__(self, clock, state=None, pushes=None, enables=None):
        self.clock = clock
        self.state = state if state is not None else (200, {"max_bytes": 1, "used_bytes": 0, "entries": []})
        self.pushes = list(pushes or [(201, {"evicted": [], "hides_upload": False}, 1)])
        self.enables = list(enables or [(200, {"enabled": "x"}, 1)])
        self.calls = []

    def library_state(self, board, *, deadline):
        self.calls.append(("state", deadline))
        if isinstance(self.state, Exception):
            raise self.state
        return self.state

    def library_push(self, board, name, data, sha256, *, deadline):
        self.calls.append(("push", name, data, sha256, deadline))
        status, body, took = self.pushes.pop(0)
        self.clock.t += took
        return status, {"id": name, "bin_bytes": len(data), "bin_sha256": sha256, **body}

    def enable(self, board, name, body, *, deadline=None):
        self.calls.append(("enable", name, json.loads(body), deadline))
        status, answer, took = self.enables.pop(0)
        self.clock.t += took
        return status, dict(answer)

    def kinds(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def lib_daemon(monkeypatch, lib_clock):
    def install(**kw):
        fake = FakeLibDaemon(lib_clock, **kw)
        monkeypatch.setattr(daemon, "library_state", fake.library_state)
        monkeypatch.setattr(daemon, "library_push", fake.library_push)
        monkeypatch.setattr(daemon, "enable", fake.enable)
        return fake

    return install


@pytest.fixture
def lib_bins(monkeypatch, lib_clock):
    """library.fetch_bin answers each fixture row's bin; `calls` records the ids fetched."""
    state = {"calls": [], "error": None, "took": 1}
    rows = {e["id"]: i for i, e in enumerate(LIB_INDEX["designs"])}

    def fake_fetch_bin(entry, deadline):
        state["calls"].append((entry["id"], deadline))
        lib_clock.t += state["took"]
        if state["error"] is not None:
            raise state["error"]
        return lib_bin(rows[entry["id"]])

    monkeypatch.setattr(library, "fetch_bin", fake_fetch_bin)
    return state


# --- the index route


def test_library_index_serves_the_index(c, boards, lib_index):
    r = c.get("/api/board/fpga-1/library")
    assert r.status_code == 200 and r["Content-Type"] == "application/json"
    assert r.content == LIB_BODY
    assert r["ETag"] == LIB_ETAG
    assert r["Cache-Control"] == "private, max-age=60"
    assert not r.has_header("X-TTSite-Library-Stale")


def test_library_index_etag_304_under_gzip(c, boards, lib_index):
    r = c.get("/api/board/fpga-1/library", HTTP_ACCEPT_ENCODING="gzip")
    assert r.status_code == 200 and r["Content-Encoding"] == "gzip"
    assert r["ETag"] == LIB_ETAG and LIB_ETAG.startswith('W/"sha256-')
    r = c.get("/api/board/fpga-1/library", HTTP_ACCEPT_ENCODING="gzip", HTTP_IF_NONE_MATCH=LIB_ETAG)
    assert r.status_code == 304 and r.content == b""
    # the echo of a strong form of the same tag also matches: Django compares If-None-Match weakly
    r = c.get("/api/board/fpga-1/library", HTTP_IF_NONE_MATCH=LIB_ETAG[2:])
    assert r.status_code == 304
    r = c.get("/api/board/fpga-1/library", HTTP_ACCEPT_ENCODING="gzip", HTTP_IF_NONE_MATCH='W/"sha256-other"')
    assert r.status_code == 200


def test_library_index_stale_header(c, boards, lib_index):
    lib_index["answer"] = (LIB_BODY, LIB_ETAG, 120)
    r = c.get("/api/board/fpga-1/library")
    assert r.status_code == 200 and r["X-TTSite-Library-Stale"] == "120"


def test_library_index_not_published_404(c, boards, lib_index):
    lib_index["answer"] = library.NotPublished("VGA library not published")
    r = c.get("/api/board/fpga-1/library")
    assert r.status_code == 404
    assert r.json() == {"error": "VGA library not published", "detail": ""}
    assert not r.has_header("ETag")
    # a 304 is never given for an index that is not there
    r = c.get("/api/board/fpga-1/library", HTTP_IF_NONE_MATCH=LIB_ETAG)
    assert r.status_code == 404


def test_index_cold_and_archive_down_is_502(c, boards, lib_index):
    lib_index["answer"] = library.ArchiveError("archive unreachable")
    r = c.get("/api/board/fpga-1/library")
    assert r.status_code == 502
    assert r.json() == {"error": "VGA library unavailable", "detail": "archive unreachable"}


def test_library_index_404_on_asic_board(c, boards, lib_index):
    r = c.get("/api/board/tt06/library", HTTP_IF_NONE_MATCH=LIB_ETAG)
    assert r.status_code == 404 and r.json()["error"] == "not an fpga board"
    assert c.get("/api/board/nope/library").status_code == 404
    assert lib_index["calls"] == []


def test_library_index_board_check_is_cached_per_slug(c, boards, lib_index, monkeypatch):
    calls = []
    real = views._fpga_board_or_error
    monkeypatch.setattr(views, "_fpga_board_or_error", lambda slug: calls.append(slug) or real(slug))
    c.get("/api/board/fpga-1/library")
    c.get("/api/board/fpga-1/library")
    c.get("/api/board/tt06/library")
    c.get("/api/board/tt06/library")
    assert calls == ["fpga-1", "tt06"]


def test_library_index_board_not_ready_is_cached_5s(c, boards, lib_index, monkeypatch):
    """Ruling AW: an answer or a 404 is kept for 60 s, a 503 "board not ready" only for STATUS_CACHE_SECONDS."""
    from django.http import JsonResponse

    ttls = {}
    real_set, real_check = cache.set, views._fpga_board_or_error

    def recording_set(key, value, timeout=None):
        if key.startswith("ttsite:library:board:"):
            ttls[key] = timeout
        return real_set(key, value, timeout)

    def not_ready(slug):
        return None, JsonResponse({"error": "board not ready", "detail": "x"}, status=503)

    monkeypatch.setattr(cache, "set", recording_set)
    monkeypatch.setattr(views, "_fpga_board_or_error", not_ready)
    r = c.get("/api/board/fpga-1/library")
    assert r.status_code == 503 and r.json() == {"error": "board not ready", "detail": "x"}
    assert ttls == {"ttsite:library:board:fpga-1": 5}
    monkeypatch.setattr(views, "_fpga_board_or_error", real_check)
    cache.delete("ttsite:library:board:fpga-1")
    assert c.get("/api/board/fpga-1/library").status_code == 200
    assert c.get("/api/board/tt06/library").status_code == 404
    assert ttls == {"ttsite:library:board:fpga-1": 60, "ttsite:library:board:tt06": 60}
    assert views.BOARD_NOT_READY_SECONDS == views.STATUS_CACHE_SECONDS == 5


def test_library_index_rejects_post(c, boards, lib_index):
    assert c.post("/api/board/fpga-1/library").status_code == 405


def test_library_off_when_setting_empty(c, boards, lib_index, lib_daemon, lib_bins, settings, monkeypatch):
    settings.TTSITE_LIBRARY_BASE_URL = ""
    fake = lib_daemon()
    r = c.get("/api/board/fpga-1/library")
    assert r.status_code == 404 and r.json()["error"] == "VGA library not enabled"
    r = c.post(LOAD.format(ROW0["id"]), data="{}", content_type="application/json")
    assert r.status_code == 404 and r.json()["error"] == "VGA library not enabled"
    assert lib_index["calls"] == [] and fake.calls == []


def test_library_disabled_raised_by_the_client_is_404_not_502(c, boards, lib_index, lib_daemon):
    """LibraryDisabled is not an archive error: never a 502, never a stale copy."""
    lib_daemon()
    lib_index["answer"] = library.LibraryDisabled("TTSITE_LIBRARY_BASE_URL is empty")
    assert c.get("/api/board/fpga-1/library").status_code == 404
    assert c.post(LOAD.format(ROW0["id"]), data="{}", content_type="application/json").status_code == 404


# --- the load route, happy paths


def post_load(c, name, **kw):
    return c.post(LOAD.format(name), data="{}", content_type="application/json", **kw)


def test_load_pushes_then_enables_with_index_values(c, boards, lib_index, lib_daemon, lib_bins, lib_clock):
    start = lib_clock.t
    fake = lib_daemon(enables=[(200, {"enabled": ROW1["id"], "released": []}, 1)])
    r = post_load(c, ROW1["id"])
    assert r.status_code == 200
    assert r.json() == {"enabled": ROW1["id"], "released": [], "pushed": True, "hides_upload": False}
    assert fake.kinds() == ["state", "push", "enable"]
    _, name, data, sha, deadline = fake.calls[1]
    assert (name, data, sha) == (ROW1["id"], lib_bin(1), ROW1["bin_sha256"])
    assert fake.calls[2][1:3] == (ROW1["id"], {"clock_hz": 25175000, "ui_in": None, "ui_in_mask": 0})
    # one deadline, 50 s after the start, for every call
    assert {call[-1] for call in fake.calls} == {start + 50} and lib_bins["calls"] == [(ROW1["id"], start + 50)]

    fake = lib_daemon()
    assert post_load(c, ROW0["id"]).status_code == 200
    assert fake.calls[2][2] == {"clock_hz": 25175000, "ui_in": 1, "ui_in_mask": 1}


def test_load_cached_entry_skips_fetch(c, boards, lib_index, lib_daemon, lib_bins):
    held = {"id": ROW0["id"], "bin_sha256": ROW0["bin_sha256"], "bin_bytes": 4096, "used_at": "x"}
    fake = lib_daemon(state=(200, {"max_bytes": 1, "used_bytes": 4096, "entries": [held]}))
    r = post_load(c, ROW0["id"])
    assert r.status_code == 200 and r.json()["pushed"] is False and r.json()["hides_upload"] is False
    assert fake.kinds() == ["state", "enable"] and lib_bins["calls"] == []


def test_load_pushes_again_when_the_pi_holds_an_older_build(c, boards, lib_index, lib_daemon, lib_bins):
    old = {"id": ROW0["id"], "bin_sha256": "0" * 64, "bin_bytes": 4096, "used_at": "x"}
    fake = lib_daemon(state=(200, {"max_bytes": 1, "used_bytes": 4096, "entries": [old]}))
    r = post_load(c, ROW0["id"])
    assert r.status_code == 200 and r.json()["pushed"] is True
    assert fake.kinds() == ["state", "push", "enable"]


def test_load_clears_designs_cache_and_passes_hides_upload(c, boards, lib_index, lib_daemon, lib_bins):
    cache.set("ttsite:designs:fpga-1", (200, {"designs": []}), 60)
    lib_daemon(pushes=[(201, {"evicted": [], "hides_upload": True}, 1)])
    r = post_load(c, ROW0["id"])
    assert r.status_code == 200 and r.json()["hides_upload"] is True and r.json()["pushed"] is True
    assert cache.get("ttsite:designs:fpga-1") is None


def test_load_failure_keeps_designs_cache(c, boards, lib_index, lib_daemon, lib_bins):
    cache.set("ttsite:designs:fpga-1", (200, {"designs": []}), 60)
    lib_daemon(enables=[(502, {"error": "REPL task failed", "detail": "x"}, 1)])
    r = post_load(c, ROW0["id"])
    assert r.status_code == 502 and r.json() == {"error": "REPL task failed", "detail": "x"}
    assert cache.get("ttsite:designs:fpga-1") is not None


# --- the load route, failures


def test_load_old_daemon_501(c, boards, lib_index, lib_daemon, lib_bins):
    fake = lib_daemon(state=daemon.Unsupported("no /library"))
    r = post_load(c, ROW0["id"])
    assert r.status_code == 501
    assert r.json() == {"error": "board software too old",
                        "detail": "this board's fpgas-tt daemon has no VGA library support yet"}  # fmt: skip
    assert fake.kinds() == ["state"] and lib_bins["calls"] == []


def test_load_state_transport_failure_passes_through(c, boards, lib_index, lib_daemon, lib_bins):
    fake = lib_daemon(state=(502, {"error": "Pi unreachable", "detail": "refused"}))
    r = post_load(c, ROW0["id"])
    assert r.status_code == 502 and r.json() == {"error": "Pi unreachable", "detail": "refused"}
    assert fake.kinds() == ["state"]


def test_load_push_failure_passes_through(c, boards, lib_index, lib_daemon, lib_bins):
    fake = lib_daemon(pushes=[(500, {"error": "the library entry could not be kept on the Pi", "detail": ""}, 1)])
    r = post_load(c, ROW0["id"])
    assert r.status_code == 500 and r.json()["error"] == "the library entry could not be kept on the Pi"
    assert fake.kinds() == ["state", "push"]


def test_load_retries_once_after_404_enable(c, boards, lib_index, lib_daemon, lib_bins):
    """Review Focus 4: the Pi rebooted after GET /library, so its cache is empty; push again, enable once more."""
    held = {"id": ROW0["id"], "bin_sha256": ROW0["bin_sha256"], "bin_bytes": 4096, "used_at": "x"}
    fake = lib_daemon(
        state=(200, {"max_bytes": 1, "used_bytes": 4096, "entries": [held]}),
        enables=[(404, {"error": "no such design", "detail": ""}, 1), (200, {"enabled": ROW0["id"]}, 1)],
    )
    r = post_load(c, ROW0["id"])
    assert r.status_code == 200 and r.json() == {"enabled": ROW0["id"], "pushed": True, "hides_upload": False}
    assert fake.kinds() == ["state", "enable", "push", "enable"]
    assert len(lib_bins["calls"]) == 1


def test_load_404_after_a_push_is_not_retried(c, boards, lib_index, lib_daemon, lib_bins):
    fake = lib_daemon(enables=[(404, {"error": "no such design", "detail": ""}, 1)])
    r = post_load(c, ROW0["id"])
    assert r.status_code == 404 and r.json()["error"] == "no such design"
    assert fake.kinds() == ["state", "push", "enable"]


def test_load_no_retry_under_32s_left(c, boards, lib_index, lib_daemon, lib_bins):
    held = {"id": ROW0["id"], "bin_sha256": ROW0["bin_sha256"], "bin_bytes": 4096, "used_at": "x"}
    fake = lib_daemon(
        state=(200, {"max_bytes": 1, "used_bytes": 4096, "entries": [held]}),
        enables=[(404, {"error": "no such design", "detail": ""}, 19)],  # 50 - 19 = 31 s left
    )
    r = post_load(c, ROW0["id"])
    assert r.status_code == 404 and r.json() == {"error": "no such design", "detail": ""}
    assert fake.kinds() == ["state", "enable"] and lib_bins["calls"] == []


def test_load_retry_at_exactly_32s_left(c, boards, lib_index, lib_daemon, lib_bins):
    held = {"id": ROW0["id"], "bin_sha256": ROW0["bin_sha256"], "bin_bytes": 4096, "used_at": "x"}
    lib_bins["took"] = 0
    fake = lib_daemon(
        state=(200, {"max_bytes": 1, "used_bytes": 4096, "entries": [held]}),
        pushes=[(201, {"evicted": [], "hides_upload": False}, 0)],
        enables=[(404, {"error": "no such design", "detail": ""}, 18), (200, {"enabled": ROW0["id"]}, 1)],
    )
    assert post_load(c, ROW0["id"]).status_code == 200
    assert fake.kinds() == ["state", "enable", "push", "enable"]


def test_load_504_when_under_27s_before_enable(c, boards, lib_index, lib_daemon, lib_bins):
    lib_bins["took"] = 23.5  # the fetch and the push leave 50 - 23.5 - 0.5 = 26 s
    fake = lib_daemon(pushes=[(201, {"evicted": [], "hides_upload": False}, 0.5)])
    r = post_load(c, ROW0["id"])
    assert r.status_code == 504 and r.json() == OUT_OF_TIME
    assert fake.kinds() == ["state", "push"]


def test_load_enables_with_exactly_27s_left(c, boards, lib_index, lib_daemon, lib_bins):
    lib_bins["took"] = 22.5
    fake = lib_daemon(pushes=[(201, {"evicted": [], "hides_upload": False}, 0.5)])
    assert post_load(c, ROW0["id"]).status_code == 200
    assert fake.kinds() == ["state", "push", "enable"]


def test_load_retry_rechecks_enable_floor(c, boards, lib_index, lib_daemon, lib_bins):
    """Finding N4: the first enable answers 404 with 34 s left; the retry's push takes 9 s, so 25 s remain."""
    held = {"id": ROW0["id"], "bin_sha256": ROW0["bin_sha256"], "bin_bytes": 4096, "used_at": "x"}
    lib_bins["took"] = 0
    fake = lib_daemon(
        state=(200, {"max_bytes": 1, "used_bytes": 4096, "entries": [held]}),
        pushes=[(201, {"evicted": [], "hides_upload": False}, 9)],
        enables=[(404, {"error": "no such design", "detail": ""}, 16), (200, {"enabled": ROW0["id"]}, 1)],
    )
    r = post_load(c, ROW0["id"])
    assert r.status_code == 504 and r.json() == OUT_OF_TIME
    assert fake.kinds() == ["state", "enable", "push"]


def test_load_bin_404_says_still_syncing(c, boards, lib_index, lib_daemon, lib_bins):
    """Review Focus 1: the index arrives before its bins; the visitor reads the syncing message."""
    lib_bins["error"] = library.ArchiveError(library.MSG_BIN_MISSING)
    fake = lib_daemon()
    r = post_load(c, ROW0["id"])
    assert r.status_code == 503 and r["Retry-After"] == "30"
    assert r.json() == {
        "error": "archive file missing: the archive is still syncing; try again in a few minutes", "detail": ""}
    assert fake.kinds() == ["state"]


def test_load_bin_mismatch_is_502(c, boards, lib_index, lib_daemon, lib_bins):
    lib_bins["error"] = library.ArchiveError(library.MSG_BIN_MISMATCH)
    lib_daemon()
    r = post_load(c, ROW0["id"])
    assert r.status_code == 502 and r.json() == {"error": "archive file does not match the index", "detail": ""}


def test_load_unknown_id_404(c, boards, lib_index, lib_daemon, lib_bins):
    fake = lib_daemon()
    r = post_load(c, "ttsky25a_not_in_the_index")
    assert r.status_code == 404 and r.json() == {"error": "no such library design", "detail": ""}
    assert fake.calls == [] and lib_bins["calls"] == []


@pytest.mark.parametrize("name", ["Upper", "a" * 41, "has-dash", "dot.bin", "%2e%2e"])
def test_load_bad_name_404(c, boards, lib_index, lib_daemon, lib_bins, name):
    fake = lib_daemon()
    r = post_load(c, name)
    assert r.status_code == 404 and r.json() == {"error": "no such library design", "detail": ""}
    assert fake.calls == [] and lib_index["calls"] == []


def test_load_not_published_and_archive_down(c, boards, lib_index, lib_daemon, lib_bins):
    fake = lib_daemon()
    lib_index["answer"] = library.NotPublished("VGA library not published")
    r = post_load(c, ROW0["id"])
    assert r.status_code == 404 and r.json() == {"error": "VGA library not published", "detail": ""}
    lib_index["answer"] = library.ArchiveError("archive unreachable")
    r = post_load(c, ROW0["id"])
    assert r.status_code == 502 and r.json() == {"error": "VGA library unavailable", "detail": "archive unreachable"}
    assert fake.calls == []


def test_load_rejects_non_fpga_board_and_get(c, boards, lib_index, lib_daemon, lib_bins):
    fake = lib_daemon()
    r = c.post(f"/api/board/tt06/library/{ROW0['id']}/load", data="{}", content_type="application/json")
    assert r.status_code == 404 and r.json()["error"] == "not an fpga board"
    assert c.get(LOAD.format(ROW0["id"])).status_code == 405
    assert fake.calls == []


# --- the lock and CSRF


class FakeLockCache:
    def __init__(self, add_answer=True, add_raises=None):
        self.add_answer, self.add_raises = add_answer, add_raises
        self.added, self.deleted = [], []

    def add(self, key, value, timeout=None):
        self.added.append((key, value, timeout))
        if self.add_raises is not None:
            raise self.add_raises
        return self.add_answer

    def delete(self, key):
        self.deleted.append(key)


def test_load_lock_uses_ttsite_cache(c, boards, lib_index, lib_daemon, lib_bins, monkeypatch):
    held = FakeLockCache(add_answer=False)
    monkeypatch.setattr(views, "caches", {"ttsite": held})
    fake = lib_daemon()
    r = post_load(c, ROW0["id"])
    assert r.status_code == 409 and r.json() == {"error": "a library load is already running on this board",
                                                 "detail": ""}  # fmt: skip
    assert held.added == [("library-load:fpga-1", 1, 70)] and held.deleted == [] and fake.calls == []

    down = FakeLockCache(add_raises=redis.exceptions.ConnectionError("refused"))
    monkeypatch.setattr(views, "caches", {"ttsite": down})
    r = post_load(c, ROW0["id"])
    assert r.status_code == 503 and r.json()["error"] == "the site cannot take the board lock; try again"
    assert down.deleted == [] and fake.calls == []

    free = FakeLockCache()
    monkeypatch.setattr(views, "caches", {"ttsite": free})
    assert post_load(c, ROW0["id"]).status_code == 200
    assert free.deleted == ["library-load:fpga-1"]

    failing = FakeLockCache()
    monkeypatch.setattr(views, "caches", {"ttsite": failing})
    lib_daemon(state=daemon.Unsupported("old"))
    assert post_load(c, ROW0["id"]).status_code == 501
    assert failing.deleted == ["library-load:fpga-1"]

    raising = FakeLockCache()
    monkeypatch.setattr(views, "caches", {"ttsite": raising})
    lib_daemon(state=RuntimeError("a bug"))
    with pytest.raises(RuntimeError):
        post_load(c, ROW0["id"])
    assert raising.deleted == ["library-load:fpga-1"]


def test_load_lock_is_held_during_the_load_and_freed_after(c, boards, lib_index, lib_daemon, lib_bins, monkeypatch):
    from django.core.cache import caches

    seen = []
    fake = lib_daemon()
    real_state = fake.library_state

    def state(board, *, deadline):
        seen.append(caches["ttsite"].get("library-load:fpga-1"))
        return real_state(board, deadline=deadline)

    monkeypatch.setattr(daemon, "library_state", state)
    assert post_load(c, ROW0["id"]).status_code == 200
    assert seen == [1] and caches["ttsite"].get("library-load:fpga-1") is None


def test_load_requires_csrf(boards, lib_index, lib_daemon, lib_bins):
    lib_daemon()
    strict = Client(HTTP_HOST="tinytapeout.fpgas.online", enforce_csrf_checks=True)
    assert post_load(strict, ROW0["id"]).status_code == 403
    token = "t" * 32
    strict.cookies["csrftoken"] = token
    assert post_load(strict, ROW0["id"]).status_code == 403  # the cookie alone is not enough
    r = post_load(strict, ROW0["id"], HTTP_X_CSRFTOKEN=token)
    assert r.status_code == 200


# --- the upload-name check


def upload_file():
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile("d.bin", b"\x7e\xaa\x99\x7e" + b"\x00" * 10)


@pytest.fixture
def uploads(monkeypatch):
    calls = []
    monkeypatch.setattr(
        daemon, "upload", lambda b, name, fileobj, filename: calls.append(name) or (201, {"name": name, "evicted": []})
    )
    return calls


def test_upload_refused_for_index_name(c, boards, lib_index, lib_clock, uploads):
    r = c.post("/api/board/fpga-1/bitstream", {"name": ROW1["id"], "file": upload_file()})
    assert r.status_code == 409
    assert r.json() == {"error": f"{ROW1['id']} is a VGA library design; pick another", "detail": ""}
    assert uploads == [] and lib_index["calls"] == [lib_clock.t + 5]
    r = c.post("/api/board/fpga-1/bitstream", {"name": "my_design", "file": upload_file()})
    assert r.status_code == 201 and uploads == ["my_design"]


def test_upload_allowed_without_index_copy(c, boards, lib_index, uploads):
    for down in (library.ArchiveError("archive unreachable"), library.NotPublished("VGA library not published")):
        lib_index["answer"] = down
        r = c.post("/api/board/fpga-1/bitstream", {"name": ROW1["id"], "file": upload_file()})
        assert r.status_code == 201
    assert uploads == [ROW1["id"], ROW1["id"]]


def test_upload_allowed_when_library_off(c, boards, lib_index, uploads, settings):
    settings.TTSITE_LIBRARY_BASE_URL = ""
    r = c.post("/api/board/fpga-1/bitstream", {"name": ROW1["id"], "file": upload_file()})
    assert r.status_code == 201 and lib_index["calls"] == []


def test_upload_check_fetches_a_cold_index(c, boards, settings, monkeypatch, lib_clock, uploads):
    """Finding r3: a worker that has never fetched the index fetches it, with a 5 s deadline, before deciding."""
    settings.TTSITE_LIBRARY_BASE_URL = LIB_BASE
    monkeypatch.setattr(library, "now", lib_clock)
    asked = []

    class Resp:
        status_code = 200
        headers = {"ETag": '"a1"'}
        url = LIB_BASE + "fpga-library.json"

        def iter_content(self, size):
            yield json.dumps(LIB_FIXTURE).encode()

        def close(self):
            pass

    monkeypatch.setattr(library.SESSION, "get", lambda url, **kw: asked.append((url, kw["timeout"])) or Resp())
    r = c.post("/api/board/fpga-1/bitstream", {"name": ROW0["id"], "file": upload_file()})
    assert r.status_code == 409 and uploads == []
    assert asked == [(LIB_BASE + "fpga-library.json", (library.CONNECT_TIMEOUT, 5.0))]
