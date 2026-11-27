"""A host's own page (#93), /fleet/<serial>/: what it is, how it is doing, every board its check found, the check in
full with its history, its rpi-hwid labels and who owes what they still need, and its registration as JSON.

Mounted at /fleet/<serial>/ by pib/urls.py (the Pi dashboard, /fleet/, is management's too); the fleet app keeps
the label input download (/fleet/<serial>/rpi-hwid.json)."""

import re
from dataclasses import dataclass
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import format_html
from fleet.models import BootEvent, Machine
from fleet.services import FPGA_STAGES, SETUP_PORT_REASON, SETUP_PORT_STATE, on_setup_port
from fleet.timeago import ago_time, exact

from fleet import hwid, jsonview, labelart

from .. import check_docs, check_groups, e2e, fpga, localtime, netconsole, network, states
from . import switches
from .fpgas import (
    CHECK_WORDS,
    CONDITION_KEYS,
    HEAD_TITLES,
    NOT_APPLICABLE,
    NOT_RUN,
    Mark,
    _cameras,
    _mark,
    _offer,
    _port,
    _visitor_pages,
    boot_mark,
    plan,
)
from .index import _url

DOCS = "https://docs.fpgas.online/en/latest/"
NOT_APPLICABLE_SORT = NOT_APPLICABLE[2]
# Two things the page names, said once (Tim, 16 Oct 2026: "Are registrations on the device page the same as the boot
# checks `Boot` value?"). They are not: fleet.models.HardwareSnapshot is a registration, kept once per distinct document
# (its fingerprint) and confirmed by every boot that sends it again; a boot is one power-on, named by the kernel's
# boot id (Machine.last_boot_id, BootEvent.boot_id), and the boot check runs once in each.
REGISTRATION_TITLE = ("a registration: what the Pi says it is (model, serial, memory, MACs, the boards it sees), sent "
                      "by every boot and kept once for each different document; many boots send the same one")
BOOT_TITLE = ("a boot: one power-on of the Pi, named by the id its kernel gives it; the boot check runs once in each, "
              "and each boot sends the registration again")
# Each board kind's page on the docs site. A kind with no page links nothing. A check links its own section of the
# checks page (check_docs, #140), one target a check, from its header and its marks alike.
BOARD_DOCS = {
    "acorn": "boards/acorn/",
    "arty": "boards/arty-a7/",
    "fomu": "boards/fomu-evt/",
    "netv2": "boards/netv2/",
    "tt-fpga": "boards/tt-fpga/",
    "tt-asic": "boards/tt-asic/",
}
HISTORY = 20  # boot checks listed, newest first
# rpi-hwid's note for an event the Pi has not sent ("no usable pi-identified event from this Pi"): the event's name is
# the value in the Labels table, the note its title (the review of #135: a sentence in a cell)
NO_EVENT = re.compile(r"no usable ([a-z-]+) event from this Pi")


def _note_cell(note):
    """The Labels table's value for one of fleet.hwid's notes, the note itself its title: the event it is about (an
    event it lacks, or one it read and could not use, #153: a cell holds a value, not a sentence). A note about no
    event (the registration, an earlier boot, rpi-hwid's refusal of the whole document) is still shown as it is:
    fpgas.online-site#163."""
    if m := NO_EVENT.fullmatch(note):
        return m[1]
    for stage in (hwid.PI_STAGE, hwid.FPGA_STAGE):
        if note.startswith(f"{stage} "):
            return stage
    return note
EVENTS = 50  # events listed for a host that has said no boot id
# Who sends each label field rpi-hwid still needs (#93 item 4: "the page says which stage owes each field").
REGISTRATION_FIELDS = {"model", "serial", "revision", "memory", "macs"}


def _when(moment):
    """<time> html, "" for none, or the reason a time cannot be shown (fail loud, in its cell)."""
    if moment is None:
        return ""
    try:
        return ago_time(moment)
    except ValueError as exc:
        return str(exc)


def _docs_key(board):
    """The BOARD_DOCS key of a reported board: its check module's name, a Tiny Tapeout board by its chip."""
    if board["title"] == "TT chip":
        return "tt-asic"
    if board["title"].startswith("TT"):
        return "tt-fpga"
    return board.get("kind", "")


