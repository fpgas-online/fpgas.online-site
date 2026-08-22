from pathlib import Path

STATIC = Path("ttsite/src/ttsite/static/ttsite")


def test_assets_present():
    assert (STATIC / "ttlogo_400.png").stat().st_size > 1000
    assert (STATIC / "ttlogo_400.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (STATIC / "README.md").exists()


def test_board_js_reads_data_attributes_and_mounts():
    js = (STATIC / "board.js").read_text()
    for attr in ["data-slug", "data-kind", "data-ws-path", "data-status-url", "data-pistat-groups", "data-commander-js"]:
        assert attr.replace("data-", "").replace("-", "") in js.replace("-", "").replace("_", "")  # dataset access
    assert "mountCommander" in js
    assert "/snmp/toggle" in js
    assert "ws/pistat/" in js
