"""The Raspberry Pi dashboard (#68, #94): every registered Pi with its board, its boot check and how it is doing,
and a summary by board type, from the fleet registry, by the one rule that offers a Pi (fleet.services.is_offered).

One page of the Pis (Tim, 9 Oct 2026: "two dashboards of the same Pis"): it is /fleet/ where the host has the fleet
pages (pib/urls.py mounts it there as fleet-list), and /management/fpgas/ sends a browser there. On a host without
the fleet pages (tinytapeout.fpgas.online's urlconf) /management/fpgas/ is the page itself."""

from dataclasses import dataclass

from django.conf import settings
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import Resolver404, resolve
from django.utils import timezone
from fleet.services import ATTENTION, CONDITIONS, MISSING, OPERATIONAL, UNKNOWN
from fleet.timeago import ago_time, duration
from pibfpgas.pis import listed
from ttsite.middleware import serves_ttsite

from .. import check_docs, check_groups, e2e, fpga, localtime, network, states
from .index import _board_reason, _url

# The State column's order when sorted: what needs a person first.
SEVERITY = {ATTENTION: 0, MISSING: 1, UNKNOWN: 2, OPERATIONAL: 3}
# A check's state as one word, then the failing tests' names after a colon: one form for the column.
CHECK_WORDS = {"pass": "passed", "fail": "failed", "error": "could not finish", "missing": "found no board",
               "unknown": "unreadable", "verifying": "running", "": "not started"}


def _resolves(request, path):
    """Whether this host's urlconf has a page at `path` (the /fpgas/ pages are not on tinytapeout's)."""
    try:
        resolve(path, urlconf=getattr(request, "urlconf", None))
    except Resolver404:
        return False
    return True


def fpgas(request):
    """/management/fpgas/: the Pi dashboard's address on every host. Where the host has /fleet/, it is there."""
    fleet = _url(request, "fleet-list")
    if fleet:
        query = request.META.get("QUERY_STRING", "")
        return HttpResponseRedirect(fleet + (f"?{query}" if query else ""))
    return pis(request)


def pis(request):
    # every time on the page in the installation's own zone, named once in the key; for this page only
    # (override, not activate: the worker's next request keeps the site's own zone)
    with timezone.override(localtime.zone()):
        return _page(request)


@dataclass
class Mark:
    """One board's state of one check, as its cell shows it, one character (Tim, 15 Oct 2026, site #127: "The
    checks columns should *still* be one character wide despite these different states"): ✓ ran and passed, ✗ ran
    and failed, ⊘ should have run but did not (an earlier check did not pass), – not applicable to this board.
    `title` says it in words, with the check's own reason (the popup on hover); a screen reader is given the same."""
    mark: str
    cls: str
    title: str


# The four states (#127): (mark, css class, sort key). A cell sorts as its worst mark: failures first, then the checks
# that did not run, then passes, then the Pis a check does not apply to.
PASSED, FAILED, NOT_RUN, NOT_APPLICABLE = ("✓", "mgmt-test-pass", 2), ("✗", "mgmt-test-fail", 0), \
    ("⊘", "mgmt-test-notrun", 1), ("–", "mgmt-test-none", 3)
FAIL_SORT = FAILED[2]
# The State column, one character (#127: "The 'State' column should be 1 character wide"), the word in its title and
# for a screen reader: a mark per condition that needs no legend beside the summary's badges of the same colours.
STATE_MARKS = states.MARKS


@dataclass
class BootMark:
    """The Boot check cell: its mark and its sort key (failures first, as the check columns)."""
    mark_: Mark
    sort: int

    @property
    def mark(self):
        return self.mark_.mark

    @property
    def cls(self):
        return self.mark_.cls

    @property
    def title(self):
        return self.mark_.title


@dataclass
class CheckCell:
    """A Pi's cell in a check's column: a mark per board, in the order of the Board cell's lines."""
    name: str  # the check, for the cell's label on a phone
    marks: list
    sort: int
    link: str  # the Pi's own page, where every check's result and reason is, for a cell with a ✗

    @property
    def title(self):
        """The cell's title, so hovering anywhere in it shows the state (#123): one line per board."""
        return "\n".join(m.title for m in self.marks)


