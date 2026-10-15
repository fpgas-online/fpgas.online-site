"""A switch's front panel (#90, #130): each port where it is on the switch, drawn so that it needs no key (Tim, 15 Oct
2026: "The key near the switch port indicators needing to exist is a smell").

Every mark on a port means what the same mark means in the /fleet/ table and the port table under the drawing, and the
port shows the hardware on it, so the drawing reads like a labelled photo of the switch:

* the port's number, top left;
* the hardware the gateway last saw on it, in the /fleet/ short forms: the Pi's model on the first line ("Pi 5",
  "OPi PC"; "+4" after it where four more machines were last seen on the same port) and its FPGA board's type on the
  second ("Acorn", "TT FPGA"); an infrastructure port's role in its place ("gateway" / "trunk"); nothing on an empty
  port;
* the State, top right: the /fleet/ mark in its colour (● operational, ! needs attention, ○ missing, ⟳ checking), "?"
  for a device that has a link or draws power on a board port where no registered machine was seen, and "!" for a port
  whose network reading is not as it should be (the table's Net ✗); the box is filled in the State's colour, an
  empty port faint;
* its edge: solid while the link is up, dashed while it is down;
* ⚡ while PoE is delivering power, nothing otherwise.

Each port's title (and accessible name) says everything about it in words; each links its row in the table.

The layout comes from the switch's model in the switch registry (netgear_switch: `port_count`, `poe_port_count`), not
from the reading: the Netgear switches here (GSM7252PS, S3300-52X) have their RJ45 ports in two rows, odd above even,
then an uplink block at the right whose ports carry no PoE (the ports after `poe_port_count`). A port is placed by its
number, so a port the reading lacks leaves its gap and moves no other port. A model with no PoE is all main block."""

from dataclasses import dataclass

# a port's pitch, its box, a row's pitch, the gap before the uplink block (px): 26 columns of a 52-port switch and the
# gap fit in 1300 px
PITCH, WIDTH, HEIGHT, ROW, GAP = 46, 44, 46, 50, 16
TEXT_WIDTH = 40  # the room for a hardware line inside a box
CHAR_WIDTH = 5.4  # a 9 px sans-serif character, on average: a longer line is squeezed into TEXT_WIDTH
BOLT = "⚡"

# The box's state (its fill and its mark's colour) and that state's words, in the order the panel's name counts them.
# The first four are the /fleet/ conditions (management.states); a box with any of them shows that machine's mark.
KINDS = (
    ("attention", "needs attention"),
    ("missing", "missing"),
    ("unknown", "checking"),
    ("unregistered", "not registered"),
    ("operational", "operational"),
    ("infrastructure", "infrastructure"),
    ("empty", "empty"),
)
# an infrastructure role (views.switches.port_role) on the box's two lines
ROLE_LINES = {"gateway trunk": ("gateway", "trunk"), "switch trunk": ("switch", "trunk"),
              "house uplink": ("house", "uplink"), "infrastructure": ("infra-", "structure")}


class LayoutError(ValueError):
    """The reading does not fit the switch's model: the panel would be drawn wrong, so it is not drawn."""


@dataclass
class Port:
    port: int
    x: int
    y: int
    kind: str  # KINDS: the box's fill and its mark's colour
    mark: str  # the State mark, "?" or "!"; "" for an empty or infrastructure port that is as it should be
    link_up: bool  # a solid edge; a dashed one when down
    bolt: str  # BOLT while PoE delivers, else ""
    lines: list  # the hardware (or role) on the box's two lines: [{"text", "fit": TEXT_WIDTH where squeezed, else 0}]
    title: str  # everything about the port, in words
    href: str


def squeeze(text):
    """TEXT_WIDTH when `text` is wider than the box has room for (the line is drawn squeezed to fit), else 0."""
    return TEXT_WIDTH if len(text) * CHAR_WIDTH > TEXT_WIDTH else 0