def _owner(label, field):
    """(short name, its title) of what sends a label field; ("", why) for a field the site does not know a sender
    of: rpi-hwid asks for a field this site's contract (fleet.hwid) has not caught up with, said loud in the cell."""
    if field in REGISTRATION_FIELDS and label == "board":
        return "registration", "the Pi's registration (its fleet agent)"
    if field in hwid.PI_FIELDS:
        return hwid.PI_STAGE, f"the Pi's {hwid.PI_STAGE} event: rpi-hwid on the Pi, through fpgas-verify"
    if field in hwid.FPGA_FIELDS or field in hwid.TT_FIELDS:
        return hwid.FPGA_STAGE, f"the board's {hwid.FPGA_STAGE} event: its check in fpgas-verify"
    return "", f"rpi-hwid asks for {field}, which no event this site reads carries: fleet.hwid is behind rpi-hwid"


@dataclass
class Board:
    """A board's row of the Boards table (#132): the same columns and marks as its row of the Pi table."""
    type: str  # "Acorn", its variant in `full`
    full: str
    ident: str  # the id on its label, its name in `label`
    label: str
    result: Mark  # its result in the boot check of this boot
    result_sort: int
    cells: list  # [(name, Mark, its sort, docs url or "")] a cell per check column
    page: str  # its docs page, or ""
    report: dict = None  # the board as fpga.report_boards() gives it: machine.json's values


# the mark of a board's result in its Boards row and in the history: one character, its words on hover
def _result_mark(result, reason=""):
    # a report with no result says so (its detail is in the events), never "not started" or "unknown"
    word = (CHECK_WORDS.get(result) or f"gave {result}") if result else "sent no result"
    title = word + (f": {reason}" if reason else "")
    if result == "pass":
        return Mark("✓", "mgmt-test-pass", title)
    return Mark("✗", "mgmt-test-fail", title)


def _boards(host):
    """(the check groups, [Board]): every board of this boot, a row each, a column per check its types get, grouped as
    on /fleet/ (Tim, 15 Oct 2026: the per device page "suffers from many of the issues I told you about on other
    pages")."""
    names = list(dict.fromkeys(name for b in host.boards
                               for name in [*plan(b)[0], *(n for n, _ in b["tests"]), *b["not_run"]]))
    groups = check_groups.grouped(names)
    columns = [name for _, members in groups for name in members]
    boards = []
    for b in host.boards:
        page = DOCS + BOARD_DOCS[_docs_key(b)] if _docs_key(b) in BOARD_DOCS else ""
        cells = []
        for name in columns:
            mark, sort = _mark(b, name)
            cells.append((name, mark, sort, check_docs.url(name)))  # the check's own section (#140)
        result = _result_mark(b["result"], b["reason"])
        boards.append(Board(b["title"], b["type"], b["label_id"], b["label"], result, 2 if b["result"] == "pass" else 0,
                            cells, page, b))
    return groups, boards


@dataclass
class Check:
    """A boot's row of the Boot checks table: one row a boot (the review of 15 Oct: a "running" row and a result row
    of the same boot were two rows)."""
    boot: str
    this_boot: bool
    when: str
    sort: int
    mark: Mark
    boards: list  # [(type, Mark)] a board a line
    reports: list  # fpga.report_boards() of its report: what each test of that boot gave
    running: bool
    cells: list = None  # [(name, [Mark], sort)] a cell per check column, a mark per board: set by _history
    at: object = None  # when its event arrived (the Pi's stamp)
    result: str | None = None  # its result as a word ("pass", "fail", "verifying"), None for a report with none
    reason: str | None = None  # why the result is what it is, where the page says more than the word


# Each event stage's kind, coloured on the page, the kind named in its own column (Tim, 16 Oct 2026: "Use color coding
# for the event types"; never colour alone). A stage not listed here is "other", grey, with the stage as sent.
EVENT_KINDS = {
    "network-online": "network", "network-state": "network", "time-synced": "network",
    "ssh-up": "service", "cam-streaming": "service", "tt-daemon-up": "service",
    "pi-identified": "identify", "fpga-board-found": "identify", "fpga-board-identified": "identify",
    "fpga-verifying": "check", "fpga-verified": "check",
    "fpga-test-started": "test", "fpga-test-finished": "test",
}
EVENT_KIND_TITLES = {
    "network": "the Pi's network: link, address, clock",
    "service": "a service on the Pi came up",
    "identify": "what the Pi and its boards are",
    "check": "the boot check started or reported",
    "test": "one test of the boot check started or finished",
    "other": "a stage this page has no kind for: shown as sent",
}