@dataclass
class Line:
    """A Pi's row as the page shows it: one value per field, each field its own column (#116, #121, #127)."""
    host: fpga.Host
    state: str  # the condition as one mark (STATE_MARKS); its word is host.condition_title
    model: str  # "Pi 3B+", "OPi PC" (the label's header, short); "" for a host with no label (Notes says why)
    model_title: str  # the label's words and the model string the host registered, for the Model cell's title
    memory: str  # "2 GB", ""
    memory_mb: int
    serial: str
    port: str  # "sw1 p10": the port it is on (fpga.seen_ports: one device a port); "" when it is on none
    port_title: str  # where the port comes from, and when
    sort_port: str  # switch and port as numbers, zero-padded: sw2 p4 before sw2 p33
    boot: Mark  # the boot check of the boot it is running, one character like every check (#129), words on hover
    boards: list  # [(type, id, label)] of each board ("Acorn CLE-215+", "0x0020...", "acorn-holly 0x0020..."), or
                  # the kinds its registration names: one per line in the Board and ID cells
    checks: list  # a CheckCell per check column
    uptime: str  # "3 d 4 h", or ""
    sort_uptime: int  # seconds, 0 for none
    seen: str  # <time> html, or "" when the time cannot be shown
    sort_seen: int
    checked: str  # when the boot check of this boot reported: <time> html, or ""
    sort_checked: int
    severity: int
    offered: bool
    offered_title: str  # "offered" or "not offered: <why>" (#127: "the 'why not' being a popup / shown on hover")
    notes: list  # short values, one a line: "revision unreadable", "clock 2 h ahead"
    page: str  # the visitor page an offered Pi's ✓ links to


# The column headers' titles: what each column is, said once (#129: "What is the differences between Checked and
# operational?"). "operational" is a State; "Check ran" is a time.
HEAD_TITLES = {
    "state": "operational: it checks in and the boot check of the boot it is running passed, so a board on it "
             "is offered to visitors; needs attention: that check did not pass (it failed, could not finish, or found "
             "no board), or it stopped checking in; missing: not heard from for 6 h; checking: that check has not "
             "finished",
    "model": "the Pi's model, as its label says", "ram": "the Pi's memory",
    "serial": "the Pi's serial number; links its own page",
    "port": "the switch port the gateway last ACKed it an address on (one device a port: empty when another "
            "device was ACKed there since)", "board": "the FPGA board's type",
    "id": "the id on the board's label (its DNA or USB serial); its name on hover",
    "offered": "offered to visitors: ✓ links its visitor page, ✗ says why not",
    "up": "how long the Pi has been up", "seen": "its last status beat",
    "boot_check": "the boot check of the boot it is running: ✓ passed, ✗ did not pass, ⟳ running, ⊘ not started",
    "check_ran": "when the boot check of the boot it is running reported",
    "check": "✓ ran and passed, ✗ ran and failed, ⊘ should have run but did not, – not applicable",
    "notes": "what the page could not read",
}


def boot_mark(host, checked_text=""):
    """The boot check of this boot as one character (#129: "Boot check is still random text"): ✓ passed, ✗ did not
    pass (failed, could not finish, found no board, unreadable, or a result the page does not know), ⟳ running,
    ⊘ not started; its words, and why the Pi is not offered, in the title."""
    word = _check_word(host)
    # why the Pi is not offered goes with a check that did not pass; a passed check is not contradicted by it
    passed = host.state == "pass"
    # the why, unless it only says the word again ("found no board": "no board found")
    said = host.why and not passed and not (host.state == "missing" and host.why == "no board found")
    title = word + (f", {checked_text}" if checked_text else "") + (f": {host.why}" if said else "")
    if passed:
        return Mark(PASSED[0], PASSED[1], title), PASSED[2]
    if host.state == "verifying":
        return Mark("⟳", "mgmt-test-running", title), NOT_RUN[2]
    if host.state == "":
        return Mark(NOT_RUN[0], NOT_RUN[1], title), NOT_RUN[2]
    return Mark(FAILED[0], FAILED[1], title), FAILED[2]


def _check_word(host):
    """The check of this boot as one word ("passed", "failed", "running"); a result the page does not know is shown
    as it came ("gave <result>"), never "unknown"."""
    return CHECK_WORDS.get(host.state) or f"gave {host.state}"


def _check(host):
    word = _check_word(host)
    failing = list(dict.fromkeys(name for b in host.boards for name in b["failing"]))
    tests = "; ".join(" ".join(f"{name}={result}" for name, result in b["tests"]) for b in host.boards if b["tests"])
    reasons = "; ".join(b["reason"] for b in host.boards if b["reason"])
    return (f"{word}: {', '.join(failing)}" if failing else word), "; ".join(t for t in (tests, reasons) if t)


