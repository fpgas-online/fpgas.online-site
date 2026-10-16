"""The Pi table's check columns in groups, in dependency order (Tim, 15 Oct 2026, site #127), and the table's looks
that come with #127: Serial and ID in a fixed-width font, no sort arrow beside a header's word."""

import pathlib

from django.apps import apps

from management import check_groups


def test_jtag_comes_first_and_groups_keep_their_order():
    names = ["pcie-link", "ddr", "uart", "jtag", "flash", "wiring"]
    assert check_groups.grouped(names) == [("basic", ["jtag", "wiring"]), ("memory", ["flash", "ddr"]),
                                           ("peripherals", ["uart"]),
                                           ("board specific", ["pcie-link"])]


def test_a_check_no_group_names_is_shown_last_under_not_grouped_never_hidden():
    assert check_groups.grouped(["jtag", "new-thing", "ddr", "other-thing"]) == [
        ("basic", ["jtag"]), ("memory", ["ddr"]), (check_groups.NOT_GROUPED, ["new-thing", "other-thing"])]


def test_basic_holds_only_checks_more_than_one_board_type_gets_and_header_is_board_specific():
    # Tim, 16 Oct 2026: "Why is `header` in basic when it appears to be fomu only?" (td #361, td PR #363)
    assert check_groups.GROUP_OF["header"] == "board specific"
    for name in dict(check_groups.GROUPS)["basic"]:
        types = [t for t, (planned, _, _) in check_groups.PLANNED.items() if name in planned]
        assert len(types) > 1, (name, types)


def test_every_check_fpgas_verify_runs_at_welland_has_a_group():
    """The checks the fleet reported on 15 Oct 2026 (welland's /fleet/ columns): none may fall into "not grouped"."""
    seen = ["pcie-link", "pcie-bar0", "rp1-pio", "jtag", "flash", "ddr", "p2-uart", "p2-serial", "scratch", "p2-gpio",
            "uart", "spiflash", "ethernet", "pin-id", "header", "foboot", "foboot-after", "sdk", "dip-switches",
            "wiring"]
    assert all(check_groups.name(name) in check_groups.GROUP_OF for name in seen)


def _css():
    return (pathlib.Path(apps.get_app_config("management").path) / "static/management/management.css").read_text()


def test_serial_and_id_are_in_a_fixed_width_font():
    css = _css()
    rule = css.split(".mgmt-hosts td.mgmt-c-serial, .mgmt-hosts td.mgmt-c-id { font-family:", 1)[1].split("}", 1)[0]
    assert "monospace" in rule


def test_the_sort_arrow_sits_under_a_header_word_so_it_widens_no_column():
    assert ".mgmt-table th:not(.mgmt-checkhead) .mgmt-sort::after { display: block; }" in _css()


def test_every_board_type_gets_each_common_check_or_says_why_it_does_not_apply():
    """Tim, 15 Oct 2026 (site #129): "*Anything* which has an FPGA should be getting jtag, anything which has wires
    should be getting wiring, etc." Every type in the site's copy of verify's lists gets jtag, wiring, flash, ddr, uart
    and ethernet, or carries verify's reason why one does not apply. A TT board whose variant is not settled is judged
    on jtag, flash, ddr and ethernet only: whether it gets wiring and uart depends on the variant verify could not
    settle."""
    for title, (checks, not_applicable, unwritten) in check_groups.PLANNED.items():
        common = ("jtag", "flash", "ddr", "ethernet") if title == "TT board, not identified" else check_groups.COMMON
        for check in common:
            assert (check in checks) != (check in not_applicable), (title, check)
        assert set(unwritten) <= set(checks), title  # a test not written yet is still planned
        assert all(not_applicable.values()), title  # every "not applicable" has its reason
