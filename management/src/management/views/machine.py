"""A host's own page (#93), /fleet/<serial>/: what it is, how it is doing, every board its check found, the check in
full with its history, its rpi-hwid labels and who owes what they still need, and its registration as JSON.

Mounted at /fleet/<serial>/ by pib/urls.py (the Pi dashboard, /fleet/, is management's too); the fleet app keeps
the label input download (/fleet/<serial>/rpi-hwid.json)."""

from dataclasses import dataclass

from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.utils.html import format_html
from fleet.models import BootEvent, Machine
from fleet.services import FPGA_STAGES
from fleet.timeago import ago_time, exact

from fleet import hwid, jsonview

from .. import check_groups, fpga, localtime, network, states
from .fpgas import CHECK_WORDS, CONDITION_KEYS, HEAD_TITLES, Mark, _mark, _offer, _port, _visitor_pages, boot_mark, plan
from .index import _url

DOCS = "https://docs.fpgas.online/en/latest/"
# Each board kind's page on the docs site, and its checks' pages (by the check's own test name where the docs have
# a page of that name; else the board's checks page). A kind with no page links nothing.
BOARD_DOCS = {
    "acorn": ("boards/acorn/", "boards/acorn/checks/", {"pin-id": "boards/acorn/checks/pin-id.html",
                                                        "ddr": "boards/acorn/checks/ddr-memory.html"}),
    "arty": ("boards/arty-a7/", "boards/arty-a7/checks/", {}),
    "fomu": ("boards/fomu-evt/", "boards/fomu-evt/checks/", {}),
    "netv2": ("boards/netv2/", "boards/netv2/checks/", {}),
    "tt-fpga": ("boards/tt-fpga/", "boards/tt-fpga/checks/", {}),
    "tt-asic": ("boards/tt-asic/", None, {}),
}
HISTORY = 20  # boot checks listed, newest first
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
    cells: list  # [(Mark, its sort, docs url or "")] a cell per check column
    page: str  # its docs page, or ""
    checks_page: str


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
        docs = BOARD_DOCS.get(_docs_key(b))
        page, checks, per_test = (DOCS + docs[0], DOCS + docs[1] if docs[1] else "", docs[2]) if docs else ("", "", {})
        raw = {check_groups.name(n): n for n, _ in b["reported_tests"]}  # the docs pages use the names as sent
        cells = []
        for name in columns:
            mark, sort = _mark(b, name)
            docs_url = (DOCS + per_test[raw.get(name, name)] if raw.get(name, name) in per_test else checks) \
                if mark.mark != "–" else ""
            cells.append((mark, sort, docs_url))
        result = _result_mark(b["result"], b["reason"])
        boards.append(Board(b["title"], b["type"], b["label_id"], b["label"], result, 2 if b["result"] == "pass" else 0,
                            cells, page, checks))
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


def _history(machine):
    """[Check] of the host's boot checks, newest first, one row a boot: its report, or "running" while it has none."""
    events = BootEvent.objects.filter(machine=machine, stage__in=FPGA_STAGES).order_by("-id")[:HISTORY * 2]
    rows = {}
    for event in events:  # newest first: a boot's report, when it has one, comes before its "running"
        if event.boot_id in rows:
            continue
        detail = event.detail if isinstance(event.detail, dict) else {}
        if event.stage == "fpga-verifying":
            mark, boards = Mark("⟳", "mgmt-test-running", "running"), []
        else:
            mark = _result_mark(str(detail.get("result", "")))
            boards = [(fpga.board_title(b), _result_mark(_t(b["result"]), _t(b.get("reason", ""))))
                      for b in fpga._reported_boards(detail)] if detail else []
        rows[event.boot_id] = Check(event.boot_id, event.boot_id == machine.last_boot_id, _when(event.ts), event.id,
                                    mark, boards)
    return list(rows.values())[:HISTORY]


def _t(value):
    return value if isinstance(value, str) else ""


def _network(entry):
    """(Network | None, error): the host's newest network-state record, or why it cannot be shown (said in the
    section; the page stands)."""
    net = network.of(entry)
    return (None, str(net)) if isinstance(net, ValueError) else (net, "")


