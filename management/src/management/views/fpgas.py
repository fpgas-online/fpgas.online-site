"""The FPGA dashboard (#68): /management/fpgas/."""

from django.shortcuts import render
from fleet.services import CONDITIONS

from .. import fpga
from .index import _url


def fpgas(request):
    rows = fpga.hosts()
    table, total, everything = fpga.summary(rows)
    want_condition = request.GET.get("condition", "")
    want_type = request.GET.get("type", "")
    shown = [r for r in rows if (not want_condition or r.condition == want_condition)
             and (not want_type or r.board_type == want_type)]
    return render(request, "management/fpgas.html", {
        "summary": table, "total": total, "everything": everything, "rows": shown,
        "conditions": [(c, fpga.CONDITION_TITLES[c]) for c in CONDITIONS],
        "want_condition": want_condition if want_condition in CONDITIONS else "", "want_type": want_type,
        "filtered": bool(want_condition or want_type),
        # the fleet pages, where this host has them (tinytapeout.fpgas.online's urlconf does not)
        "fleet": _url(request, "fleet-list"),
    })