@dataclass
class Event:
    """An Events row: its time, kind (coloured), stage, the test it is about, its result as a mark, and its detail:
    laid out as a registration's document is (jsonview.render, Tim 16 Oct 2026: "The details in the events of this
    boot should be properly formatted like in the registration section.", #154), or "" when the event sent none."""
    ts: object
    kind: str
    stage: str
    test: str
    result: Mark | None
    detail: str
    sent: object = None  # the event's detail as sent
    result_word: str | None = None  # the result `result` marks, as sent
    reason: str | None = None  # its reason, as sent
    test_name: str | None = None  # the test it is about, whole (`test` is cut for its cell)
    id: int = 0  # its BootEvent's: the order of events of one time


def _event(event, render=True):
    """An Events row; `render` False leaves its detail unlaid-out (machine.json sends it as it came)."""
    detail = event.detail if isinstance(event.detail, dict) else {}
    test = _t(detail.get("test", ""))
    result = _t(detail.get("result", ""))
    reason = _t(detail.get("reason", ""))
    marked = bool(result) and event.stage in ("fpga-test-finished", "fpga-verified")
    mark = _result_mark(result, reason) if marked else None
    sent = event.detail if event.detail not in (None, {}) else None
    return Event(event.ts, EVENT_KINDS.get(event.stage, "other"), event.stage, test[:40], mark,
                 jsonview.render(event.detail) if render and sent is not None else "", sent,
                 result if marked else None, reason if marked and reason else None, test or None, event.id)


def _events(entry, limit=None):
    """This boot's events, oldest first; a host that has said no boot id yet: its newest EVENTS, of any boot, newest
    first. `limit`: the newest `limit` of those (machine.json's events=), cut in the query. Events of one time are
    in the order they arrived."""
    if entry.last_boot_id:
        events = entry.events.filter(boot_id=entry.last_boot_id)
        if limit is None:
            return list(events.order_by("ts", "id"))
        return list(events.order_by("-ts", "-id")[:limit])[::-1]
    return list(entry.events.order_by("-ts", "-id")[:EVENTS if limit is None else min(limit, EVENTS)])


RUNNING = Mark("⟳", "mgmt-test-running", "running")
RUNNING_TEST = Mark("⟳", "mgmt-test-running", "the check of this boot is running: no test has reported yet")


def _history(machine):
    """(the check groups, [Check]) of the host's boot checks, newest first, one row a boot: its report, or "running"
    while it has none; each row has a column per check, the marks of that boot's tests as on /fleet/ (Tim, 16 Oct
    2026: "Why doesn't the boot checks table show the tests for that boot?")."""
    events = BootEvent.objects.filter(machine=machine, stage__in=FPGA_STAGES).order_by("-id")[:HISTORY * 2]
    rows = {}
    for event in events:  # newest first: a boot's report, when it has one, comes before its "running"
        if event.boot_id in rows:
            continue
        detail = event.detail if isinstance(event.detail, dict) else {}
        reports = fpga.report_boards(detail) if event.stage != "fpga-verifying" else []
        reason = None
        if event.stage == "fpga-verifying":
            mark, boards, result = RUNNING, [], "verifying"
        else:
            result = str(detail.get("result", "")) or None
            mark = _result_mark(result or "")
            boards = [(b["title"], _result_mark(b["result"], b["reason"])) for b in reports]
        # the boot it is running, on the setup / config port: failed, as the page's head says (fleet.services
        # setup_port_state); its name says where it is now, so an earlier boot elsewhere keeps its own result
        if event.boot_id == machine.last_boot_id and on_setup_port(machine.hostname):
            mark = _result_mark(SETUP_PORT_STATE, SETUP_PORT_REASON)
            result, reason = SETUP_PORT_STATE, SETUP_PORT_REASON
        rows[event.boot_id] = Check(event.boot_id, event.boot_id == machine.last_boot_id, _when(event.ts), event.id,
                                    mark, boards, reports, event.stage == "fpga-verifying", at=event.ts,
                                    result=result, reason=reason)
    checks = list(rows.values())[:HISTORY]
    groups = check_groups.grouped(list(dict.fromkeys(
        name for c in checks for b in c.reports for name in [*plan(b)[0], *(n for n, _ in b["tests"]), *b["not_run"]])))
    columns = [name for _, members in groups for name in members]
    for c in checks:
        c.cells = [_history_cell(c, name) for name in columns]
    return groups, checks