def _lifetime(seconds):
    """A lifetime in seconds as the page says it: "forever" for null."""
    return "forever" if seconds is None else f"{seconds} s"


def _exact(moment):
    """A time that may be ahead of now (a lease's expiry) in the active zone, with no "x ago"."""
    return "–" if moment is None else format_html('<time datetime="{}">{}</time>', moment.isoformat(), exact(moment))


def machine(request, serial):
    entry = Machine.objects.select_related("latest_snapshot").filter(serial=serial).first()
    if entry is None:
        raise Http404("no host with this serial has registered")
    with timezone.override(localtime.zone()):
        return _page(request, entry)


def _page(request, entry):
    now = timezone.now()
    host = next((h for h in fpga.hosts(now) if h.machine.pk == entry.pk), None)
    built = hwid.build(entry)
    try:
        missing = sorted((label, fields) for label, fields in hwid.missing(built.document).items() if fields)
    except hwid.label_input.InputError as exc:  # build() makes documents rpi-hwid takes; the last guard (§35)
        missing = [("document", exc.problems)]
    offered, why, page = _offer(host, now, _visitor_pages(request))
    fields = fpga.pi_fields(entry)
    port, port_title, _, port_note = _port(host, fpga.seen_ports().get(entry.pk))
    boot = boot_mark(host, timezone.localtime(host.checked).strftime("%-d %b %H:%M") if host.checked else "")[0]
    groups, boards = _boards(host)
    registered = fpga.registered_model(entry)
    snapshots = list(entry.snapshots.order_by("-first_seen"))
    # this boot's events; a host that has said no boot id yet: its newest events, of any boot
    events = (entry.events.filter(boot_id=entry.last_boot_id).order_by("ts") if entry.last_boot_id
              else entry.events.order_by("-ts")[:EVENTS])
    net, net_error = _network(entry)
    return render(request, "management/machine.html", {
        "machine": entry, "host": host, "net": net, "net_error": net_error,
        "net_changed": _when(net.event.ts) if net and net.event else "",
        "net_addresses": [(a, _lifetime(a.get("valid_lifetime_s")), _lifetime(a.get("preferred_lifetime_s")))
                          for a in net.addresses] if net else [],
        "net_last_ack": _when(net.last_ack) if net else "", "net_expires": _exact(net.expires) if net else "",
        "state_mark": states.MARKS[host.condition],
        "label": fpga.pi_label(entry), "model": fields.short, "model_title": "; ".join(
            t for t in (fields.model if fields.model != fields.short else "",
                        f"registered as {registered}" if registered else "") if t),
        "memory": fields.memory,
        # the port the gateway saw it on, with the time, as on /fleet/ (the review of 15 Oct: the page said "the port
        # its host name says", a remembered name under the same word)
        "port": port, "port_title": port_title, "port_note": port_note,
        "condition": CONDITION_KEYS[host.condition], "boot": boot,
        "offered": offered, "offered_title": "offered" if offered else (
            "not applicable: this host carries no board" if host.no_board_expected else f"not offered: {why}"),
        "page": page,
        "seen": _when(entry.last_seen), "first_seen": _when(entry.first_seen), "checked": _when(host.checked),
        "groups": [(group, len(members)) for group, members in groups], "boards": boards,
        "columns": [name for _, members in groups for name in members],
        "head_titles": HEAD_TITLES, "history": _history(entry),
        # one row per field, the label repeated, so the table sorts (rule 22)
        "labels": [(label, field, *_owner(label, field)) for label, fields in missing for field in fields],
        "notes": built.notes,
        "label_input": _url(request, "fleet-label-input", entry.serial),
        "record": jsonview.render(snapshots[0].document) if snapshots else "",
        "older": [(snap, jsonview.render(snap.document)) for snap in snapshots[1:]],
        "events": [(event, jsonview.compact(event.detail)) for event in events],
        "zone": localtime.zone_name(),
        "fleet": _url(request, "fleet-list"),
    })
