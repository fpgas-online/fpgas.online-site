"""The Raspberry Pi dashboard (#68, #94): every registered Pi with its board, its boot check and how it is doing,
and a summary by board type, from the fleet registry, by the one rule that offers a Pi (fleet.services.is_offered).

One page of the Pis (Tim, 9 Oct 2026: "two dashboards of the same Pis"): it is /fleet/ where the host has the fleet
pages (pib/urls.py mounts it there as fleet-list), and /management/fpgas/ sends a browser there. On a host without
the fleet pages (tinytapeout.fpgas.online's urlconf) /management/fpgas/ is the page itself."""

from dataclasses import dataclass

from django.conf import settings
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import Resolver404, resolve
from django.utils import timezone
from fleet.services import ATTENTION, CONDITIONS, MISSING, OPERATIONAL, UNKNOWN
from fleet.timeago import ago_time
from pibfpgas.pis import listed
from ttsite.middleware import serves_ttsite

from .. import fpga, localtime
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
    """A Pi's row as the page shows it."""
    host: fpga.Host
    label: str  # the Pi as its label names it
    model: str  # the model string its registration sent, in full (the label's title, #92)
    port: str  # "sw1 p10": the port its host name says it booted on
    check: str  # "failed: pin-id, spiflash"
    check_title: str  # every test and its result, and the check's reasons
    seen: str  # <time> html, or "" when the time cannot be shown
    seen_error: str  # why it cannot (a time further ahead than clocks differ): shown in the cell
    sort_seen: int
    severity: int
    offered: str  # "yes", or "no: <why>" in the index's words ("stopped checking in", "dip-switches")
    page: str  # the visitor page "yes" links to


def _check(host):
    word = CHECK_WORDS.get(host.state)
    if word is None:  # a result the page does not know: shown as it came, not as "unknown"
        word = f"gave {host.state}"
    failing = list(dict.fromkeys(name for b in host.boards for name in b["failing"]))
    tests = "; ".join(" ".join(f"{name}={result}" for name, result in b["tests"]) for b in host.boards if b["tests"])
    reasons = "; ".join(b["reason"] for b in host.boards if b["reason"])
    return (f"{word}: {', '.join(failing)}" if failing else word), "; ".join(t for t in (tests, reasons) if t)


def _offered(host, now, pages):
    """("yes", its visitor page) or ("no: <why>", "") for one Pi. Every "yes" links a page (rule 20): an offered
    Pi no visitor page shows is "no: no visitor page"."""
    if host.condition != OPERATIONAL:
        try:
            return f"no: {_board_reason(host, now)}", ""  # the index's Boards rows say the same
        except ValueError as exc:  # a value the page does not know: said in the cell, the page stands
            return f"no: {exc}", ""
    page = pages.get(("pi", host.machine.hostname)) or next(
        (pages[("tt", b["usb_serial"])] for b in host.boards if b["usb_serial"] and ("tt", b["usb_serial"]) in pages), "")
    return ("yes", page) if page else ("no: no visitor page", "")


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
    check, check_title = _check(host)
    try:
        seen, error = ago_time(host.machine.last_seen), ""
    except ValueError as exc:  # fail loud, in this Pi's cell: the rest of the page stands
        seen, error = "", str(exc)
    stamp = host.machine.last_seen
    offered, page = _offered(host, now, pages)
    return Line(host, fpga.pi_label(host.machine), fpga.registered_model(host.machine), fpga.port_text(host.machine), check, check_title, seen, error,
                int(stamp.timestamp()) if stamp else 0, SEVERITY[host.condition], offered, page)


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
        "conditions": [(c, fpga.CONDITION_TITLES[c]) for c in CONDITIONS],
        "reload_on": "?" + stay.urlencode(), "reload_off": "?" + off.urlencode(),
        "want_condition": want_condition, "want_type": want_type,
        "filtered": bool(want_condition or want_type),
        # each Pi's fleet page, where this host has them (tinytapeout.fpgas.online's urlconf does not)
        "fleet": _url(request, "fleet-list"),
        "zone": localtime.zone_name(),
        "reload": request.GET.get("reload", "") != "off",
    })