def _place(ports, port_count, poe_port_count):
    """{port: (x, y)} by port number: the main block (1..poe_port_count, or every port with no PoE), then the uplink
    block after a gap; odd above even in each. A port number outside the model's raises LayoutError."""
    main = poe_port_count or port_count
    places = {}
    for n in ports:
        if not 1 <= n <= port_count:
            raise LayoutError(f"port {n} is not on this model (ports 1..{port_count})")
        if n <= main:
            x = ((n - 1) // 2) * PITCH
        else:
            x = ((main + 1) // 2) * PITCH + GAP + ((n - main - 1) // 2) * PITCH
        places[n] = (x, ((n - 1) % 2) * ROW)
    return places


def box(row):
    """(kind, mark, lines) of a port from its table row (views.switches._row): the newest machine the gateway saw on
    it, else its role, else whether something is there unregistered; a port whose network reading is not as it should
    be is "!" whatever is on it."""
    machines = row["machines"]
    if machines:
        newest = machines[0]
        more = f" +{len(machines) - 1}" if len(machines) > 1 else ""
        kind, mark, lines = newest["condition"], newest["mark"], [newest["model"] + more, newest["board"]]
    elif row["role"]:
        kind, mark, lines = "infrastructure", "", list(ROLE_LINES.get(row["role"], (row["role"], "")))
    elif row["unregistered"]:
        kind, mark, lines = "unregistered", row["unregistered"], ["", ""]
    else:
        kind, mark, lines = "empty", "", ["", ""]
    if not row["net_ok"]:
        kind, mark = "attention", "!"
    return kind, mark, lines


def title(row):
    """Everything about a port in words, for its box's title and accessible name."""
    parts = [f"port {row['port']}"]
    for m in row["machines"]:
        parts.append(f"{m['label']}, {m['board']}, {m['state_title']}, leased an address here {m['lease_at']}")
    if row["role"]:
        parts.append(row["role"])
    if row["unregistered"]:
        parts.append(row["unregistered_title"])
    parts.append(row["link_title"] + (f" {row['speed']}" if row["speed"] else ""))
    parts.append(row["poe_title"] + (f" {row['watts']} W" if row["watts"] else ""))
    if row["vlan"] and row["vlan"] != "–":
        parts.append(f"VLAN {row['vlan']}")
    if row["lldp_name"]:
        parts.append(f"sees {row['lldp_name']}")
    parts.append(row["net_title"])
    return " · ".join(parts)


def panel(rows, anchor, port_count, poe_port_count):
    """[Port] of one reachable switch from its table rows (views.switches._row); `anchor` the row anchor's prefix
    ("switch-1"); `port_count` and `poe_port_count` the model's, from the switch registry."""
    places = _place([r["port"] for r in rows], port_count, poe_port_count)
    out = []
    for row in sorted(rows, key=lambda r: r["port"]):
        x, y = places[row["port"]]
        kind, mark, lines = box(row)
        out.append(Port(row["port"], x, y, kind, mark, row["link_up"], BOLT if row["poe_state"] == "delivering" else "",
                        [{"text": line, "fit": squeeze(line)} for line in lines], title(row),
                        f"#{anchor}-port-{row['port']}"))
    return out


def size(ports):
    """(width, height) of the drawing."""
    if not ports:
        return 0, 0
    return max(p.x for p in ports) + WIDTH + 2, max(p.y for p in ports) + HEIGHT + 2


def summary(ports):
    """The panel's accessible name after the switch's title: how many ports are in each state (KINDS' order), and
    how many deliver power or have a link."""
    counts = {}
    for p in ports:
        counts[p.kind] = counts.get(p.kind, 0) + 1
    parts = [f"{counts[k]} {words}" for k, words in KINDS if k in counts]
    parts.append(f"{sum(p.link_up for p in ports)} with a link up")
    parts.append(f"{sum(bool(p.bolt) for p in ports)} delivering PoE")
    return "; ".join(parts)
