"""The management index: the state of this installation at a glance (Tim, 11 Oct 2026).

One table, rows grouped by area, failures first (severity, then area). Each row is a short value with a link to the
page that shows the detail. The areas:

* Boards: a row per Pi that is not offered (fleet.services.condition, the rule the FPGA hosts page uses), then one
  row for the offered ones;
* Switches: per switch, the ports that are not as the inventory says (management.inventory_check), or one row saying
  it is as the inventory says; a switch that does not answer is a failure row;
* Boot checks: the newest check across the fleet, and how many Pis' newest check failed;
* Browser test: the newest completed run of the e2e workflow for this site (management.e2e).

Fail loud: a value the code does not know (a condition, a check name, a boot check result, a time in the future) is an
exception with the reason; the exception becomes a failure row naming it, never an "unknown" cell. An area whose
data cannot be read at all is one failure row for the area."""

import logging
import re
from dataclasses import dataclass

from django.db.models import Max
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from fleet.models import BootEvent
from fleet.services import ATTENTION, CONDITIONS, MISSING, OPERATIONAL, UNKNOWN
from fleet.timeago import ago_time, duration

from .. import e2e, fpga, inventory_check, localtime
from . import switches as switch_page

logger = logging.getLogger(__name__)

# State words (a word, or nothing to explain): the first column.
OK, FAILING, MISSING_WORD, WAITING = "ok", "failing", "missing", "waiting"
# Severity: the order of the failures at the top; the ok rows follow all of them.
ERROR, SWITCH_DOWN, BOARD_MISSING, BOARD_FAILING, SWITCH_PORTS, CHECK_FAILED, E2E_FAILED, BOARD_WAITING, FINE = range(9)
AREAS = ("Boards", "Switches", "Boot checks", "Browser test")
# The names of a board's tests ("sdk", "dip-switches", "pin-id", "uart"): a word the code can show as it is.
CHECK_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,39}")
# Results of a boot check (fpga-verified detail["result"]) that mean the Pi is not offered and why.
BAD_RESULTS = {"fail": "failed", "error": "could not finish", "missing": "found no board", "unknown": "unreadable"}
MAX_VALUE = 200  # characters of a row's value; the rest is in its title
TITLE_PORTS = 20  # failing ports named in a switch row's title


@dataclass
class Row:
    severity: int
    state: str
    area: str
    item: str
    value: str
    link: str = ""
    title: str = ""
    seen: str = ""  # <time> html from fleet.timeago.ago_time, or ""
    sort_seen: int = 0  # the time as a number, for the sortable column

    @property
    def failing(self):
        return self.severity < FINE

    @property
    def css(self):
        return {OK: "operational", FAILING: "attention", MISSING_WORD: "missing", WAITING: "unknown"}[self.state]


def _url(request, name, *args):
    """The page's address on this host, or "" when it does not exist here (not built yet, or not on this host)."""
    try:
        return reverse(name, args=args, urlconf=getattr(request, "urlconf", None))
    except NoReverseMatch:
        return ""


def _short(text):
    return text if len(text) <= MAX_VALUE else text[:MAX_VALUE - 1] + "…"


def _row(severity, state, area, item, value, link="", title="", moment=None):
    return Row(severity, state, area, item, _short(value), link, title or (value if len(value) > MAX_VALUE else ""),
               ago_time(moment), int(moment.timestamp()) if moment else 0)


def _error_row(area, exc, link=""):
    """An area (or one item of it) that could not be worked out: a failure row naming the reason."""
    reason = f"{type(exc).__name__}: {exc}" if not isinstance(exc, (e2e.E2EError, switch_page._Unavailable)) else str(exc)
    return _row(ERROR, FAILING, area, "this page", reason, link, reason)


# --- Boards ---------------------------------------------------------------------------------------------------


