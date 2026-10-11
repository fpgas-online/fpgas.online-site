"""The FPGA dashboard's data (#68): every registered machine with what its boot check reported, and a summary
by board type. Everything comes from the fleet registry (each Pi's registration and boot events); nothing here
is a remembered table of which board is on which port."""

from dataclasses import dataclass, field

from django.db.models import F
from django.utils import timezone
from fleet.models import BootEvent, Machine
from fleet.services import _BOARD_KEY, CONDITIONS, FPGA_STAGES, MAX_BOARDS, _reported_boards, checked_in, condition
from pibfpgas.pis import Pi
from rpi_hwid import names
from rpi_hwid.revision import decode_revision

# The board names fpgas-verify's modules use (board<i>), as a visitor reads them.
TYPE_TITLES = {"acorn": "Acorn", "arty": "Arty A7", "netv2": "NeTV2", "fomu": "Fomu", "pcileech": "PCIe card"}
OTHER = "other"  # a board name this page does not know: one bucket, so a Pi cannot make a row per name it sends
SNAPSHOT_TT = "TT, not checked this boot"
# What a registration's hardware document calls a board kind, for a machine whose check reported none this boot.
SNAPSHOT_KINDS = {"tt-demo-board": SNAPSHOT_TT, "arty-a7": "Arty A7", "xilinx-pcie": "PCIe card",
                  "netv2": "NeTV2", "fomu": "Fomu", "acorn": "Acorn"}
NO_BOARD = "no board"
TT_UNIDENTIFIED = "TT board, not identified"  # the check could not tell which: the Boot check cell says why
# rpi-hwid's names for a board by its Device DNA (rpi_hwid.names): the name on its label. An Arty's name depends on
# the order the registry took the serials, so it is not made here; it is shown by its serial.
DNA_NAMES = {"acorn": names.acorn_name, "netv2": names.netv2_name, "pcileech": names.pcileech_name}
# "unknown" is the condition of a Pi whose boot check has not finished this boot (not started, or running): the
# page calls it what it is, "checking" (the coordinator, 11 Oct 2026: no state reads "unknown").
CONDITION_TITLES = {"operational": "operational", "attention": "needs attention", "missing": "missing",
                    "unknown": "checking"}
MAX_TEXT = 300


def _text(value, limit=MAX_TEXT):
    return value[:limit] if isinstance(value, str) else ""


def board_title(board):
    """A reported board's type, for the summary: a Tiny Tapeout board by what it said it carries."""
    if board["board"] == "tt":
        chip = _text(board["identity"].get("chip", ""), 20).lower()
        return {"fpga": "TT FPGA", "asic": "TT chip"}.get(chip, TT_UNIDENTIFIED)
    if not board["board"]:
        return "not readable"
    return TYPE_TITLES.get(board["board"], OTHER)


def board_label(board):
    """A board as its label names it: "acorn-holly 0x00200c8664b04854" where its check read the DNA (rpi-hwid's
    name for it, then the DNA), else its type and the id its check read ("TT FPGA 4df39a7a6856f86f"), else its
    type."""
    title = board_title(board)
    dna = _text(board["identity"].get("dna", ""), 40)
    namer = DNA_NAMES.get(board["board"])
    if dna and namer:
        try:
            return f"{namer(dna)} {dna}"
        except ValueError:  # a DNA rpi-hwid cannot read: the type and what was sent
            return f"{title} {dna}"
    ident = board_id(board)
    return f"{title} {ident}" if ident else title


def pi_label(machine):
    """A Pi as its label names it: "Pi 5 2 GB 285df3f84af242d0", from the revision code its registration sent
    (rpi-hwid's decode_revision) and its serial. With no revision registered: "Pi" and the serial; with one
    rpi-hwid cannot read, that is said in the label (fail loud: the page does not quietly lose it)."""
    doc = machine.latest_snapshot.document if machine.latest_snapshot else {}
    facts = doc.get("machine") if isinstance(doc, dict) else None
    code = facts.get("revision_code") if isinstance(facts, dict) else None
    if not isinstance(code, str) or not code:
        return f"Pi {machine.serial}"
    try:
        rev = decode_revision(code)
    except (ValueError, KeyError):
        return f"Pi {machine.serial} (revision {_text(code, 20)!r} not readable)"
    return f"Pi {rev.model} {rev.memory} {machine.serial}"


def board_id(board):
    """The value on the board's label that its check read: a USB serial, a DNA, a serial."""
    ids = board["identity"]
    return _text(ids.get("usb_serial") or ids.get("dna") or ids.get("serial") or "", 40)


def board_indices(detail):
    """The `i` of each board<i> entry, in the order _reported_boards reads them (the check's own numbering, which
    need not be 0, 1, 2, ...)."""
    found = sorted(int(m[1]) for key in detail if isinstance(key, str) and (m := _BOARD_KEY.fullmatch(key)))
    return found[:MAX_BOARDS]


def board_tests(detail, index):
    """[(name, result)] of a board's tests, in the check's order, from `board<i>_tests` ("sdk=pass
    dip-switches=fail"); a word without "=" is not a test."""
    tests = detail.get(f"board{index}_tests", "")
    words = tests.split() if isinstance(tests, str) else []
    return [(_text(name, 40), _text(result, 20)) for name, _, result in (w.partition("=") for w in words) if result]


def failing_tests(detail, index):
    """The names of a board's tests that did not pass."""
    return [name for name, result in board_tests(detail, index) if result != "pass"]


