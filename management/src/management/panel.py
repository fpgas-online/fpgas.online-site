"""A switch's front panel (#90): each port where it is on the switch, coloured by what it is doing now, marked where
it is not as the inventory says, linked to its row in the table.

The layout comes from the reading, not a drawing per model: the Netgear switches here (GSM7252PS, S3300-52X) have
their RJ45 ports in two rows, odd above even, then an uplink block at the right whose ports carry no PoE. So the
ports after the last one with PoE are the uplink block (two rows the same way), and the rest are the main block. A
switch with no PoE at all is all main block.

Each port's state is one word (STATES): what the switch says of its link and its PoE. The panel's key says each in
words; the colour is never the only signal (the port's title says it all, and a port that is not as the inventory
says has a mark as well as a colour)."""

from dataclasses import dataclass

# (state, the key's words), in the key's order
STATES = (
    ("power", "link up, PoE delivering"),
    ("up", "link up, no PoE drawn"),
    ("searching", "link down, PoE on and searching"),
    ("off", "link down, PoE off"),
    ("down", "link down, no PoE on the port"),
    ("fault", "PoE fault"),
)
STATE_WORDS = dict(STATES)
PITCH, WIDTH, HEIGHT, GAP = 30, 26, 22, 16  # a port's pitch, its box, the gap before the uplink block (px)


@dataclass
class Port:
    port: int
    x: int
    y: int
    state: str
    mismatch: str  # what the inventory check says is wrong with this port, or ""
    known: bool  # every failure of this port has a known cause (an open issue): marked apart from a new fault
    title: str  # everything about the port, in words
    href: str


def state(view_port):
    """The port's state word (STATES) from the switch's reading."""
    poe = view_port.poe_state or ""
    if poe == "fault":
        return "fault"
    if view_port.link_up:
        return "power" if poe == "delivering" else "up"
    if poe == "searching":
        return "searching"
    if poe == "disabled":
        return "off"
    return "down"


def _place(ports):
    """{port: (x, y)}: the main block, then the uplink block (the ports after the last one with PoE)."""
    numbers = sorted(p.port for p in ports)
    with_poe = [p.port for p in ports if p.poe_state]
    last_poe = max(with_poe) if with_poe else max(numbers, default=0)
    main = [n for n in numbers if n <= last_poe]
    uplink = [n for n in numbers if n > last_poe]
    places = {}
    for i, n in enumerate(main):
        places[n] = ((i // 2) * PITCH, (i % 2) * (HEIGHT + 6))
    start = ((len(main) + 1) // 2) * PITCH + GAP
    for i, n in enumerate(uplink):
        places[n] = (start + (i // 2) * PITCH, (i % 2) * (HEIGHT + 6))
    return places


def panel(view, rows, failures, anchor, known=None):
    """[Port] of one reachable switch. `rows` are the table's rows ({port: row}, for the label and the words the table
    shows); `failures` the inventory check's ({port: [text]}); `known` the ports whose every failure has a known
    cause ({port: "the issue"}); `anchor` the row anchor's prefix ("switch-1")."""
    known = known or {}
    places = _place(view.ports)
    out = []
    for p in sorted(view.ports, key=lambda p: p.port):
        x, y = places[p.port]
        row = rows.get(p.port, {})
        word = state(p)
        wrong = "; ".join(failures.get(p.port, ()))
        parts = [f"port {p.port}", STATE_WORDS[word]]
        if row.get("label") and row.get("label") != "–":
            parts.append(row["label"])
        if row.get("label_title"):
            parts.append(row["label_title"])
        if row.get("vlan"):
            parts.append(f"VLAN {row['vlan']}")
        if row.get("lldp_name"):
            parts.append(f"sees {row['lldp_name']}")
        if wrong:
            parts.append(f"not as the inventory says: {wrong}")
            if p.port in known:
                parts.append(f"known: {known[p.port]}")
        out.append(Port(p.port, x, y, word, wrong, bool(wrong) and p.port in known, " · ".join(parts),
                        f"#{anchor}-port-{p.port}"))
    return out


def size(ports):
    """(width, height) of the drawing."""
    if not ports:
        return 0, 0
    return max(p.x for p in ports) + WIDTH + 2, max(p.y for p in ports) + HEIGHT + 2
