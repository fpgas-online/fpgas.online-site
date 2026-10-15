"""The FPGA dashboard's data (#68): every registered machine with what its boot check reported, and a summary
by board type. Everything comes from the fleet registry (each Pi's registration and boot events); nothing here
is a remembered table of which board is on which port."""

import re
from dataclasses import dataclass, field

from django.db.models import F, Max
from django.utils import timezone
from fleet.models import BootEvent, GatewayRecord, Machine
from fleet.services import _BOARD_KEY, CONDITIONS, FPGA_STAGES, MAX_BOARDS, _reported_boards, checked_in, condition
from pibfpgas.pis import Pi
from rpi_hwid import names
from rpi_hwid.boards import identify
from rpi_hwid.model import Summary
from rpi_hwid.probe import nominal_memory
from rpi_hwid.revision import decode_revision

from . import check_groups
from .states import WORDS

# The board names fpgas-verify's modules use (board<i>), as a visitor reads them. "xilinx-pcie" is its name
# (conclusion.OTHER_PCIE) for a Xilinx PCIe card that is not ours (15 Oct 2026: one on welland showed as "other").
TYPE_TITLES = {"acorn": "Acorn", "arty": "Arty A7", "netv2": "NeTV2", "fomu": "Fomu", "pcileech": "PCIe card",
               "xilinx-pcie": "PCIe card"}
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
CONDITION_TITLES = WORDS  # one set of names everywhere (management.states, #131)
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


def board_parts(board):
    """(name, id) of a board as its label gives them: rpi-hwid's name and the DNA where its check read one
    ("acorn-holly", "0x00200c8664b04854"), else its type and the id its check read ("TT FPGA", "4df39a7a6856f86f"),
    else its type and "". The Pi table shows the two in two columns (one value a column)."""
    title = board_title(board)
    dna = _text(board["identity"].get("dna", ""), 40)
    namer = DNA_NAMES.get(board["board"])
    if dna and namer:
        try:
            return namer(dna), dna
        except ValueError:  # a DNA rpi-hwid cannot read: the type and what was sent
            return title, dna
    return title, board_id(board)


def board_label(board):
    """A board as its label names it: "acorn-holly 0x00200c8664b04854", "TT FPGA 4df39a7a6856f86f", "Fomu"."""
    return " ".join(part for part in board_parts(board) if part)


def registered_model(machine):
    """The model string the host's registration sent, as it sent it (cut), or ""."""
    doc = machine.latest_snapshot.document if machine.latest_snapshot else {}
    facts = doc.get("machine") if isinstance(doc, dict) else None
    model = facts.get("model") if isinstance(facts, dict) else None
    return _text(model, MAX_TEXT) if isinstance(model, str) else ""


def short_model(model):
    """"Pi 3 Model B+" -> "Pi 3B+", "Pi 4 Model B" -> "Pi 4B", "Pi Compute Module 4" -> "Pi CM4", "Orange Pi PC" ->
    "OPi PC"; any other model as it is ("Pi 5", "Pi Zero 2 W")."""
    short = re.sub(r"(\d) Model ", r"\1", model)
    short = short.replace("Compute Module ", "CM")
    return re.sub(r"^Orange Pi\b", "OPi", short)


