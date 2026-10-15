"""A switch's front panel (#90, #130): each port where it is on the switch, drawn so that it needs no key (Tim, 15 Oct
2026: "The key near the switch port indicators needing to exist is a smell").

Every mark on a port means what the same mark means in the port table under the drawing (and on /fleet/), and the
port shows the hardware on it, so the drawing reads like a labelled photo of the switch:

* top left, the port's number; top right, the State column's mark in its colour: the /fleet/ State of the newest
  machine the gateway saw on the port (● operational, ! needs attention, ○ missing, ⟳ checking), or "?" for a device
  with a link or drawing power on a board port where no registered machine was seen; the box is filled in that
  State's colour, an empty port faint;
* in the middle, the hardware the gateway last saw on it, in the /fleet/ short forms: the Pi's model ("Pi 5", "OPi
  PC") and its FPGA board's type ("Acorn"; "TT" over "FPGA" where it is too long for one line); an infrastructure
  port's role as one short word ("gateway", "trunk", "uplink", "infra"); nothing on an empty port;
* at the bottom: ⚡ while PoE is delivering power; "+3" where three more machines were last seen on the port; and the
  Net column's ✗, in red, where the port's network reading is not as it should be (the review of #137: the State
  mark is always the machine's own, and the Net verdict is its own mark, as the table's two columns are);
* its link light, a small rectangle at the top as on the switch's own face: lit green while the link is up, dark
  while it is down (Tim, 16 Oct 2026, site #139: "Why are you using the switch border style to indicate if a switch
  port has link?"); the box's edge is the same for every port.

Each port's title (and accessible name) says everything about it in words; each links its row in the table.

The layout comes from the switch's model in the switch registry (netgear_switch: `port_count`, `poe_port_count`), not
from the reading: the Netgear switches here (GSM7252PS, S3300-52X) have their RJ45 ports in two rows, odd above even,
then an uplink block at the right whose ports carry no PoE (the ports after `poe_port_count`). A port is placed by its
number, so a port the reading lacks leaves its gap and moves no other port. A model with no PoE is all main block.
Each port also has a place in a narrow drawing for a phone: the same two rows, cut into bands of NARROW_COLUMNS
columns, one under the other, the uplink block a band of its own."""

import re
from dataclasses import dataclass

# a port's pitch, its box, a row's pitch, the gap before the uplink block, the gap between two bands of the narrow
# drawing (px): 26 columns of a 52-port switch and the gap fit in about 1350 px
PITCH, WIDTH, HEIGHT, ROW, GAP, BAND_GAP = 52, 50, 70, 74, 12, 12
NARROW_COLUMNS = 6  # columns a band of the narrow drawing has: 6 boxes fit a phone's 390 px at full size
TEXT_WIDTH = 48  # the room for a hardware line inside a box
# an 11 px sans-serif character, on average, at most: measured in Chrome, "gateway" is 47 px, "TT FPGA" 46, "PCIe
# card" 52 (so it wraps); a longer line is wrapped at a space, never squeezed
CHAR_WIDTH = 6.8
LINES = 2  # hardware lines under the model
BOLT = "⚡"
NET_FAILED = "✗"

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
# an infrastructure role (views.switches.port_role) as one short word for the box; the role in full is its title's
ROLE_WORDS = {"gateway trunk": "gateway", "switch trunk": "trunk", "house uplink": "uplink",
              "infrastructure": "infra"}


class LayoutError(ValueError):
    """The reading does not fit the switch's model: the panel would be drawn wrong, so it is not drawn."""


@dataclass
class Port:
    port: int
    x: int  # in the wide drawing
    y: int
    nx: int  # in the narrow drawing
    ny: int
    kind: str  # KINDS: the box's fill and its State mark's colour
    mark: str  # the State column's mark: the newest machine's /fleet/ mark, or "?"; "" for an empty or role port
    net_ok: bool  # False: the Net column's ✗ in the box's corner
    link_up: bool  # the link light lit green; dark when down
    bolt: str  # BOLT while PoE delivers, else ""
    more: str  # "+3" where three more machines were last seen on the port, else ""
    lines: list  # the hardware (or role) on the box's three lines: the model, then the board's type over two
    title: str  # everything about the port, in words
    href: str

    @property
    def net(self):
        return "" if self.net_ok else NET_FAILED


def fits(text):
    """Whether `text` fits a box's line at 11 px."""
    return len(text) * CHAR_WIDTH <= TEXT_WIDTH


def wrap(text, lines=LINES):
    """`text` on at most `lines` lines that each fit a box, broken at spaces; a word that does not fit on a line of its
    own, or more words than the lines hold, end in "…" (the whole is in the box's title). Never squeezed."""
    out, words = [], text.split()
    while words and len(out) < lines:
        line = words.pop(0)
        while words and fits(f"{line} {words[0]}"):
            line += " " + words.pop(0)
        out.append(line)
    if words or any(not fits(line) for line in out):
        last = out[-1]
        while last and not fits(last + "…"):
            last = last[:-1]
        out[-1] = last.rstrip() + "…"
    return out + [""] * (lines - len(out))