def _offer(host, now, pages):
    """(offered, why not, its visitor page) for one Pi. Every "yes" links a page (rule 20): an offered Pi no visitor
    page shows is not offered, "no visitor page"."""
    if host.condition != OPERATIONAL:
        try:
            return False, _board_reason(host, now), ""  # the index's Boards rows say the same
        except ValueError as exc:  # a value the page does not know: said in the cell, the page stands
            return False, str(exc), ""
    page = pages.get(("pi", host.machine.hostname)) or next(
        (pages[("tt", b["usb_serial"])] for b in host.boards if b["usb_serial"] and ("tt", b["usb_serial"]) in pages), "")
    return (True, "", page) if page else (False, "no visitor page", "")


def _offered(host, now, pages):
    """("yes", its visitor page) or ("no: <why>", "") for one Pi, as the host's own page words it."""
    offered, why, page = _offer(host, now, pages)
    return ("yes", page) if offered else (f"no: {why}", "")


def _visitor_pages(request):
    """{("pi", hostname): its /fpgas/ page, ("tt", usb serial): its Tiny Tapeout board page}: the visitor pages of
    this installation, as those pages list them."""
    # the /fpgas/ pages: by path on a host that has them, else at the site's own address (DOMAIN_NAME, which the
    # /fpgas/ pages need too): tinytapeout.fpgas.online's Acorns are offered at welland.fpgas.online
    base = "" if _resolves(request, "/fpgas/") else f"https://{settings.DOMAIN_NAME}"
    pages = {("pi", pi.hostname): f"{base}/fpgas/{pi.hostname}.html" for pi in listed()}
    from ttsite.boards import pages as tt_pages

    # tinytapeout.fpgas.online's board pages: on its own host by path, from any other by its address
    base = "" if serves_ttsite(request) else f"https://{settings.TTSITE_HOST}"
    pages.update({("tt", page.live.usb_serial): f"{base}/board/{page.slug}/" for page in tt_pages() if page.live})
    return pages


def plan(board):
    """(the checks the board's type gets, {check: why not applicable}, {check: the issue of a test not written yet}):
    from the board's own report where it says (fpgas-verify's board<i>_planned and board<i>_not_applicable_<check>,
    fpgas.online-test-designs#339/#340), else from the site's copy of verify's lists (check_groups.PLANNED); ((), {},
    {}) for a type neither knows."""
    known = check_groups.planned(board["title"]) or ((), {}, {})
    checks = board["planned"] if board["planned"] is not None else list(known[0])
    return checks, board["not_applicable"] or dict(known[1]), dict(known[2])


def check_names(hosts):
    """Every check the shown Pis' boards get, ran or left out, in the order their checks gave them (#129: a check a
    board type should get is a column even before any board reports it)."""
    return list(dict.fromkeys(name for host in hosts for b in host.boards
                              for name in [*plan(b)[0], *(n for n, _ in b["tests"]), *b["not_run"]]))


def _stopped(board):
    """Whether a board's check stopped before any test: no test ran, none was left out, and it did not pass."""
    return not board["tests"] and not board["not_run"] and board["result"] != "pass"


def test_reason(board, name, whole=True):
    """What the board's check said about one test: the parts of its reason that start with the test's name ("pin-id
    fail: the test exited 1", "ddr not run: it needs jtag to pass first"; an old name counts as its new one), the
    reason split at "; " as the check joins it, a part that names no test going with the one before. A reason that
    names no test of the board is the board's reason, whole."""
    names = {test for test, _ in board["tests"]} | set(board["not_run"])
    groups, current = {}, None
    for part in board["reason"].split("; ") if board["reason"] else ():
        first = check_groups.name(part.split(" ", 1)[0].rstrip(":"))
        if first in names:
            current = first
        if current is not None:
            groups.setdefault(current, []).append(part)
    if name in groups:
        return "; ".join(groups[name])
    return board["reason"] if whole else ""


def _not_run_title(board, name, unwritten):
    """Why a check the board should get did not run, from the most specific thing the data says."""
    if name in board["not_run_reasons"]:
        return f"{name} did not run: {board['not_run_reasons'][name]}"
    own = test_reason(board, name, whole=False)
    if own and " not run" in own.split(":", 1)[0]:
        return own
    if name in unwritten:
        return f"{name} did not run: no boot-check test yet ({unwritten[name]})"
    if _stopped(board):
        return f"{name} did not run: " + (board["reason"] or f"the check of this board gave {board['result']}")
    if name in board["not_run"] and board["failing"]:
        return f"{name} did not run: {', '.join(board['failing'])} did not pass"
    # the board's check ran but did not report this check (an older fpgas-verify: jtag was kept outside the tests)
    return f"{name} was not reported by this board's check: its fpgas-verify does not report it yet"