def _history_cell(check, name):
    """(name, [Mark], sort, its docs) of one boot's cell in the column of check `name`: a mark per board, as on /fleet/,
    each linking the check's own section (#140)."""
    if check.running:
        return name, [RUNNING_TEST], NOT_APPLICABLE_SORT, check_docs.url(name)
    if not check.reports:
        return name, [Mark("–", "mgmt-test-none", f"{name} not applicable: no board was checked in this boot")], \
            NOT_APPLICABLE_SORT, check_docs.url(name)
    marks, sorts = zip(*(_mark(b, name) for b in check.reports))
    return name, list(marks), min(sorts), check_docs.url(name)


def _label_images(made, png, pdf):
    """[(title, image url, PDF url)] of the labels rpi-hwid made. The device page is served only where the fleet app's
    label URLs are (pib/urls.py): a label with nowhere to be shown is a fault, said, never a missing picture."""
    if made and not (png and pdf):
        raise ImproperlyConfigured("the device page is served where the label URLs (fleet.urls) are not")
    return [(m.title, f"{png}?{urlencode({'id': m.id})}", f"{pdf}?{urlencode({'id': m.id})}") for m in made]


def _t(value):
    return value if isinstance(value, str) else ""


def _network(entry):
    """(Network | None, error): the host's newest network-state record, or why it cannot be shown (said in the
    section; the page stands)."""
    net = network.of(entry)
    return (None, str(net)) if isinstance(net, ValueError) else (net, "")


def _console(entry):
    """(Console | None, error, not set up) of the host's kernel console this boot, or why it cannot be shown (said in
    the section; the page stands). `not set up`: the site has no receiver yet, a state of the site said plainly, not a
    fault drawn as one."""
    try:
        return netconsole.read(entry), "", ""
    except netconsole.NotSetUp as exc:
        return None, "", str(exc)
    except netconsole.Unavailable as exc:
        return None, str(exc), ""
    except ValueError as exc:  # a stored line not in the receiver's form: the receiver's fault, said loud
        return None, f"the netconsole file holds a line this page cannot read: {exc}", ""


def _lifetime(seconds):
    """A lifetime in seconds as the page says it: "forever" for null."""
    return "forever" if seconds is None else f"{seconds} s"


def _exact(moment):
    """A time that may be ahead of now (a lease's expiry) in the active zone, with no "x ago"."""
    return "–" if moment is None else format_html('<time datetime="{}">{}</time>', moment.isoformat(), exact(moment))


def _entry(serial):
    return Machine.objects.select_related("latest_snapshot").filter(serial=serial).first()


def machine(request, serial):
    entry = _entry(serial)
    if entry is None:
        raise Http404(UNKNOWN)
    with timezone.override(localtime.zone()):
        return _page(request, _facts(request, entry))


UNKNOWN = "no host with this serial has registered"


@dataclass
class Facts:
    """Everything the device page shows of one host, built once by _facts: the page draws it (_page) and
    machine.json returns it (_document), so the two cannot drift."""
    entry: Machine
    now: object
    host: fpga.Host
    notes: list  # fleet.hwid's notes of the label input
    missing: list  # [(label, [field])] rpi-hwid still needs
    label_images: list  # [(title, image url, PDF url)]
    refused: list  # [(kind, exception)]
    offered: bool
    why: str  # why it is not offered, "" when it is
    visitor_page: str  # its visitor page, when offered
    fields: fpga.PiFields
    registered: str  # the model its registration sent, ""
    observed: object  # fpga.seen_ports()'s observation of it, None for none
    port: str
    port_title: str
    port_note: str
    groups: list
    boards: list  # [Board]
    history_groups: list
    history: list  # [Check], newest first
    snapshots: list  # [HardwareSnapshot], newest first
    events: list  # [Event], in the page's order: oldest first for this boot, newest first with no boot id
    net: object  # network.Network | None
    net_error: str
    console: object  # netconsole.Console | None
    console_error: str
    console_not_set_up: str
    switch_port: dict  # switches.port_of()