@dataclass(frozen=True)
class PiFields:
    """A host's label as its values, each its own column on the Pi dashboard (Tim, 15 Oct 2026: "Model names in the
    same column as serial numbers"): the model as the label's header names it ("Pi 5", "Pi 3 Model B+", "Orange Pi
    PC"), the memory ("2 GB", "" where the registration gave none), the serial. `why` and `note` say what keeps the
    host from its label (both "" for a host with one); such a host has no model and no memory, only its serial."""
    model: str
    memory: str
    serial: str
    why: str = ""
    note: str = ""
    kind: str = "rpi"  # rpi-hwid's kind of host: "rpi" for a Raspberry Pi, else what identify() says ("h3" ...)

    @property
    def short(self):
        """The model in a few characters for a dashboard column (Tim, 9 Oct 2026: "Pi 3B+", "Pi 5", "OPi PC"): the
        label's "Model " and "Compute Module " folded ("Pi 3 Model B+" -> "Pi 3B+", "Pi Compute Module 4" -> "Pi
        CM4"), "Orange Pi" as "OPi". The label's own words stay in the cell's title."""
        return short_model(self.model)

    @property
    def label(self):
        """"Pi 5 2 GB 285df3f84af242d0", or the serial alone for a host with no label."""
        return " ".join(part for part in (self.model, self.memory, self.serial) if part)

    @property
    def memory_mb(self):
        """The memory as a number of MB, for sorting ("512 MB" before "1 GB"); 0 for none."""
        if not self.memory:
            return 0
        number, _, unit = self.memory.partition(" ")
        scale = {"MB": 1, "GB": 1024, "TB": 1024 * 1024}.get(unit)
        try:
            value = float(number)
        except ValueError:
            value = None
        if scale is None or value is None:
            raise ValueError(f"memory {self.memory!r} is not '<number> MB|GB|TB'")
        return int(value * scale)


def _memory(facts):
    kb = facts.get("mem_total_kb")
    return nominal_memory(kb) if isinstance(kb, int) and not isinstance(kb, bool) and kb > 0 else None


def _other_board(facts, model, serial):
    """PiFields of a host whose registered model rpi-hwid identifies as a board that is not a Raspberry Pi, else
    None."""
    if not isinstance(model, str) or not model.strip():
        return None
    memory = _memory(facts)
    try:
        ident = identify(Summary(model=model.strip(), serial=serial, revision="", memory=memory))
    except (ValueError, KeyError):  # a Raspberry Pi model: its revision code is what names it
        return None
    if ident is None or ident.kind == "rpi":
        return None
    return PiFields(ident.short, memory or "", serial, kind=ident.kind)


def pi_fields(machine):
    """A host's label by rpi-hwid's own rules, as its values (PiFields): from the revision code its registration sent
    (decode_revision, the label header's "Pi <model>"), or for a host with no revision code, an Orange Pi, by
    rpi-hwid's identify() ("Orange Pi PC"). What keeps a host from its label is `why` in full ("revision 'zz' not
    readable") and `note` as a short value ("revision unreadable")."""
    doc = machine.latest_snapshot.document if machine.latest_snapshot else {}
    facts = doc.get("machine") if isinstance(doc, dict) else None
    facts = facts if isinstance(facts, dict) else {}
    code, model = facts.get("revision_code"), facts.get("model")
    serial = machine.serial
    if code is not None and (not isinstance(code, str) or not code):
        return PiFields("", "", serial, f"revision {_text(str(code), 20)!r} not readable", "revision unreadable")
    if code:
        try:
            rev = decode_revision(code)
        except (ValueError, KeyError):
            # not a Raspberry Pi's code: an Orange Pi's kernel says "0000" (welland's seven, 15 Oct 2026). Its
            # registered model names it when rpi-hwid knows that model as another board; else the code is the fault.
            other = _other_board(facts, model, serial)
            if other is not None:
                return other
            return PiFields("", "", serial, f"revision {_text(code, 20)!r} not readable", "revision unreadable")
        return PiFields(f"Pi {rev.model}", rev.memory, serial)
    if model is None:
        return PiFields("", "", serial, "no model registered", "no model registered")
    if not isinstance(model, str) or not model.strip():
        return PiFields("", "", serial, f"model {_text(str(model), 40)!r} not readable", "model unreadable")
    memory = _memory(facts)
    try:
        ident = identify(Summary(model=model.strip(), serial=serial, revision="", memory=memory))
    except (ValueError, KeyError):  # a Raspberry Pi by its model, with no revision code to decode
        return PiFields("", "", serial, "no revision code registered", "no revision code")
    if ident is None:
        return PiFields("", "", serial, f"model {_text(model.strip(), 40)!r} not known to rpi-hwid", "model unknown")
    return PiFields(ident.short, memory or "", serial, kind=ident.kind)


