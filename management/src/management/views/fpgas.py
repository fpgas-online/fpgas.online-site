"""The FPGA dashboard (#68): /management/fpgas/."""

from django.shortcuts import render
from django.urls import Resolver404, resolve
from django.utils import timezone
from fleet.services import CONDITIONS
from pibfpgas.pis import listed

from .. import fpga, localtime
from .index import _url


def _resolves(request, path):
    """Whether this host's urlconf has a page at `path` (the /fpgas/ pages are not on tinytapeout's)."""
    try:
        resolve(path, urlconf=getattr(request, "urlconf", None))
    except Resolver404:
        return False
    return True


def fpgas(request):
    # every time on the page in the installation's own zone, named once at the foot; for this page only
    # (override, not activate: the worker's next request keeps the site's own zone)
    with timezone.override(localtime.zone()):
        return _page(request)


def _page(request):
    rows = fpga.hosts()
    table, total, everything = fpga.summary(rows)
    want_condition = request.GET.get("condition", "")
    if want_condition not in CONDITIONS:  # a filter this page does not have is no filter
        want_condition = ""
    want_type = request.GET.get("type", "")[:100]
    shown = [r for r in rows if (not want_condition or r.condition == want_condition)
             and (not want_type or r.board_type == want_type)]
    return render(request, "management/fpgas.html", {
        "summary": table, "total": total, "everything": everything, "rows": shown,
        "conditions": [(c, fpga.CONDITION_TITLES[c]) for c in CONDITIONS],
        "want_condition": want_condition, "want_type": want_type,
        "filtered": bool(want_condition or want_type),
        # the fleet pages, where this host has them (tinytapeout.fpgas.online's urlconf does not)
        "fleet": _url(request, "fleet-list"),
        "zone": localtime.zone_name(),
        "reload": request.GET.get("reload", "") != "off",
        # the visitor's page of an offered Pi, where this host has the /fpgas/ pages
        # the visitor's page of a Pi the /fpgas/ pages list, where this host has them: the same list those pages
        # show, so a link never points at a page that is not there
        "listed": {pi.hostname for pi in listed()} if _resolves(request, "/fpgas/") else set(),
    })