def _board_reason(host, now):
    """The short reason a Pi is not offered, as a value; ValueError for what the code does not know."""
    if host.condition not in CONDITIONS:
        raise ValueError(f"unknown condition {host.condition!r}")
    if host.condition == MISSING:
        return f"missing {duration(now - host.machine.last_seen)}"
    if host.condition == UNKNOWN:
        if host.state == "verifying":
            return "boot check running"
        if host.state == "":
            return "boot check not started"
        raise ValueError(f"waiting with boot check result {host.state!r}")
    if host.condition != ATTENTION:
        raise ValueError(f"condition {host.condition!r} is not a reason")
    if not host.checked_in:
        return "stopped checking in"
    names = list(dict.fromkeys(name for board in host.boards for name in board["failing"]))
    for name in names:
        if not CHECK_NAME.fullmatch(name):
            raise ValueError(f"check name {name!r} is not one the page knows how to show")
    if names:
        return ", ".join(names)
    if host.state not in BAD_RESULTS:
        raise ValueError(f"boot check result {host.state!r} is not one the page knows")
    return f"boot check {BAD_RESULTS[host.state]}"


def _boards(request, now):
    rows, offered = [], 0
    for host in fpga.hosts(now):
        name = host.machine.hostname.split(".")[0] or host.machine.serial
        link = _url(request, "fleet-detail", host.machine.serial)
        item = f"{name}: {host.board_type}"
        try:
            if host.condition == OPERATIONAL:
                offered += 1
                continue
            reason = _board_reason(host, now)
        except ValueError as exc:
            rows.append(_row(ERROR, FAILING, "Boards", item, str(exc), link, str(exc), host.machine.last_seen))
            continue
        severity, state = {MISSING: (BOARD_MISSING, MISSING_WORD), UNKNOWN: (BOARD_WAITING, WAITING)}.get(
            host.condition, (BOARD_FAILING, FAILING))
        rows.append(_row(severity, state, "Boards", item, reason, link, host.why, host.machine.last_seen))
    everything = _url(request, "fleet-list") or _url(request, "management-fpgas")
    word = "board" if offered == 1 else "boards"
    rows.append(_row(FINE if offered else BOARD_FAILING, OK if offered else FAILING, "Boards", "offered",
                     f"{offered} {word} offered", everything,
                     "Pis that check in and whose boot check of this boot passed"))
    return rows


# --- Switches -------------------------------------------------------------------------------------------------


def _switches(request, now):
    page = _url(request, "management-switches")
    try:
        views = sorted(switch_page._read(), key=lambda v: (v.index is None, v.index or 0))
    except switch_page._Unavailable as exc:
        return [_row(SWITCH_DOWN, FAILING, "Switches", "all switches", exc.message, page, exc.message)]
    inventory = switch_page._inventory()
    rows = []
    for view in views:
        number = f"Switch {view.index}" if view.index is not None else "Switch"
        link = f"{page}#switch-{view.index}" if page and view.index is not None else page
        if not view.reachable:
            reason = view.error or "not answering"
            rows.append(_row(SWITCH_DOWN, FAILING, "Switches", number, f"not answering: {reason}", link, reason))
            continue
        spec = switch_page._spec(inventory, view.index)
        if spec is None or spec is switch_page.NO_INVENTORY:
            why = "the switch inventory does not describe this switch, so its ports cannot be checked"
            rows.append(_row(SWITCH_PORTS, FAILING, "Switches", number, "not in the inventory", link, why))
            continue
        failures = inventory_check.check(spec, view)
        if not failures:
            rows.append(_row(FINE, OK, "Switches", number, "as the inventory says", link,
                             "board ports have their own PVID and are untagged in it only; the gateway trunk "
                             "carries every board VLAN"))
            continue
        shown = "; ".join(f.text for f in failures[:TITLE_PORTS])
        title = shown + (f"; and {len(failures) - TITLE_PORTS} more" if len(failures) > TITLE_PORTS else "")
        causes = sorted({inventory_check.KNOWN_CAUSES[f.vlan] for f in failures if f.vlan in inventory_check.KNOWN_CAUSES})
        rows.append(_row(SWITCH_PORTS, FAILING, "Switches", number,
                         f"{len(failures)} not as the inventory says, first: {failures[0].text}", link,
                         "; ".join([title, *causes])))
    return rows