def pi_label_parts(machine):
    """(label, why, note) of a host: its label ("Pi 5 2 GB 285df3f84af242d0", "Orange Pi PC 02c0008164c2a1b0"), or
    its serial alone with what keeps it from its label (pi_fields)."""
    fields = pi_fields(machine)
    return fields.label, fields.why, fields.note


def pi_label(machine):
    """A host as its label names it, in one form for a program: the label, or "<serial> (<why>)" for a host that
    has none (fail loud: the page does not quietly lose it). The Pi dashboard shows the two apart."""
    label, why, _ = pi_label_parts(machine)
    return f"{label} ({why})" if why else label


NONE = "–"  # a value that is not known, as the switch page shows it


def port_text(machine):
    """"sw2 p48": the port a Pi's host name says it booted on (an observation, never its name), or NONE."""
    pi = Pi.from_hostname(machine.hostname.split(".")[0]) if machine.hostname else None
    if pi is None:
        return NONE
    return f"sw{pi.switch} p{pi.port}" if pi.switch else f"p{pi.port}"


def board_id(board):
    """The value on the board's label that its check read: a USB serial, a DNA, a serial."""
    ids = board["identity"]
    return _text(ids.get("usb_serial") or ids.get("dna") or ids.get("serial") or "", 40)


def board_indices(detail):
    """The `i` of each board<i> entry, in the order _reported_boards reads them (the check's own numbering, which
    need not be 0, 1, 2, ...)."""
    found = sorted(int(m[1]) for key in detail if isinstance(key, str) and (m := _BOARD_KEY.fullmatch(key)))
    return found[:MAX_BOARDS]


def board_tests(detail, index, alias=True):
    """[(name, result)] of a board's tests, in the check's order, from `board<i>_tests` ("sdk=pass
    dip-switches=fail"); a word without "=" is not a test."""
    tests = detail.get(f"board{index}_tests", "")
    words = tests.split() if isinstance(tests, str) else []
    return [(check_groups.name(_text(name, 40)) if alias else _text(name, 40), _text(result, 20))
            for name, _, result in (w.partition("=") for w in words) if result]


def _not_run_names(detail, index):
    names = detail.get(f"board{index}_not_run", "")
    if not isinstance(names, str):
        return []
    return [_text(n.strip(), 60) for n in names.split(",") if n.strip()]


def not_run_tests(detail, index):
    """The tests a board's check left out because something earlier did not pass (fpgas-verify's `board<i>_not_run`:
    names joined by ", "), in its order: "should have run, did not" on the Pi table (site #127)."""
    return [check_groups.name(n) for n in _not_run_names(detail, index) if " " not in n]


def planned_tests(detail, index):
    """The checks the board's type gets, as its own report says (fpgas-verify's `board<i>_planned`, space-separated,
    in run order; fpgas.online-test-designs#339), or None for a report that does not say (an older verify)."""
    planned = detail.get(f"board{index}_planned")
    if not isinstance(planned, str):
        return None
    return [check_groups.name(_text(n, 40)) for n in planned.split()]


def keyed_reasons(detail, index, key):
    """{check: reason} of the report's `board<i>_<key>_<check>` entries (`not_run`: why a planned check did not run;
    `not_applicable`: why a common check does not apply to this type), cut as every text a Pi sends."""
    prefix = f"board{index}_{key}_"
    return {check_groups.name(k[len(prefix):][:40]): _text(v) for k, v in detail.items()
            if isinstance(k, str) and k.startswith(prefix) and isinstance(v, str)}


def not_run_parts(detail, index):
    """The parts of tests that ran which the check left out ("scratch over BAR0": a name with a space, the test's
    name first): said in that test's cell, never dropped (the review of #128)."""
    return [n for n in _not_run_names(detail, index) if " " in n]


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
    no_board_expected: bool = False  # carries_no_board(): a host whose check finding no board is as it should be

    @property
    def board_text(self):
        """Its boards by their labels ("acorn-holly 0x00200c8664b04854 + TT FPGA 4df3..."); with no report this
        boot, the kinds its registration names; else its board type."""
        if self.boards:
            return " + ".join(b["label"] for b in self.boards)
        return " + ".join(self.registered_kinds) or self.board_type

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


