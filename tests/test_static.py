from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "ttsite" / "src" / "ttsite" / "static" / "ttsite"

# every dataset key board.js is expected to read off #ttsite-board
DATASET_KEYS = ["statusUrl", "pistatGroups", "commanderJs", "wsPath", "apiBase", "slug", "kind", "shuttle", "port",
                "switch"]


def test_assets_present():
    assert (STATIC / "ttlogo_400.png").stat().st_size > 1000
    assert (STATIC / "ttlogo_400.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (STATIC / "README.md").exists()


def test_board_js_reads_data_attributes_and_mounts():
    js = (STATIC / "board.js").read_text()
    missing = [key for key in DATASET_KEYS if f"d.{key}" not in js]
    assert missing == [], f"board.js never reads {missing}"
    assert "mountCommander" in js
    assert "/snmp/toggle" in js
    assert "ws/pistat/" in js


def test_board_js_sends_the_port_as_a_string():
    """snmp_switch builds "<oid>.<port>", so a number would break the toggle."""
    js = (STATIC / "board.js").read_text()
    assert "port: d.port" in js
    assert "Number(d.port)" not in js


def test_board_js_names_the_switch_and_prints_why_a_power_cycle_was_refused():
    """The site has more than one switch, so the port alone is a 400; and a
    refusal (403 not a board, 429 too soon) says why as JSON {"error": ...}."""
    js = (STATIC / "board.js").read_text()
    assert "switch: Number(d.switch)" in js
    assert "(await r.json()).error" in js
    assert "power-cycle refused (HTTP ${r.status})" in js


def test_board_js_has_gallery_and_upload_behaviour():
    js = (STATIC / "board.js").read_text()
    assert "refreshDesigns" in js
    assert "/designs" in js
    assert "X-CSRFToken" in js


def test_ttsite_css_styles_the_gallery():
    css = (STATIC / "ttsite.css").read_text()
    assert ".tt-gallery" in css


def test_board_js_wires_library_and_csrf():
    """The VGA library panel's fetches live in library.js's controller, wired up by board.js; the Commander gets the
    page's CSRF reader (Commander PR #12)."""
    board = (STATIC / "board.js").read_text()
    lib = (STATIC / "library.js").read_text()
    assert "/library/" in lib and "/load" in lib
    assert "'/library'" in lib
    assert "X-CSRFToken" in lib
    assert "loading = 'lazy'" in lib
    assert "innerHTML" not in lib
    assert "csrfToken: csrf" in board
    assert "ttsiteLibrary" in board and "createPanel" in board
    # the cookie reader is defined before the Commander is mounted, so mountCommander can be handed it
    assert board.index("const csrf = ") < board.index("mountCommander(")
    assert "'library'" in board  # the gallery's badge rule knows the library source


def test_library_js_is_commonjs_and_a_browser_global():
    lib = (STATIC / "library.js").read_text()
    assert "module.exports" in lib
    assert "window.ttsiteLibrary" in lib


def test_ttsite_css_styles_the_library():
    css = (STATIC / "ttsite.css").read_text()
    for cls in (".tt-library", ".tt-library-grid", ".tt-badge--library", ".tt-badge--warn", ".tt-badge--info"):
        assert cls in css, cls


def test_board_js_releases_only_the_buttons_it_disabled_and_links_https_only():
    """A failed enable, upload or library load must not re-enable the gallery's disabled "Running" button: every
    snapshot takes only the buttons enabled at that moment (library.js's holdButtons is tested in node)."""
    js = (STATIC / "board.js").read_text()
    assert ".filter((btn) => !btn.disabled)" in js
    assert "const holdButtons = (nodes)" in js
    assert js.count("holdButtons(") == 3  # the library's setBusy, enable() and the upload
    assert "btn.disabled = false; });\n" not in js.replace("return () => held.forEach((btn) => { btn.disabled = false; });", "")
    assert "/^https:\\/\\//i" in js
    assert "https?:" not in js
    assert "https?:" not in (STATIC / "library.js").read_text()
