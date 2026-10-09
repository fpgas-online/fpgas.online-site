"""The FPGA dashboard's data (#68): every registered machine with what its boot check reported, and a summary
by board type. Everything comes from the fleet registry (each Pi's registration and boot events); nothing here
is a remembered table of which board is on which port."""

from dataclasses import dataclass, field

from django.db.models import F
from django.utils import timezone
from fleet.models import BootEvent, Machine
from fleet.services import _BOARD_KEY, CONDITIONS, FPGA_STAGES, MAX_BOARDS, _reported_boards, checked_in, condition
from pibfpgas.pis import Pi

# The board names fpgas-verify's modules use (board<i>), as a visitor reads them.
TYPE_TITLES = {"acorn": "Acorn", "arty": "Arty A7", "netv2": "NeTV2", "fomu": "Fomu", "pcileech": "PCIe card"}
OTHER = "other"  # a board name this page does not know: one bucket, so a Pi cannot make a row per name it sends
SNAPSHOT_TT = "TT, not checked this boot"
# What a registration's hardware document calls a board kind, for a machine whose check reported none this boot.
SNAPSHOT_KINDS = {"tt-demo-board": SNAPSHOT_TT, "arty-a7": "Arty A7", "xilinx-pcie": "PCIe card",
                  "netv2": "NeTV2", "fomu": "Fomu", "acorn": "Acorn"}
NO_BOARD = "no board"
CONDITION_TITLES = {"operational": "operational", "attention": "needs attention", "missing": "missing",
                    "unknown": "unknown"}
# The summary's count columns hold one or two digits: their headers are one symbol each (its title the full name,
# and a key beside the table), so a column is as wide as its counts, not its header (Tim, 9 Oct 2026).
CONDITION_SYMBOLS = {"operational": "\u2713", "attention": "!", "missing": "\u2717", "unknown": "?"}
MAX_TEXT = 300


def _text(value, limit=MAX_TEXT):
    return value[:limit] if isinstance(value, str) else ""


def board_title(board):
    """A reported board's type, for the summary: a Tiny Tapeout board by what it said it carries."""
    if board["board"] == "tt":
        chip = _text(board["identity"].get("chip", ""), 20).lower()
        return {"fpga": "TT FPGA", "asic": "TT chip"}.get(chip, "TT (not identified)")
    if not board["board"]:
        return "not readable"
    return TYPE_TITLES.get(board["board"], OTHER)


def board_id(board):
    """The value on the board's label that its check read: a USB serial, a DNA, a serial."""
    ids = board["identity"]
    return _text(ids.get("usb_serial") or ids.get("dna") or ids.get("serial") or "", 40)


def board_indices(detail):
    """The `i` of each board<i> entry, in the order _reported_boards reads them (the check's own numbering, which
    need not be 0, 1, 2, ...)."""
    found = sorted(int(m[1]) for key in detail if isinstance(key, str) and (m := _BOARD_KEY.fullmatch(key)))
    return found[:MAX_BOARDS]


def failing_tests(detail, index):
    """The names of a board's tests that did not pass, from `board<i>_tests` ("sdk=pass dip-switches=fail")."""
    tests = detail.get(f"board{index}_tests", "")
    words = tests.split() if isinstance(tests, str) else []
    return [_text(name, 40) for name, _, result in (w.partition("=") for w in words) if result and result != "pass"]


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
    boards: list = field(default_factory=list)  # [{"title", "variant", "id", "result", "reason", "failing"}]
    board_type: str = NO_BOARD
    checked_in: bool = False
    pi: Pi | None = None

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
            boards.append({"title": board_title(b), "variant": variant, "id": board_id(b),
                           "result": _text(b["result"], 20), "reason": _text(b.get("reason", "")),
                           "failing": failing_tests(detail, i)})
        if boards:
            board_type = " + ".join(dict.fromkeys(b["title"] for b in boards))
        else:
            kinds = _snapshot_kinds(m.latest_snapshot.document if m.latest_snapshot else {})
            board_type = " + ".join(dict.fromkeys(SNAPSHOT_KINDS.get(k, OTHER) for k in kinds if k)) or NO_BOARD
        has_checked_in = m.serial in live
        rows.append(Host(machine=m, condition=condition(state, has_checked_in, m.last_seen, now), state=state,
                         checked=ts, boards=boards, board_type=board_type, checked_in=has_checked_in,
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
