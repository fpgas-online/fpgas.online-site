"""The management section's index: what each page shows and where its data comes from (#66)."""

from dataclasses import dataclass

from django.shortcuts import render
from django.urls import NoReverseMatch, reverse


@dataclass(frozen=True)
class Entry:
    title: str
    url_name: str  # the page's url name; a page not built yet has none in this urlconf
    issue: int  # the issue that describes the page
    what: str  # one line: what it shows
    source: str  # one line: where its data comes from


DASHBOARDS = [
    Entry("Switch ports", "management-switches", 67,
          "Every switch and every port: PoE, link, LLDP neighbour and traffic, with PoE on and off per port.",
          "SNMP on the site's switches, read by the gateway."),
    Entry("FPGA hosts", "management-fpgas", 68,
          "Every Raspberry Pi: its switch port, the FPGA board its boot check found, how that check went and its "
          "last check-in; a summary by board type.",
          "The fleet registry: each Pi's registration and its boot check events."),
    Entry("Visitor and usage stats", "management-stats", 69,
          "Anonymised visitor and board-use reports for the last day, week and month.",
          "The gateway's logs, with nothing that identifies a visitor."),
]
EXISTING = [
    Entry("Fleet registry", "fleet-list", 0,
          "Every registered machine, its hardware and the events of its boots.",
          "Each Pi's own registration and boot check events."),
]


def _url(request, name):
    """The page's address on this host, or "" when it does not exist here (not built yet, or not on this host)."""
    try:
        return reverse(name, urlconf=getattr(request, "urlconf", None))
    except NoReverseMatch:
        return ""


def index(request):
    rows = [(entry, _url(request, entry.url_name)) for entry in DASHBOARDS + EXISTING]
    return render(request, "management/index.html", {
        "dashboards": rows[:len(DASHBOARDS)], "existing": [r for r in rows[len(DASHBOARDS):] if r[1]],
    })
