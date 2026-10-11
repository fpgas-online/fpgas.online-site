"""A switch's front panel (#90): each port where it is on the switch, coloured by what it is doing now, marked where
it is not as the inventory says, linked to its row in the table.

The layout comes from the switch's model in the switch registry (netgear_switch: `port_count`, `poe_port_count`), not
from the reading: the Netgear switches here (GSM7252PS, S3300-52X) have their RJ45 ports in two rows, odd above even,
then an uplink block at the right whose ports carry no PoE (the ports after `poe_port_count`). A port is placed by its
number, so a port the reading lacks leaves its gap and moves no other port. A model with no PoE is all main block.

Each port's state is one word (STATES): what the switch says of its link and its PoE. A port shows its state as a
glyph as well as a colour, the key says each in words, and the port's title and accessible name say everything about
it; a port that is not as the inventory says has a mark as well as a colour."""

from dataclasses import dataclass

# (state, glyph, the key's words), in the key's order
STATES = (
    ("power", "P", "link up, PoE delivering"),
    ("starting", "*", "link down, PoE delivering (the device is powered, its link not up yet)"),
    ("up", "L", "link up, no PoE drawn"),
    ("searching", "s", "link down, PoE on and searching"),
    ("off", "o", "link down, PoE off"),
    ("down", "–", "link down, no PoE on the port"),
    ("fault", "F", "PoE fault"),
    ("unknown", "?", "PoE state not known (the switch gave none it names, or it was not read)"),
)
STATE_WORDS = {state: words for state, _, words in STATES}
GLYPHS = {state: glyph for state, glyph, _ in STATES}
PITCH, WIDTH, HEIGHT, ROW, GAP = 30, 26, 30, 36, 16  # a port's pitch, its box, a row's pitch, the uplink gap (px)


class LayoutError(ValueError):
    """The reading does not fit the switch's model: the panel would be drawn wrong, so it is not drawn."""


@dataclass
class Port:
    port: int
    x: int
    y: int
    state: str
    glyph: str
    mismatch: str  # what the inventory check says is wrong with this port, or ""
    known: bool  # every failure of this port has a known cause (an open issue): marked apart from a new fault
    title: str  # everything about the port, in words
    href: str


def state(view_port, has_poe):
    """The port's state word (STATES) from the switch's reading. `has_poe`: the model gives this port PoE, so a PoE
    state of None is a reading that is missing, not a port without PoE. A PoE state this does not name raises."""
    poe = view_port.poe_state
    if poe is None:
        if has_poe:
            return "unknown"
        return "up" if view_port.link_up else "down"
    if poe == "other":
        return "unknown"
    if poe == "fault":
        return "fault"
    if poe == "delivering":
        return "power" if view_port.link_up else "starting"
    if poe in ("searching", "disabled"):
        if view_port.link_up:
            return "up"
        return "searching" if poe == "searching" else "off"
    raise ValueError(f"port {view_port.port}: PoE state {poe!r} is not one the panel knows")


def _place(ports, port_count, poe_port_count):
    """{port: (x, y)} by port number: the main block (1..poe_port_count, or every port with no PoE), then the uplink
    block after a gap; odd above even in each. A port number outside the model's raises LayoutError."""
    main = poe_port_count or port_count
    places = {}
    for p in ports:
        n = p.port
        if not 1 <= n <= port_count:
            raise LayoutError(f"port {n} is not on this model (ports 1..{port_count})")
        if n <= main:
            x = ((n - 1) // 2) * PITCH
        else:
            x = ((main + 1) // 2) * PITCH + GAP + ((n - main - 1) // 2) * PITCH
        places[n] = (x, ((n - 1) % 2) * ROW)
    return places


def panel(view, rows, failures, anchor, port_count, poe_port_count, known=None):
    """[Port] of one reachable switch. `rows` are the table's rows ({port: row}, for the label and the words the table
    shows); `failures` the inventory check's ({port: [text]}); `known` the ports whose every failure has a known
    cause ({port: "the issue"}); `anchor` the row anchor's prefix ("switch-1"); `port_count` and `poe_port_count` the
    model's, from the switch registry."""
    known = known or {}
    places = _place(view.ports, port_count, poe_port_count)
    out = []
    for p in sorted(view.ports, key=lambda p: p.port):
        x, y = places[p.port]
        row = rows.get(p.port, {})
        word = state(p, p.port <= poe_port_count)
        wrong = "; ".join(failures.get(p.port, ()))
        parts = [f"port {p.port}", STATE_WORDS[word]]
        if word == "unknown":
            parts.append("link up" if p.link_up else "link down")
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
        out.append(Port(p.port, x, y, word, GLYPHS[word], wrong, bool(wrong) and p.port in known, " · ".join(parts),
                        f"#{anchor}-port-{p.port}"))
    return out


def size(ports):
    """(width, height) of the drawing."""
    if not ports:
        return 0, 0
    return max(p.x for p in ports) + WIDTH + 2, max(p.y for p in ports) + HEIGHT + 2


def summary(ports):
    """The panel's accessible name after the switch's title: how many ports are in each state, in the key's order."""
    counts = {}
    for p in ports:
        counts[p.state] = counts.get(p.state, 0) + 1
    return "; ".join(f"{counts[s]} {STATE_WORDS[s]}" for s, _, _ in STATES if s in counts)
