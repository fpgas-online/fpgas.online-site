"""The Pi table's check columns in groups, in dependency order (Tim, 15 Oct 2026, site #127), and the table's looks
that come with #127: Serial and ID in a fixed-width font, no sort arrow beside a header's word."""

import pathlib

from django.apps import apps

from management import check_groups


def test_jtag_comes_first_and_groups_keep_their_order():
    names = ["pcie-link", "ddr", "uart", "jtag", "flash", "pin-id"]
    assert check_groups.grouped(names) == [("basic", ["jtag"]), ("memory", ["flash", "ddr"]), ("peripherals", ["uart"]),
                                           ("board specific", ["pcie-link", "pin-id"])]


def test_a_check_no_group_names_is_shown_last_under_not_grouped_never_hidden():
    assert check_groups.grouped(["jtag", "new-thing", "ddr", "other-thing"]) == [
        ("basic", ["jtag"]), ("memory", ["ddr"]), (check_groups.NOT_GROUPED, ["new-thing", "other-thing"])]


def test_every_check_fpgas_verify_runs_at_welland_has_a_group():
    """The checks the fleet reported on 15 Oct 2026 (welland's /fleet/ columns): none may fall into "not grouped"."""
    seen = ["pcie-link", "pcie-bar0", "rp1-pio", "jtag", "flash", "ddr", "p2-uart", "p2-serial", "scratch", "p2-gpio",
            "uart", "spiflash", "ethernet", "pin-id", "header", "foboot", "foboot-after", "sdk", "dip-switches",
            "wiring"]
    assert all(name in check_groups.GROUP_OF for name in seen)


def _css():
    return (pathlib.Path(apps.get_app_config("management").path) / "static/management/management.css").read_text()


def test_serial_and_id_are_in_a_fixed_width_font():
    css = _css()
    rule = css.split(".mgmt-hosts td.mgmt-c-serial, .mgmt-hosts td.mgmt-c-id { font-family:", 1)[1].split("}", 1)[0]
    assert "monospace" in rule


def test_the_sort_arrow_sits_under_a_header_word_so_it_widens_no_column():
    assert ".mgmt-table th:not(.mgmt-checkhead) .mgmt-sort::after { display: block; }" in _css()