def _snapshot_kinds(doc):
    """The board kinds a registration document names (fpga.boards[].kind), whatever shape the document has: it
    is what a Pi sent, and one that does not fit must not break the page."""
    fpga_doc = doc.get("fpga") if isinstance(doc, dict) else None
    boards = fpga_doc.get("boards") if isinstance(fpga_doc, dict) else None
    if not isinstance(boards, list):
        return []
    kinds = (b.get("kind") for b in boards[:MAX_BOARDS] if isinstance(b, dict))
    return [k for k in kinds if isinstance(k, str) and k]


@dataclass
class Host:
    machine: Machine
    condition: str
    state: str  # the check of the boot it is running: "pass", "fail", "verifying", "" (not started), ...
    checked: object  # when that check reported: its event's time, as the Pi stamped it; or None
    boards: list = field(default_factory=list)  # [{"title", "variant", "id", "result", "reason", "failing", "tests"}]
    board_type: str = NO_BOARD
    checked_in: bool = False
    pi: Pi | None = None
    registered_kinds: list = field(default_factory=list)  # the board kinds its registration names, as it named them

    @property
    def condition_title(self):
        return CONDITION_TITLES[self.condition]

    @property
    def uptime(self):
        """The uptime the Pi last reported, as a person reads it: "1 h 0 min", "3 d 4 h", "12 min"."""
        s = self.machine.last_uptime_s
        if not s or not self.checked_in:  # the uptime of a Pi that stopped checking in says nothing now
            return ""
        days, rest = divmod(s, 86400)
        hours, rest = divmod(rest, 3600)
        minutes = rest // 60
        if days:
            return f"{days} d {hours} h"
        if hours:
            return f"{hours} h {minutes} min"
        return f"{minutes} min"

    @property
    def place(self):
        """The sort key: switch, then port, as numbers (pi-sw2-p4 before pi-sw2-p33); a name that names no port
        after every one that does."""
        if self.pi is None:
            return (1, 0, 0, self.machine.hostname, self.machine.serial)
        return (0, self.pi.switch or 0, self.pi.port, self.machine.hostname, self.machine.serial)

    @property
    def registered(self):
        """When the Pi last sent its registration (the hostname it booted with is its word from then)."""
        snap = self.machine.latest_snapshot
        return snap.last_confirmed if snap else None

    @property
    def why(self):
        """Why the machine is not offered to visitors, in the words its check gave (empty when it is offered)."""
        if self.condition == "operational":
            return ""
        if not self.checked_in:
            return "it has stopped checking in"
        if self.state == "":
            return "its boot check has not started this boot"
        if self.state == "verifying":
            return "its boot check is running"
        reasons = "; ".join(b["reason"] for b in self.boards if b["reason"])
        said = {"fail": "its boot check failed", "error": "its boot check could not finish"}.get(
            self.state, f"its boot check gave {self.state}")
        return said + (f": {reasons}" if reasons else "")


def hosts(now=None):
    """Every registered machine, by switch and port, with its check of the boot it is running."""
    now = now or timezone.now()
    live = checked_in()
    checks = {}
    events = BootEvent.objects.filter(stage__in=FPGA_STAGES, boot_id=F("machine__last_boot_id")) \
        .exclude(boot_id="").values_list("machine__serial", "stage", "detail", "ts").order_by("id")
    for serial, stage, detail, ts in events:
        if stage == "fpga-verifying":
            checks[serial] = ("verifying", {}, ts)
        elif isinstance(detail, dict) and detail.get("result"):
            checks[serial] = (_text(str(detail["result"]), 20), detail, ts)
        else:
            checks[serial] = ("unknown", {}, ts)
    rows = []
    for m in Machine.objects.select_related("latest_snapshot").order_by("hostname", "serial"):
        state, detail, ts = checks.get(m.serial, ("", {}, None))
        boards = []
        for i, b in zip(board_indices(detail), _reported_boards(detail)) if detail else ():
            # the variant, when it says more than the type: an Acorn's model, not a Tiny Tapeout board's "tt-fpga"
            variant = "" if b["board"] == "tt" else _text(b["variant"], 40)
            boards.append({"title": board_title(b), "label": board_label(b), "variant": variant, "id": board_id(b),
                           "usb_serial": _text(b["identity"].get("usb_serial", ""), 40) if b["board"] == "tt" else "",
                           "result": _text(b["result"], 20), "reason": _text(b.get("reason", "")),
                           "failing": failing_tests(detail, i), "tests": board_tests(detail, i)})
        kinds = []
        if boards:
            board_type = " + ".join(dict.fromkeys(b["title"] for b in boards))
        else:
            kinds = _snapshot_kinds(m.latest_snapshot.document if m.latest_snapshot else {})
            board_type = " + ".join(dict.fromkeys(SNAPSHOT_KINDS.get(k, OTHER) for k in kinds if k)) or NO_BOARD
        has_checked_in = m.serial in live
        rows.append(Host(machine=m, condition=condition(state, has_checked_in, m.last_seen, now), state=state,
                         checked=ts, boards=boards, board_type=board_type, checked_in=has_checked_in,
                         registered_kinds=list(dict.fromkeys(_text(k, 40) for k in kinds if k)),
                         pi=Pi.from_hostname(m.hostname.split(".")[0]) if m.hostname else None))
    return sorted(rows, key=lambda r: r.place)


def summary(rows):
    """[(board type, {condition: count}, total)] in board-type order, then the totals row."""
    types = {}
    for row in rows:
        counts = types.setdefault(row.board_type, dict.fromkeys(CONDITIONS, 0))
        counts[row.condition] += 1
    table = [(t, counts, sum(counts.values())) for t, counts in sorted(types.items())]
    total = {c: sum(counts[c] for _, counts, _ in table) for c in CONDITIONS}
    return table, total, sum(total.values())
