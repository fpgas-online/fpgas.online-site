"""The theme a management page renders in: the site's own, by the host the request came to."""

from django.urls import NoReverseMatch, reverse
from ttsite.middleware import serves_ttsite

TT_THEME = "ttsite/base.html"
WELLAND_THEME = "fleet/base.html"


def theme(request):
    """`management_theme`: the base template management/base.html extends. tinytapeout.fpgas.online's own look
    on that host, the fleet pages' look on every other (welland.fpgas.online, ps1.fpgas.online, ...)."""
    return {"management_theme": TT_THEME if serves_ttsite(request) else WELLAND_THEME}


# The management section's pages, in the navigation's order, each by the first of its url names this host has: the
# Pis are /fleet/ where the host has the fleet pages, else /management/fpgas/ (tinytapeout.fpgas.online).
NAV = (("Management", ("management-index",)), ("Raspberry Pis", ("fleet-list", "management-fpgas")),
       ("Switch ports", ("management-switches",)), ("Visitor stats", ("management-stats",)))


def _url(request, names):
    for name in names:
        try:
            return reverse(name, urlconf=getattr(request, "urlconf", None))
        except NoReverseMatch:
            continue
    return ""


def nav(request):
    """`management_nav`: [(title, url, current)] of the management pages this host has (site #131: one navigation,
    the same names everywhere). A page is current when it is the one asked for, or a page below it (a Pi's own page
    /fleet/<serial>/ is under Raspberry Pis); the landing only for itself."""
    links = [(title, url) for title, names in NAV if (url := _url(request, names))]
    path = request.path
    return {"management_nav": [(title, url, path == url or (url != links[0][1] and path.startswith(url)))
                               for title, url in links] if links else []}