def _facts(request, entry, events_limit=None, page=True):
    """The Facts of one host. `page` False (machine.json): nothing laid out as the page's HTML; `events_limit` cuts
    the events in their query."""
    now = timezone.now()
    host = next((h for h in fpga.hosts(now) if h.machine.pk == entry.pk), None)
    built = hwid.build(entry)
    try:
        missing = sorted((label, fields) for label, fields in hwid.missing(built.document).items() if fields)
        made, refused = labelart.made(built.document)
    except hwid.label_input.InputError as exc:  # build() makes documents rpi-hwid takes; the last guard (§35)
        missing, made, refused = [("document", exc.problems)], [], []
    png, pdf = _url(request, "fleet-label-png", entry.serial), _url(request, "fleet-label-pdf", entry.serial)
    offered, why, visitor_page = _offer(host, now, _visitor_pages(request))
    observed = fpga.seen_ports().get(entry.pk)
    port, port_title, _, port_note = _port(host, observed)
    groups, boards = _boards(host)
    history_groups, history = _history(entry)
    net, net_error = _network(entry)
    console, console_error, console_not_set_up = _console(entry)
    return Facts(entry, now, host, built.notes, missing, _label_images(made, png, pdf), refused, offered, why,
                 visitor_page, fpga.pi_fields(entry), fpga.registered_model(entry), observed, port, port_title,
                 port_note, groups, boards, history_groups, history, list(entry.snapshots.order_by("-first_seen")),
                 [_event(event, page) for event in _events(entry, events_limit)], net, net_error, console, console_error, console_not_set_up,
                 switches.port_of(request, entry.serial))


def _refusals(facts):
    """A refusal other than a missing field (the fields are the Labels rows): rpi-hwid's own words."""
    return [(kind, str(exc)) for kind, exc in facts.refused if not isinstance(exc, labelart.labels.MissingFieldsError)]


def _page(request, facts):
    entry, host, fields, net, console, groups = (facts.entry, facts.host, facts.fields, facts.net, facts.console,
                                                 facts.groups)
    history_groups = facts.history_groups
    boot = boot_mark(host, timezone.localtime(host.checked).strftime("%-d %b %H:%M") if host.checked else "")[0]
    return render(request, "management/machine.html", {
        # its switch port's row, as the switch port dashboard draws it, with the PoE and link buttons (round 6 item 6)
        "switch_port": facts.switch_port, "switch_head_titles": switches.head_titles(),
        "refresh_seconds": switches.REFRESH_SECONDS, "power_url": switches.POWER_URL, "link_url": switches.LINK_URL,
        "switches_json_url": _url(request, "management-switches-json"),
        "switches_url": _url(request, "management-switches"),
        "machine": entry, "host": host, "net": net, "net_error": facts.net_error,
        "net_changed": _when(net.event.ts) if net and net.event else "",
        "net_addresses": [(a, _lifetime(a.get("valid_lifetime_s")), _lifetime(a.get("preferred_lifetime_s")))
                          for a in net.addresses] if net else [],
        "net_last_ack": _when(net.last_ack) if net else "", "net_expires": _exact(net.expires) if net else "",
        "state_mark": states.MARKS[host.condition],
        "label": fpga.pi_label(entry), "model": fields.short, "model_title": "; ".join(
            t for t in (fields.model if fields.model != fields.short else "",
                        f"registered as {facts.registered}" if facts.registered else "") if t),
        "memory": fields.memory,
        # the port the gateway saw it on, with the time, as on /fleet/ (the review of 15 Oct: the page said "the port
        # its host name says", a remembered name under the same word)
        "port": facts.port, "port_title": facts.port_title, "port_note": facts.port_note,
        "condition": CONDITION_KEYS[host.condition], "boot": boot,
        "offered": facts.offered, "offered_title": "offered" if facts.offered else f"not offered: {facts.why}",
        "page": facts.visitor_page,
        "seen": _when(entry.last_seen), "first_seen": _when(entry.first_seen), "checked": _when(host.checked),
        "groups": [(group, len(members)) for group, members in groups], "boards": facts.boards,
        "columns": [name for _, members in groups for name in members],
        "head_titles": HEAD_TITLES, "history": facts.history, "boot_check_docs": check_docs.BOOT_CHECK,
        "check_ran_docs": check_docs.CHECK_RAN,
        "column_docs": {name: check_docs.url(name) for name in
                        [n for _, members in groups for n in members] + [n for _, m in history_groups for n in m]},
        "history_groups": [(group, len(members)) for group, members in history_groups],
        "history_columns": [name for _, members in history_groups for name in members],
        # one row per field, the label repeated, so the table sorts (rule 22)
        "labels": [(label, field, *_owner(label, field)) for label, fields in facts.missing for field in fields],
        "notes": [(_note_cell(note), note) for note in facts.notes],
        "label_input": _url(request, "fleet-label-input", entry.serial),
        # the labels rpi-hwid makes from what the Pi sent, as images linking the PDF it prints (#141)
        "label_images": facts.label_images,
        "label_refusals": _refusals(facts),
        "registrations": [(snap.fingerprint, snap.pk == entry.latest_snapshot_id, _when(snap.first_seen),
                           int(snap.first_seen.timestamp()),
                           _when(snap.last_confirmed), int(snap.last_confirmed.timestamp()),
                           jsonview.render(snap.document)) for snap in facts.snapshots],
        "registration_title": REGISTRATION_TITLE, "boot_title": BOOT_TITLE,
        "events": facts.events, "event_kind_titles": EVENT_KIND_TITLES,
        "zone": localtime.zone_name(),
        "fleet": _url(request, "fleet-list"),
        # the kernel's own log of this boot, from the gateway's netconsole receiver (round 6 item 5)
        "console": console, "console_error": facts.console_error, "console_not_set_up": facts.console_not_set_up,
        "console_since": _when(console.since) if console and console.since else "",
        # the receiver's oldest stored line, said when it came after this boot began (round 7)
        "console_receiving_since": _exact(console.receiving_since) if console and console.receiving_since else "",
        "console_receiver_late": bool(console and console.receiver_late),
        "console_lines": [(timezone.localtime(at).strftime("%H:%M:%S.%f")[:-3], at.isoformat(), text)
                          for at, text in console.lines] if console else [],
    })


