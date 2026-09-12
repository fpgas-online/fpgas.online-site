"""Inject the under-construction banner into the pages tweed serves.

Welland (tweed) is where new work lands first, so its pages carry a banner
pointing visitors at the stable PS1 site when something is broken. The banner
is gated on the UNDER_CONSTRUCTION setting rather than on a hostname: tweed
answers to both welland.fpgas.online and tinytapeout.fpgas.online and both
should carry it, while ps1.fpgas.online runs this same code and must not.
fpgas.online-infra writes the setting per host into local_settings.py.

This is response-rewriting middleware because the pib-side pages
(index.html, fpga.html, tt.html) are standalone documents with no shared base
template -- there is no single place to put an {% include %}.
"""

import re

from django.conf import settings
from django.template.loader import render_to_string

# The opening <body> tag, with or without attributes.
_BODY_TAG = re.compile(r"<body\b[^>]*>", re.IGNORECASE)

# Dismissal is a client-side cookie with a max-age and no server-side record:
# the browser drops it after a week and the banner comes back by itself.
# Both names are handed to the template so the script that WRITES the cookie
# and the check that READS it can never drift apart.
DISMISS_COOKIE = "fo_uc_dismissed"
DISMISS_MAX_AGE = 7 * 24 * 60 * 60


class UnderConstructionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if not getattr(settings, "UNDER_CONSTRUCTION", False):
            return response

        if request.COOKIES.get(DISMISS_COOKIE):
            return response

        html = response.content.decode(response.charset)
        body = _BODY_TAG.search(html)
        if body is None:
            return response

        banner = render_to_string(
            "under_construction.html",
            {
                "fallback": settings.UNDER_CONSTRUCTION_FALLBACK,
                "cookie": DISMISS_COOKIE,
                "max_age": DISMISS_MAX_AGE,
            },
        )
        response.content = (html[: body.end()] + banner + html[body.end() :]).encode(response.charset)
        return response
