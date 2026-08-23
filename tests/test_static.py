from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "ttsite" / "src" / "ttsite" / "static" / "ttsite"

# every dataset key board.js is expected to read off #ttsite-board
DATASET_KEYS = ["statusUrl", "pistatGroups", "commanderJs", "wsPath", "apiBase", "slug", "kind", "shuttle", "port"]


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


def test_board_js_has_gallery_and_upload_behaviour():
    js = (STATIC / "board.js").read_text()
    assert "refreshDesigns" in js
    assert "/designs" in js
    assert "X-CSRFToken" in js


def test_ttsite_css_styles_the_gallery():
    css = (STATIC / "ttsite.css").read_text()
    assert ".tt-gallery" in css