# --- /fleet/<serial>/machine.json: the page as data -----------------------------------------------------------------

# A check cell's result as a word where the board reported no result for the check (its mark's class says which)
CELL_WORDS = {NOT_RUN[1]: "not_run", NOT_APPLICABLE[1]: "not_applicable"}
LIMITS = ("events", "console")
MAX_LIMIT = 10 ** 6  # events= and console= above this are this


def _null(value):
    return value if value not in ("", None) else None


def _absolute(url):
    """A page's address as the site's own, https (DOMAIN_NAME, as views.fpgas._visitor_pages builds the /fpgas/
    links): behind nginx the request says http. An address that is already absolute is kept."""
    if not url:
        return None
    return url if url.startswith(("https://", "http://")) else f"https://{settings.DOMAIN_NAME}{url}"


def _test(board, name, mark):
    """{"result", "reason"} of one check cell: the result the board's check reported for it as sent ("pass",
    "fail", ...), else "not_run" or "not_applicable"; the reason the cell's title gives, in words."""
    reported = dict(board["tests"]).get(name)
    return {"result": reported if reported is not None else CELL_WORDS[mark.cls], "reason": mark.title}


def _limit(request, name):
    """The count a query parameter asks for, None for all; ValueError for one that is not a whole number."""
    value = request.GET.get(name)
    if value is None:
        return None
    if not value.isdigit():
        raise ValueError(f"{name} must be a whole number of 0 or more, not {value[:20]!r}")
    return min(int(value), MAX_LIMIT)  # a database counts no further than 2**63 - 1: past this is all


def _network_document(net):
    if net is None:
        return None
    event, doc = net.event, net.doc
    return {
        "state": net.operational_state, "administrative_state": net.administrative_state,
        "interface": doc.get("interface"), "mac": doc.get("mac"),
        "changed": network.utc(event.ts) if event else None,
        "recorded": network.utc(parse_datetime(doc["at"])) if doc.get("at") else None,
        "last_ack": network.utc(net.last_ack), "last_ack_source": net.last_ack_source,
        "expires": network.utc(net.expires),
        "boot_id": _null(event.boot_id) if event else None, "current_boot": net.current_boot,
        "addresses": [{k: a[k] for k in network.ADDRESS_KEYS} for a in net.addresses],
        "default_routes": [dict(r) for r in net.default_routes],
        "lease": dict(net.lease) if event else None,
    }


def _console_document(console, limit):
    if console is None:
        return None
    lines = console.lines if limit is None else console.lines[max(0, len(console.lines) - limit):] if limit else []
    return {"since": network.utc(console.since), "receiving_since": network.utc(console.receiving_since),
            "receiver_late": bool(console.receiver_late), "address": console.address, "count": console.count,
            "partial": bool(console.partial), "lines": [{"at": network.utc(at), "text": text} for at, text in lines]}


