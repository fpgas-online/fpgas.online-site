"""The theme a management page renders in: the site's own, by the host the request came to."""

from ttsite.middleware import serves_ttsite

TT_THEME = "ttsite/base.html"
WELLAND_THEME = "fleet/base.html"


def theme(request):
    """`management_theme`: the base template management/base.html extends. tinytapeout.fpgas.online's own look
    on that host, the fleet pages' look on every other (welland.fpgas.online, ps1.fpgas.online, ...)."""
    return {"management_theme": TT_THEME if serves_ttsite(request) else WELLAND_THEME}
