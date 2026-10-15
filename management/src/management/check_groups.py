"""Which checks each board type gets, and the Pi table's check columns in groups, in the order one check depends on
another (Tim, 15 Oct 2026: site #127 "sorted by complexity / requirement ... grouped by type", and site #129
"*Anything* which has an FPGA should be getting jtag, anything which has wires should be getting wiring, etc.").

fpgas-verify is the owner of all of this (fpgas.online-test-designs #339, #340): its report will carry each board's
planned checks (`board<i>_planned`), their groups (`board<i>_groups`) and why a common check does not apply
(`board<i>_not_applicable_<check>`). Until a Pi runs that verify, the site keeps the same lists here, as verify's
branch verify/checks-every-board (bdf3a62) states them (fo-tools, 15 Oct 2026, scratch data/tools/2026-10-15-verify-
checks/plans.txt). A board's own report wins over these lists wherever it says something."""

# (group, its checks in the order they depend on each other: jtag first, as nothing loads without it). The groups
# are verify's ids (basic, memory, peripherals, board-specific), shown with a space.
GROUPS = (
    ("basic", ("jtag", "header", "wiring")),
    ("memory", ("flash", "ddr", "spram")),
    ("peripherals", ("uart", "p2-uart", "p2-serial", "p2-gpio", "ethernet")),
    ("board specific", ("pcie-link", "pcie-bar0", "power-cycle", "rp1-pio", "scratch", "foboot", "foboot-after", "sdk",
                        "dip-switches")),
)
NOT_GROUPED = "not grouped"
GROUP_OF = {name: group for group, names in GROUPS for name in names}
GROUP_IDS = {"basic": "basic", "memory": "memory", "peripherals": "peripherals", "board-specific": "board specific"}
_RANK = {name: i for i, name in enumerate(name for _, names in GROUPS for name in names)}

# The old names of two checks, as reports older than verify's rename still send them (fo-tools, 15 Oct 2026): the Pmod
# cabling test is "wiring" on every board, the configuration flash "flash". One column a check.
ALIASES = {"pin-id": "wiring", "spiflash": "flash"}

# Board type (fpga.board_title) -> (the checks it gets, in run order; {check: why it does not apply to this type};
# {check: the issue of a planned check that has no boot-check test yet}).
_ACORN = (("pcie-link", "pcie-bar0", "rp1-pio", "jtag", "flash", "ddr", "p2-uart", "p2-serial", "scratch", "p2-gpio"),
          {"wiring": "its two cables are each tested by name: jtag (P1), and p2-uart, p2-serial and p2-gpio (P2)",
           "uart": "its UART is on the P2 cable: p2-uart tests it",
           "ethernet": "an Acorn is an M.2 PCIe card with no Ethernet"}, {})
PLANNED = {
    "Acorn": _ACORN,
    "PCIe card": _ACORN,  # fpgas-verify checks any Acorn-module board with the Acorn's checks
    "Arty A7": (("jtag", "uart", "ddr", "flash", "ethernet", "wiring"), {}, {}),
    "NeTV2": (("jtag", "uart", "ddr", "flash", "ethernet"),
              {"wiring": "its only wires to the Pi are the header's JTAG and UART lines: jtag and uart test them"},
              {"ethernet": "fpgas.online-test-designs#341"}),
    "Fomu": (("header", "foboot", "uart", "flash", "foboot-after"),
             {"jtag": "the iCE40UP5K has no JTAG port: the Fomu is loaded over USB DFU, by foboot",
              "wiring": "it sits on the Pi's header, whose reset, CDONE and SPI lines the header test checks",
              "ddr": "the Fomu EVT has no DRAM", "ethernet": "the Fomu EVT has no Ethernet"},
             {"flash": "fpgas.online-test-designs#342"}),
    "TT FPGA": (("sdk", "dip-switches", "wiring", "uart", "spram"),
                {"ddr": "the demo board has no DRAM", "ethernet": "the demo board has no Ethernet",
                 "jtag": "the iCE40UP5K has no JTAG port: it is loaded through the demo board's RP2350",
                 "flash": "the FPGA breakout has no configuration flash: the RP2350 streams each design from the Pi"},
                {"spram": "fpgas.online-test-designs#343"}),
    "TT chip": (("sdk", "wiring"),
                {"jtag": "a Tiny Tapeout chip is an ASIC, not an FPGA: nothing on it is configured over JTAG",
                 "flash": "a Tiny Tapeout chip is an ASIC: it has no configuration flash (the RP2040's own flash "
                          "holds only its firmware)",
                 "uart": "the chip has no UART to the Pi: the Pi reaches the demo board's RP2040 over USB",
                 "ddr": "the demo board has no DRAM", "ethernet": "the demo board has no Ethernet"}, {}),
    "TT board, not identified": (("sdk",),
                                 {"jtag": "neither a TT FPGA breakout (an iCE40UP5K, loaded through the RP2350) "
                                          "nor a Tiny Tapeout chip has a JTAG port",
                                  "flash": "neither a TT FPGA breakout nor a Tiny Tapeout chip has a configuration "
                                           "flash",
                                  "ddr": "the demo board has no DRAM", "ethernet": "the demo board has no Ethernet"},
                                 {}),
}


# The checks every board is judged on (Tim, 15 Oct 2026: "*Anything* which has an FPGA should be getting jtag,
# anything which has wires should be getting wiring, etc."): each type gets each of them or says why it does not apply.
COMMON = ("jtag", "wiring", "flash", "ddr", "uart", "ethernet")


def types_running(check):
    """The board types that get a check, in PLANNED's order: "Acorn, PCIe card, Arty A7"."""
    return [title for title, (checks, _, _) in PLANNED.items() if check in checks]


def name(check):
    """A check's name in the columns: its new name where a report sends an old one."""
    return ALIASES.get(check, check)


def planned(title):
    """(checks, {check: why not applicable}, {check: issue of a test not written yet}) of a board type, or None for a
    type the site does not know (its board's own report is then all there is)."""
    return PLANNED.get(title)


def grouped(names):
    """[(group, [check, ...])] for the check names given (in the order the checks gave them): each known one in its
    group, in the groups' order and their dependency order; the unknown ones last, under NOT_GROUPED, as given.
    Groups with no check here are left out."""
    known = sorted((n for n in names if n in GROUP_OF), key=_RANK.__getitem__)
    out = [(group, [n for n in known if GROUP_OF[n] == group]) for group, _ in GROUPS]
    out = [(group, members) for group, members in out if members]
    unknown = [n for n in names if n not in GROUP_OF]
    return out + ([(NOT_GROUPED, unknown)] if unknown else [])