def wrap_name(name, lines):
    """A host name on at most `lines` lines that each fit a box, broken after its hyphens ("rpi5-" / "new-" /
    "13f59c"); a part longer than a line, or more parts than the lines hold, end in "…" as wrap() does."""
    parts = re.findall(r"[^-]+-?|-", name)
    out = []
    while parts and len(out) < lines:
        line = parts.pop(0)
        while parts and fits(line + parts[0]):
            line += parts.pop(0)
        out.append(line)
    if parts or any(not fits(line) for line in out):
        last = out[-1]
        while last and not fits(last + "…"):
            last = last[:-1]
        out[-1] = last + "…"
    return out + [""] * (lines - len(out))


def _place(ports, port_count, poe_port_count, band=None):
    """{port: (x, y)} by port number: the main block (1..poe_port_count, or every port with no PoE), then the uplink
    block after a gap; odd above even in each. With `band`, the narrow drawing: each block cut into bands of `band`
    columns, one under the other. A port number outside the model's raises LayoutError."""
    main = poe_port_count or port_count
    main_columns = (main + 1) // 2
    main_bands = -(-main_columns // band) if band else 1
    places = {}
    for n in ports:
        if not 1 <= n <= port_count:
            raise LayoutError(f"port {n} is not on this model (ports 1..{port_count})")
        row = (n - 1) % 2
        if band is None:
            if n <= main:
                x = ((n - 1) // 2) * PITCH
            else:
                x = main_columns * PITCH + GAP + ((n - main - 1) // 2) * PITCH
            places[n] = (x, row * ROW)
            continue
        column = (n - 1) // 2 if n <= main else (n - main - 1) // 2
        stripe = column // band + (0 if n <= main else main_bands)
        places[n] = ((column % band) * PITCH, stripe * (2 * ROW + BAND_GAP) + row * ROW)
    return places


def box(row):
    """(kind, mark, more, lines) of a port from its table row (views.switches._row): the newest machine the gateway saw
    on it, else its role, else whether something is there unregistered. The mark is the table's State mark, always;
    the Net verdict is a mark of its own (Port.net)."""
    machines = row["machines"]
    if machines:
        newest = machines[0]
        more = f"+{len(machines) - 1}" if len(machines) > 1 else ""
        return newest["condition"], newest["mark"], more, wrap(newest["model"], 1) + wrap(newest["board"])
    if row["role"]:
        word = ROLE_WORDS.get(row["role"], row["role"])
        return "infrastructure", "", "", wrap(word, 1) + [""] * LINES
    if row["unregistered"]:  # what it announces over LLDP, where it does: the device a reader wants named (#148)
        return "unregistered", row["unregistered"], "", wrap_name(row["lldp_name"] or "", 1 + LINES)
    return "empty", "", "", [""] * (1 + LINES)


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
    if row["lldp_name"] or row.get("lldp_title"):  # what LLDP says, as the row's LLDP cell and its title say it (#148)
        parts.append("LLDP: " + "; ".join(t for t in (row["lldp_name"], row.get("lldp_title", "")) if t))
    parts.append(row["net_title"])
    return " · ".join(parts)


def panel(rows, anchor, port_count, poe_port_count):
    """[Port] of one reachable switch from its table rows (views.switches._row); `anchor` the row anchor's prefix
    ("switch-1"); `port_count` and `poe_port_count` the model's, from the switch registry."""
    numbers = [r["port"] for r in rows]
    wide = _place(numbers, port_count, poe_port_count)
    narrow = _place(numbers, port_count, poe_port_count, NARROW_COLUMNS)
    out = []
    for row in sorted(rows, key=lambda r: r["port"]):
        kind, mark, more, lines = box(row)
        (x, y), (nx, ny) = wide[row["port"]], narrow[row["port"]]
        out.append(Port(row["port"], x, y, nx, ny, kind, mark, row["net_ok"], row["link_up"],
                        BOLT if row["poe_state"] == "delivering" else "", more, lines, title(row),
                        f"#{anchor}-port-{row['port']}"))
    return out


def size(ports, narrow=False):
    """(width, height) of the drawing, wide or narrow."""
    if not ports:
        return 0, 0
    xs = [p.nx if narrow else p.x for p in ports]
    ys = [p.ny if narrow else p.y for p in ports]
    return max(xs) + WIDTH + 2, max(ys) + HEIGHT + 2


def summary(ports):
    """The panel's accessible name after the switch's title: how many ports are in each state (KINDS' order), how
    many are not as they should be, and how many deliver power or have a link."""
    counts = {}
    for p in ports:
        counts[p.kind] = counts.get(p.kind, 0) + 1
    parts = [f"{counts[k]} {words}" for k, words in KINDS if k in counts]
    parts.append(f"{sum(not p.net_ok for p in ports)} not as it should be")
    parts.append(f"{sum(p.link_up for p in ports)} with a link up")
    parts.append(f"{sum(bool(p.bolt) for p in ports)} delivering PoE")
    return "; ".join(parts)