def _mark(board, name, planned=None):
    result = dict(board["tests"]).get(name)
    # parts of this test the check left out ("scratch over BAR0"), said with it (the review of #128)
    parts = [p for p in board["not_run_parts"] if p.startswith(f"{name} ")]
    left_out = f" ({', '.join(parts)} not run)" if parts else ""
    if result == "pass":
        return Mark(PASSED[0], PASSED[1], f"{name} passed{left_out}"), PASSED[2]
    if result is not None:
        # every result but "pass" is a failure (fpgas-verify: only "pass" means the board is as it should be)
        reason = test_reason(board, name)
        # the check's own words start so ("pin-id fail: 0/18 pins match", an old name counting as its new one): not
        # said twice
        first, _, rest = reason.partition(" ")
        own = check_groups.name(first) == name and rest.startswith(result)
        title = reason if own else f"{name} {result}" + (f": {reason}" if reason else "")
        return Mark(FAILED[0], FAILED[1], title + left_out), FAILED[2]
    checks, not_applicable, unwritten = plan(board)
    # a check the board's type gets and that did not run is ⊘, never – (Tim, 15 Oct 2026: "*Anything* which has an
    # FPGA should be getting jtag, anything which has wires should be getting wiring")
    if name in board["not_run"] or name in checks:
        return Mark(NOT_RUN[0], NOT_RUN[1], _not_run_title(board, name, unwritten)), NOT_RUN[2]
    why = not_applicable.get(name)
    return Mark(NOT_APPLICABLE[0], NOT_APPLICABLE[1],
                f"{name} not applicable to {board['type']}" + (f": {why}" if why else "")), NOT_APPLICABLE[2]


def check_cell(host, name, link, planned=None):
    """A Pi's cell in the column of check `name`: a mark per board; one – for a Pi whose check named no board."""
    if not host.boards:
        why = "no board found" if host.state == "missing" else "no board checked this boot"
        mark = Mark(NOT_APPLICABLE[0], NOT_APPLICABLE[1], f"{name} not applicable: {why}")
        return CheckCell(name, [mark], NOT_APPLICABLE[2], "")
    marks, sorts = zip(*(_mark(b, name) for b in host.boards))
    worst = min(sorts)
    return CheckCell(name, list(marks), worst, link if worst == FAIL_SORT else "")


def _port(host, observed):
    """(port, its title, its sort key, a note or ""): the port the gateway last leased the Pi an address on, with the
    time, an observation (the dashboard spec: "switch port (observed, with its time, never a remembered table)").
    A Pi on no port (fpga.seen_ports: no DHCPACK on a port in the records, which are kept for days, or another device
    ACKed on its port since: one device a port) has no port: the cell is empty and Notes says so with the port its
    host name names, which is the Pi's word, not an observation (the review of #122). A
    host name naming another port than the gateway saw is a note too."""
    named = fpga.port_text(host.machine)
    if observed is None:
        return "", "", "999999", f"on no port; named {named}" if host.pi else "on no port"
    when = timezone.localtime(observed.at).strftime("%-d %b %H:%M")
    note = f"named {named}" if host.pi and named != observed.text else ""
    return (observed.text, f"the gateway leased it an address on this port at {when}",
            f"{observed.switch:03d}{observed.port:03d}", note)