# --- Boot checks ----------------------------------------------------------------------------------------------


def _newest_checks():
    """[(machine, event)] the newest `fpga-verified` event of each machine (by arrival, as fleet.services does), in
    two queries however many machines there are."""
    newest = BootEvent.objects.filter(stage="fpga-verified").values("machine_id").annotate(last=Max("id")) \
        .values_list("last", flat=True)
    return [(event.machine, event) for event in
            BootEvent.objects.filter(id__in=list(newest)).select_related("machine").order_by("id")]


def _result(event):
    detail = event.detail
    if not isinstance(detail, dict) or not isinstance(detail.get("result"), str) or not detail["result"]:
        raise ValueError(f"the boot check event {event.id} of {event.machine.hostname or event.machine.serial} "
                         "has no result")
    return detail["result"]


def _boot_checks(request, now):
    checks = _newest_checks()
    if not checks:
        raise ValueError("no boot check has been reported by any Pi")
    results = [(machine, event, _result(event)) for machine, event in checks]
    machine, event, result = results[-1]  # the newest by arrival
    name = machine.hostname.split(".")[0] or machine.serial
    failed = sorted(m.hostname.split(".")[0] or m.serial for m, _, r in results if r != "pass")
    when = event.ts
    return [
        _row(FINE if result == "pass" else CHECK_FAILED, OK if result == "pass" else FAILING, "Boot checks",
             "newest check", f"{name}: {result}", _url(request, "fleet-detail", machine.serial), "", when),
        _row(CHECK_FAILED if failed else FINE, FAILING if failed else OK, "Boot checks", "Pis whose newest check failed",
             f"{len(failed)}: {', '.join(failed)}" if failed else "0", _url(request, "management-fpgas")),
    ]


# --- Browser test ---------------------------------------------------------------------------------------------


def _browser_test(request, now):
    site = e2e.site_name()
    item = f"{site} shared job of {e2e.WORKFLOW}"  # which job and site the row reads (the coordinator, 11 Oct)
    try:
        run = e2e.latest(site)
    except e2e.E2EError as exc:
        return [_row(E2E_FAILED, FAILING, "Browser test", item, f"cannot be read: {exc}", e2e.WORKFLOW_URL, str(exc))]
    passed = run["result"] == "passed"
    return [_row(FINE if passed else E2E_FAILED, OK if passed else FAILING, "Browser test", item, run["result"],
                 run["url"], "", run["completed_at"])]


# --- The page -------------------------------------------------------------------------------------------------

LINKS = (("Switch ports", "management-switches"), ("FPGA hosts", "management-fpgas"),
         ("Visitor and usage stats", "management-stats"), ("Fleet registry", "fleet-list"))
BUILDERS = (("Boards", _boards), ("Switches", _switches), ("Boot checks", _boot_checks),
            ("Browser test", _browser_test))


def rows(request, now=None):
    """Every row of the page, failures first: by severity, then by area, then as the area gave them."""
    now = now or timezone.now()
    found = []
    for area, build in BUILDERS:
        try:
            found.extend(build(request, now))
        except Exception as exc:  # the area's data could not be worked out: a failure row, never a silent gap
            logger.exception("management index: %s could not be worked out", area)
            found.append(_error_row(area, exc))
    return sorted(found, key=lambda r: (r.severity, AREAS.index(r.area)))


def index(request):
    # every time on the page in the installation's own zone (override, not activate: the worker's next request keeps
    # the site's own zone)
    with timezone.override(localtime.zone()):
        table = rows(request)
    links = [(title, url) for title, name in LINKS if (url := _url(request, name))]
    return render(request, "management/index.html", {"rows": table, "links": links})