# The type of a host that carries no FPGA board (the Orange Pi PCs, USB-booted from the hub Pi): its own row in the
# summary, never "no board" as a board type.
NO_FPGA = "no FPGA"


def carries_no_board(machine):
    """Whether a host carries no FPGA board by design: one switch (the coordinator's default for coord-65, Tim's
    answer may change it): a host rpi-hwid identifies as a board that is not a Raspberry Pi (welland's Orange Pi PCs);
    every Raspberry Pi on a fleet port is there to carry a board."""
    fields = pi_fields(machine)
    return not fields.why and fields.kind != "rpi"


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
            name, ident = board_parts(b)
            boards.append({"kind": _text(b["board"], 40), "title": board_title(b), "label": board_label(b),
                           "name": name, "label_id": ident,
                           "variant": variant, "id": board_id(b),
                           "usb_serial": _text(b["identity"].get("usb_serial", ""), 40) if b["board"] == "tt" else "",
                           "result": _text(b["result"], 20), "reason": _text(b.get("reason", "")),
                           "failing": failing_tests(detail, i), "tests": board_tests(detail, i),
                           # as the Pi sent them, old names and all: /fleet/registry.json's contract
                           "reported_tests": board_tests(detail, i, alias=False),
                           "not_run": not_run_tests(detail, i), "not_run_parts": not_run_parts(detail, i),
                           "planned": planned_tests(detail, i),
                           "not_run_reasons": keyed_reasons(detail, i, "not_run"),
                           "not_applicable": keyed_reasons(detail, i, "not_applicable"),
                           # the type as a visitor reads it, its variant with it (#127): "Acorn CLE-215+", "TT FPGA"
                           "type": f"{board_title(b)} {variant.upper()}" if variant else board_title(b)})
        kinds = []
        if boards:
            board_type = " + ".join(dict.fromkeys(b["title"] for b in boards))
        else:
            kinds = _snapshot_kinds(m.latest_snapshot.document if m.latest_snapshot else {})
            board_type = " + ".join(dict.fromkeys(SNAPSHOT_KINDS.get(k, OTHER) for k in kinds if k)) or NO_BOARD
        has_checked_in = m.serial in live
        no_board = not boards and not kinds and carries_no_board(m)
        if no_board:
            board_type = NO_FPGA
        # a host that carries no board and whose check found none is as it should be (the coordinator's default for
        # coord-65, 15 Oct 2026): judged as a check that passed, never "needs attention" for the absence of a board
        judged = "pass" if no_board and state == "missing" else state
        rows.append(Host(machine=m, condition=condition(judged, has_checked_in, m.last_seen, now), state=state,
                         checked=ts, boards=boards, board_type=board_type, checked_in=has_checked_in,
                         registered_kinds=list(dict.fromkeys(_text(k, 40) for k in kinds if k)),
                         pi=Pi.from_hostname(m.hostname.split(".")[0]) if m.hostname else None,
                         no_board_expected=no_board))
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


@dataclass(frozen=True)
class SeenPort:
    """The port the gateway last saw a machine on: its newest lease record that names a port (fleet.GatewayRecord,
    from dnsmasq on the gateway), and when. An observation with its time, never a remembered table."""
    switch: int
    port: int
    at: object  # datetime

    @property
    def text(self):
        return f"sw{self.switch} p{self.port}"


def seen_ports():
    """{machine id: SeenPort} for every machine the gateway has leased an address to on a port, in two queries."""
    newest = GatewayRecord.objects.filter(kind="lease", machine__isnull=False, switch__isnull=False,
                                          port__isnull=False).values("machine_id").annotate(last=Max("id")) \
        .values_list("last", flat=True)
    return {r.machine_id: SeenPort(r.switch, r.port, r.at)
            for r in GatewayRecord.objects.filter(id__in=list(newest))}