def _line(host, now, pages, columns, detail, observed=None, planned=None):
    fields = fpga.pi_fields(host.machine)
    notes = [fields.note] if fields.note else []
    # a board name the page does not know is said, loud, in Notes: the Board cell's "other" is not a value
    notes.extend(f"board {b['kind']!r} not known" for b in host.boards if b["title"] == fpga.OTHER)
    port, port_title, sort_port, port_note = _port(host, observed)
    if port_note:
        notes.append(port_note)
    # a board whose check stopped before its tests, with no board of its type to say which it runs: loud, not dashes
    # alone (fpgas.online-test-designs#339 asks the check to list them)
    notes.extend(f"{b['type']}: checks not reported" for b in host.boards if _stopped(b) and not plan(b)[0])
    registered = fpga.registered_model(host.machine)
    model_title = "; ".join(t for t in (fields.model if fields.model != fields.short else "",
                                         f"registered as {registered}" if registered else "") if t)
    try:
        memory_mb = fields.memory_mb
    except ValueError as exc:  # fail loud, in this Pi's Notes: the rest of the page stands
        memory_mb = 0
        notes.append(str(exc))
    stamp = host.machine.last_seen
    try:
        seen = ago_time(stamp)
    except ValueError as exc:  # fail loud, in this Pi's Notes: the rest of the page stands
        seen = ""
        notes.append(f"clock {duration(stamp - now)} ahead" if stamp and stamp > now else str(exc))
    try:
        checked = ago_time(host.checked) if host.checked else ""
    except ValueError as exc:  # a check stamped in the future by the Pi's clock: loud in Notes, the page stands
        checked = ""
        notes.append(f"check {duration(host.checked - now)} ahead" if host.checked > now else str(exc))
    offered, why, page = _offer(host, now, pages)
    _boot = BootMark(*boot_mark(host, timezone.localtime(host.checked).strftime("%-d %b %H:%M") if host.checked else ""))
    seconds = host.machine.last_uptime_s if host.uptime else 0
    link = f"{detail}{host.machine.serial}/#boards" if detail else ""
    return Line(host, STATE_MARKS[host.condition], fields.short, model_title, fields.memory, memory_mb, fields.serial,
                port, port_title, sort_port, _boot,
                # the type alone (Tim, #127: "just the type of FPGA board"; the review of 15 Oct: "Arty A7 A7-35"
                # said the type twice): its variant on hover
                [(b["title"], b["type"], b["label_id"], b["label"]) for b in host.boards]
                or [(kind, "", "", "") for kind in host.registered_kinds or [host.board_type]],
                [check_cell(host, name, link, planned or {}) for name in columns],
                host.uptime, int(seconds or 0), seen, int(stamp.timestamp()) if stamp else 0, checked,
                int(host.checked.timestamp()) if checked else 0, SEVERITY[host.condition],
                offered, "offered" if offered else f"not offered: {why}", notes, page)


def _page(request):
    now = timezone.now()
    rows = fpga.hosts(now)
    table, total, everything = fpga.summary(rows)
    want_condition = request.GET.get("condition", "")
    if want_condition not in CONDITIONS:  # a filter this page does not have is no filter
        want_condition = ""
    want_type = request.GET.get("type", "")[:100]
    pages = _visitor_pages(request)
    fleet = _url(request, "fleet-list")  # each Pi's fleet page, where this host has them (not tinytapeout's urlconf)
    hosts = [r for r in rows if (not want_condition or r.condition == want_condition)
             and (not want_type or r.board_type == want_type)]
    groups = check_groups.grouped(check_names(hosts))
    columns = [name for _, names in groups for name in names]
    # each check's header says its group and the board types that get it (the review of #134: a legend repeated
    # on every header said nothing about the check)
    check_titles = {name: f"{name} ({group}): " + (", ".join(check_groups.types_running(name)) or "no board type the "
                                                   "site knows") + " get it. " + HEAD_TITLES["check"]
                    for group, names in groups for name in names}
    planned = None
    # what needs a person first, then by switch and port (the table's own sort returns to this order)
    observed = fpga.seen_ports()
    shown = sorted((_line(r, now, pages, columns, fleet, observed.get(r.machine.pk), planned) for r in hosts),
                   key=lambda line: (line.severity, line.host.place))
    # the Pis that are here, and below them the missing ones (#127: "Move the missing into a seperate table")
    present = [line for line in shown if line.host.condition != MISSING]
    missing = [line for line in shown if line.host.condition == MISSING]
    # the reload switch keeps the filter
    stay = request.GET.copy()
    stay.pop("reload", None)
    off = stay.copy()
    off["reload"] = "off"
    return render(request, "management/fpgas.html", {
        "summary": table, "total": total, "everything": everything, "rows": shown,
        "tables": [(title, lines) for title, lines in (("", present), ("Missing", missing)) if lines],
        "groups": [(group, len(names), ", ".join(names)) for group, names in groups],
        # a check the docs have no section for says so in its header's title (it sits under "not grouped" too)
        "columns": [(name, check_titles[name] + ("" if check_docs.url(name) else
                                                 " No section on the docs' checks page for it yet."),
                     check_docs.url(name)) for name in columns],
        "boot_check_docs": check_docs.BOOT_CHECK, "check_ran_docs": check_docs.CHECK_RAN,
        "conditions": [(c, fpga.CONDITION_TITLES[c]) for c in CONDITIONS],
        "reload_on": "?" + stay.urlencode(), "reload_off": "?" + off.urlencode(),
        "want_condition": want_condition, "want_type": want_type,
        "filtered": bool(want_condition or want_type),
        "fleet": fleet,
        "head_titles": HEAD_TITLES,
        "zone": localtime.zone_name(),
        "reload": request.GET.get("reload", "") != "off",
    })


