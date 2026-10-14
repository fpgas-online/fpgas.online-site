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

from .. import e2e, fpga, localtime, network
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
class Line:
    """A Pi's row as the page shows it: one value per field, each field its own column (#116)."""
    host: fpga.Host
    label: str  # the Pi as its label names it ("Pi 5 2 GB 285df3f84af242d0"), the label alone
    port: str  # "sw1 p10": the port its host name says it booted on
    check: str  # the boot check's verdict, one word or phrase: "passed", "failed", "found no board"
    boards: list  # each board's label, or the kinds its registration names: one per line in the cell
    uptime: str  # "3 d 4 h", or ""
    sort_uptime: int  # seconds, 0 for none
    seen: str  # <time> html, or "" when the time cannot be shown
    sort_seen: int
    severity: int
    offered: str  # "yes" or "no"
    why_not: str  # in the index's words ("stopped checking in", "pin-id, spiflash"), "" when offered
    notes: list  # short values, one a line: "revision unreadable", "clock 2 h ahead"
    page: str  # the visitor page "yes" links to


@dataclass
class ResultCell:
    """One cell of a Tests table: a mark alone, the result in its title and for a screen reader."""
    mark: str  # "✓" pass, "✗" fail, "–" any other result (skip), "" the board does not run the test
    title: str
    sort: int  # failures first, then other results, then passes, then the boards that do not run it
    cls: str


def _cell(name, result):
    if result is None:
        return ResultCell("", "", 3, "")
    if result == "pass":
        return ResultCell("✓", f"{name}: {result}", 2, "mgmt-test-pass")
    if result == "fail":
        return ResultCell("✗", f"{name}: {result}", 0, "mgmt-test-fail")
    return ResultCell("–", f"{name}: {result}", 1, "mgmt-test-other")


@dataclass
class BoardRow:
    """A board's row of its type's Tests table."""
    host: fpga.Host
    label: str  # the Pi's label
    board: str  # the board's label when the Pi has more than one of this type, else ""
    cells: list
    reason: str


@dataclass
class TestTable:
    title: str  # the board type: "Acorn"
    columns: list  # test names, in the order the checks gave them
    rows: list


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


def _line(host, now, pages):
    label, _, label_note = fpga.pi_label_parts(host.machine)
    notes = [label_note] if label_note else []
    stamp = host.machine.last_seen
    try:
        seen = ago_time(stamp)
    except ValueError as exc:  # fail loud, in this Pi's Notes: the rest of the page stands
        seen = ""
        notes.append(f"clock {duration(stamp - now)} ahead" if stamp and stamp > now else str(exc))
    offered, why_not, page = _offer(host, now, pages)
    seconds = host.machine.last_uptime_s if host.uptime else 0
    return Line(host, label, fpga.port_text(host.machine), _check_word(host),
                [b["label"] for b in host.boards] or host.registered_kinds or [host.board_type], host.uptime, int(seconds or 0), seen, int(stamp.timestamp()) if stamp else 0, SEVERITY[host.condition],
                "yes" if offered else "no", why_not, notes, page)


def _test_tables(lines):
    """One table per board type that has tests: a row per board, a column per test name of that type in the order the
    checks gave them, each cell a mark (blank where the board does not run the test)."""
    types = {}
    for line in lines:
        for b in line.host.boards:
            if b["tests"]:
                types.setdefault(b["title"], []).append((line, b))
    tables = []
    for title, boards in types.items():
        columns = list(dict.fromkeys(name for _, b in boards for name, _ in b["tests"]))
        several = {}
        for line, _ in boards:
            several[line.host.machine.pk] = several.get(line.host.machine.pk, 0) + 1
        rows = [BoardRow(line.host, line.label, b["label"] if several[line.host.machine.pk] > 1 else "",
                         [_cell(name, dict(b["tests"]).get(name)) for name in columns], b["reason"])
                for line, b in boards]
        tables.append(TestTable(title, columns, rows))
    return tables


def _page(request):
    now = timezone.now()
    rows = fpga.hosts(now)
    table, total, everything = fpga.summary(rows)
    want_condition = request.GET.get("condition", "")
    if want_condition not in CONDITIONS:  # a filter this page does not have is no filter
        want_condition = ""
    want_type = request.GET.get("type", "")[:100]
    pages = _visitor_pages(request)
    # what needs a person first, then by switch and port (the table's own sort returns to this order)
    shown = sorted((_line(r, now, pages) for r in rows if (not want_condition or r.condition == want_condition)
                    and (not want_type or r.board_type == want_type)), key=lambda line: (line.severity, line.host.place))
    # the reload switch keeps the filter
    stay = request.GET.copy()
    stay.pop("reload", None)
    off = stay.copy()
    off["reload"] = "off"
    return render(request, "management/fpgas.html", {
        "summary": table, "total": total, "everything": everything, "rows": shown,
        "test_tables": _test_tables(shown),
        "conditions": [(c, fpga.CONDITION_TITLES[c]) for c in CONDITIONS],
        "reload_on": "?" + stay.urlencode(), "reload_off": "?" + off.urlencode(),
        "want_condition": want_condition, "want_type": want_type,
        "filtered": bool(want_condition or want_type),
        # each Pi's fleet page, where this host has them (tinytapeout.fpgas.online's urlconf does not)
        "fleet": _url(request, "fleet-list"),
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
      "last_seen" | null, "cameras": [as the registration lists them] | null,
      "network": null | {"state", "administrative_state", "changed", "last_ack" | null,
                         "last_ack_source": "pi" | "gateway" | null, "boot_id", "current_boot": true | false,
                         "error": null}
                 | {"state": null, "administrative_state": null, "changed": null, "last_ack",
                    "last_ack_source": "gateway", "boot_id": null, "current_boot": null, "error": null}
                 | {"state": null, "administrative_state": null, "changed": null, "last_ack": null,
                    "last_ack_source": null, "boot_id": null, "current_boot": null, "error": "<what is wrong>"}}]}

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
                        "tests": dict(b["tests"])} for b in host.boards],
            "check": {"result": host.state or None, "at": host.checked.isoformat() if host.checked else None},
            "last_seen": m.last_seen.isoformat() if m.last_seen else None,
            "cameras": _cameras(m),
            "network": network.summary(networks.get(m.serial)),
        })
    return JsonResponse({"site": e2e.site_name(), "generated_at": now.isoformat(), "machines": machines})
