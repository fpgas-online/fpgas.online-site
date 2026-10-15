"""The Pi table's check columns in groups, in the order one check depends on another (Tim, 15 Oct 2026, site #127:
"The checks columns should be sorted by complexity / requirement. They should probably be grouped by type - something
like (basic / common, memory, peripherals, board specific, etc). IE If the jtag isn't working, then most of the other
tests can't even run.").

fpgas-verify's report names each test but not its group, so the groups are kept here until it does
(fpgas-online/fpgas.online-test-designs#339). A check name not listed is shown, loud, under NOT_GROUPED at the end, in
the order the checks gave it, so a new check is never hidden."""

# (group, its checks in the order they depend on each other: jtag first, as nothing loads without it)
GROUPS = (
    ("basic", ("jtag", "header", "wiring")),
    ("memory", ("flash", "spiflash", "ddr")),
    ("peripherals", ("uart", "p2-uart", "p2-serial", "p2-gpio", "ethernet")),
    ("board specific", ("pcie-link", "pcie-bar0", "rp1-pio", "scratch", "pin-id", "foboot", "foboot-after", "sdk",
                        "dip-switches")),
)
NOT_GROUPED = "not grouped"
GROUP_OF = {name: group for group, names in GROUPS for name in names}
_RANK = {name: i for i, name in enumerate(name for _, names in GROUPS for name in names)}


def grouped(names):
    """[(group, [check, ...])] for the check names given (in the order the checks gave them): each known one in its
    group, in the groups' order and their dependency order; the unknown ones last, under NOT_GROUPED, as given.
    Groups with no check here are left out."""
    known = sorted((n for n in names if n in GROUP_OF), key=_RANK.__getitem__)
    out = [(group, [n for n in known if GROUP_OF[n] == group]) for group, _ in GROUPS]
    out = [(group, members) for group, members in out if members]
    unknown = [n for n in names if n not in GROUP_OF]
    return out + ([(NOT_GROUPED, unknown)] if unknown else [])