def _cameras(machine):
    """The cameras the host's newest registration lists (peripherals.cameras), or None when it lists none at all:
    None is "not said", never "no camera"."""
    doc = machine.latest_snapshot.document if machine.latest_snapshot else None
    peripherals = doc.get("peripherals") if isinstance(doc, dict) else None
    cameras = peripherals.get("cameras") if isinstance(peripherals, dict) else None
    return cameras if isinstance(cameras, list) else None


# The condition as a key for programs (the review of #107): stable words, not the page's titles; "checking" for the
# condition fleet.services calls "unknown" (a check that has not finished this boot), as the page says.
CONDITION_KEYS = {OPERATIONAL: "operational", ATTENTION: "attention", MISSING: "missing", UNKNOWN: "checking"}


def registry_json(request):
    """/fleet/registry.json: every registered host, for programs (the e2e suite's protection check and camera step,
    the coordinator's probe). The page /fleet/ is for people and may change its layout; this is the contract.
    Times are ISO 8601 with their offset (UTC). A field the site does not know is null, never guessed.

    {"site", "generated_at", "machines": [{"serial", "hostname", "label", "port" | null,
      "condition": "operational" | "attention" | "missing" | "checking", "offered",
      "boards": [{"label", "type", "id" | null, "result", "tests": {name: result}}],
      "check": {"result": "pass" | "fail" | "error" | "missing" | "verifying" | "unknown" (unreadable) | ... | null,
                "at" | null},
      "setup_port": true | false,
      "last_seen" | null, "cameras": [as the registration lists them] | null,
      "network": null | {"state", "administrative_state", "changed", "last_ack" | null,
                         "last_ack_source": "pi" | "gateway" | null, "boot_id", "current_boot": true | false,
                         "error": null}
                 | {"state": null, "administrative_state": null, "changed": null, "last_ack",
                    "last_ack_source": "gateway", "boot_id": null, "current_boot": null, "error": null}
                 | {"state": null, "administrative_state": null, "changed": null, "last_ack": null,
                    "last_ack_source": null, "boot_id": null, "current_boot": null, "error": "<what is wrong>"}}]}

    "setup_port" says the machine is registered on the setup / config port (fleet.services.on_setup_port): it is never
    offered and its "check" "result" is "fail" whatever its boards' own results say.

    "network" is the machine's newest network-state record (management.network): the uplink's operational and
    administrative state, when the record was made, and the last DHCP ACK the Pi itself saw; null when the machine has
    sent none (the first form; a machine the gateway has acknowledged is never null). The second form is the
    record's entry; "boot_id" is the boot it was sent in ("" for a record sent without one) and "current_boot" says whether that is the boot the machine is running (if
    not, the machine has sent no record this boot). A malformed record gives the fourth form for that machine only,
    every key null and "error" naming the event, and a warning in the log: the other machines are unaffected.

    "last_ack" is the newer of the Pi's own last DHCP ACK (the record's lease.acked_at) and the gateway's (the newest
    acked_at of its lease records of the machine), "last_ack_source" says which. A machine the gateway has seen
    acknowledged but that has sent no record gets the third form: the same keys, the record's fields null, "last_ack"
    and "last_ack_source": "gateway"."""
    now = timezone.now()
    machines = []
    networks = network.newest()  # one read for every machine
    for host in fpga.hosts(now):
        m = host.machine
        machines.append({
            "serial": m.serial,
            "hostname": m.hostname,
            "label": fpga.pi_label(m),
            "port": fpga.port_text(m) if host.pi else None,
            "condition": CONDITION_KEYS[host.condition],
            "offered": host.condition == OPERATIONAL,
            "boards": [{"label": b["label"], "type": b["title"], "id": b["id"] or None, "result": b["result"],
                        "tests": dict(b["reported_tests"])} for b in host.boards],
            "check": {"result": host.state or None, "at": host.checked.isoformat() if host.checked else None},
            "setup_port": host.setup_port,
            "last_seen": m.last_seen.isoformat() if m.last_seen else None,
            "cameras": _cameras(m),
            "network": network.summary(networks.get(m.serial)),
        })
    return JsonResponse({"site": e2e.site_name(), "generated_at": now.isoformat(), "machines": machines})