def _document(request, facts, console_limit=None):
    """machine.json's document of Facts (the contract: machine_json)."""
    entry, host, fields = facts.entry, facts.host, facts.fields
    history_columns = [name for _, members in facts.history_groups for name in members]
    port_row = facts.switch_port.get("row")
    return {
        "site": e2e.site_name(), "generated_at": network.utc(facts.now), "serial": entry.serial,
        "page": _absolute(_url(request, "fleet-detail", entry.serial)),
        "machine": {
            "hostname": _null(entry.hostname), "label": fpga.pi_label(entry), "model": _null(fields.short),
            "model_full": _null(fields.model), "registered_model": _null(facts.registered),
            "memory": _null(fields.memory),
            "port": _null(facts.port), "port_seen_at": network.utc(facts.observed.at) if facts.observed else None,
            "port_note": _null(facts.port_note),
            "condition": CONDITION_KEYS[host.condition], "condition_reason": _null(host.why),
            "check": host.state or None,
            "offered": facts.offered, "offered_reason": None if facts.offered else facts.why,
            "board_page": _absolute(facts.visitor_page),
            "online": bool(entry.online),
            "first_seen": network.utc(entry.first_seen), "last_seen": network.utc(entry.last_seen),
            "checked": network.utc(host.checked),
            "uptime_s": entry.last_uptime_s if host.uptime else None,
            "boot_id": _null(entry.last_boot_id), "setup_port": host.setup_port, "cameras": _cameras(entry),
        },
        "boards": [{
            "type": b.type, "variant": _null(b.report["variant"]), "id": _null(b.report["id"]), "label": _null(b.label),
            "result": _null(b.report["result"]), "reason": _null(b.report["reason"]),
            "tests": {name: _test(b.report, name, mark) for name, mark, _, _ in b.cells},
            "docs": _null(b.page),
        } for b in facts.boards],
        "boot_checks": [{
            "boot_id": c.boot, "this_boot": c.this_boot, "at": network.utc(c.at), "running": c.running,
            "result": c.result, "reason": c.reason,
            "boards": [{"type": b["title"], "result": _null(b["result"]), "reason": _null(b["reason"]),
                        "tests": {name: _test(b, name, _mark(b, name)[0]) for name in history_columns}}
                       for b in c.reports],
        } for c in facts.history],
        "labels": {
            "missing": [{"label": label, "field": field, "sent_by": _null(owner), "sent_by_title": title}
                        for label, fields_ in facts.missing for field in fields_
                        for owner, title in [_owner(label, field)]],
            "notes": list(facts.notes),
            "refusals": [{"kind": kind, "text": text} for kind, text in _refusals(facts)],
            "images": [{"title": title, "png": _absolute(png), "pdf": _absolute(pdf)}
                       for title, png, pdf in facts.label_images],
            "label_input": _absolute(_url(request, "fleet-label-input", entry.serial)),
        },
        "network": _network_document(facts.net),
        "network_error": _null(facts.net_error),
        # the port's row exactly as /management/switches.json has it
        "switch_port": port_row,
        "switch_port_error": None if port_row else _null(facts.switch_port.get("problem")),
        "registrations": [{"fingerprint": snap.fingerprint, "current": snap.pk == entry.latest_snapshot_id,
                           "first_seen": network.utc(snap.first_seen), "last_confirmed": network.utc(snap.last_confirmed),
                           "document": snap.document} for snap in facts.snapshots],
        "events": [{"ts": network.utc(e.ts), "kind": e.kind, "stage": e.stage, "test": e.test_name,
                    "result": e.result_word, "reason": e.reason, "detail": e.sent} for e in facts.events],
        "console": _console_document(facts.console, console_limit),
        "console_error": _null(facts.console_error),
        "console_not_set_up": _null(facts.console_not_set_up),
    }


def machine_json(request, serial):
    """/fleet/<serial>/machine.json: everything the host's own page (/fleet/<serial>/) shows, for programs (the
    fpgas.online MCP). The page is for people and may change its layout; this is the contract, as /fleet/registry.json
    is for the list. It comes from the page's own Facts (_facts), so the two cannot drift. Times are ISO 8601 with
    their offset (UTC). A field the site does not know is null, never guessed. Keys are stable words, not the page's
    titles; marks (✓ ✗ ⟳) never appear, only result words. An unknown serial is 404
    {"error": "no host with this serial has registered"}; a limit that is not a whole number is 400 {"error"}.

    Query parameters (optional, for size): events=<n> keeps the newest n events, console=<n> the newest n console
    lines (default: all, as the page). Unknown parameters are ignored.

    {"site", "generated_at", "serial", "page": the page's absolute address,
     "machine": {"hostname" | null, "label", "model" | null (short: "Pi 3B+"), "model_full" | null (the label's:
                 "Pi 3 Model B+"), "registered_model" | null (as its registration sent it), "memory" | null ("4 GB"),
                 "port" | null ("sw2 p46": the port the gateway last leased it an address on), "port_seen_at" | null,
                 "port_note" | null, "condition": "operational" | "attention" | "missing" | "checking",
                 "condition_reason" | null, "check": the boot check of this boot ("pass" | "fail" | "verifying" | ...)
                 | null (not started), "offered": bool, "offered_reason": null | why not, "board_page": null | its
                 visitor page, "online": bool (its last status), "first_seen", "last_seen" | null, "checked" | null,
                 "uptime_s" | null (null once it stops checking in), "boot_id" | null, "setup_port": bool,
                 "cameras": [as the registration lists them] | null},
     "boards": [{"type", "variant" | null, "id" | null, "label" | null, "result" | null, "reason" | null,
                 "tests": {check: {"result", "reason"}}, "docs" | null}],         this boot's boards
     "boot_checks": [{"boot_id", "this_boot": bool, "at", "running": bool, "result" | null, "reason" | null,
                      "boards": [{"type", "result" | null, "reason" | null,
                                  "tests": {check: {"result", "reason"}}}]}],
                                                                                 newest first, at most HISTORY
     "labels": {"missing": [{"label", "field", "sent_by" | null, "sent_by_title"}], "notes": [text],
                "refusals": [{"kind", "text"}], "images": [{"title", "png", "pdf"}], "label_input"},
     "network": null | {"state", "administrative_state", "interface", "mac", "changed", "recorded", "last_ack",
                        "last_ack_source": "pi" | "gateway" | null, "expires", "boot_id", "current_boot",
                        "addresses": [{"family", "address", "prefixlen", "scope", "valid_lifetime_s",
                                       "preferred_lifetime_s"}],
                        "default_routes": [{"family", "gateway", "metric", "protocol"}],
                        "lease": null | {"address", "server", "lifetime_s", "acked_at", "expires"}},
     "network_error": null | why the record cannot be shown,
     "switch_port": null | the port's row exactly as /management/switches.json has it,
     "switch_port_error": null | why there is no row (the switches cannot be read, or it is on no port they read),
     "registrations": [{"fingerprint", "current": bool, "first_seen", "last_confirmed", "document": as sent}],
     "events": [{"ts", "kind", "stage", "test" | null, "result" | null, "reason" | null, "detail": as sent | null}],
     "console": null | {"since" | null, "receiving_since" | null, "receiver_late": bool, "address", "count",
                        "partial": bool, "lines": [{"at", "text"}]},
     "console_error": null | text, "console_not_set_up": null | text}

    A test's "result" is what the board's check reported for it, as sent ("pass", "fail", ...), else "not_run" (a
    check its type gets that did not run) or "not_applicable"; its "reason" is the page's words for the cell. A boot
    check's "result" is its report's ("verifying" while it runs; "fail" with reason "setup port" for the boot it is
    running on the setup port, as the page says); null for a report with no result. An event's "result" and "reason"
    are those of a test or check result (fpga-test-finished, fpga-verified), null for other stages.

    "network" is null when the host has sent no network-state record and the gateway has acknowledged it no lease; a
    host known only from the gateway's ack has the record's fields null, "addresses" and "default_routes" empty,
    "lease" null, and "last_ack" the gateway's. "changed" is when the record arrived (as registry.json's), "recorded"
    the record's own time ("at"). Times the host stamped (events, checks) are the Pi's clock."""
    entry = _entry(serial)
    if entry is None:
        return JsonResponse({"error": UNKNOWN}, status=404)
    try:
        events_limit, console_limit = (_limit(request, name) for name in LIMITS)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    with timezone.override(localtime.zone()):
        facts = _facts(request, entry, events_limit, page=False)
        return JsonResponse(_document(request, facts, console_limit), json_dumps_params={"ensure_ascii": False})
