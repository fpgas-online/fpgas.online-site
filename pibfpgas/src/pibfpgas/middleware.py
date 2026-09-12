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

        # Only top-level pages. Browsers send "document" for a navigation and
        # "iframe" for a frame load ("empty" for fetch/XHR, so this excludes
        # HTML fragments pulled in by script too). An absent header means a
        # client too old to tell us (Safari before 16.4, curl); assume top
        # level and let the banner's own script drop it if it is framed.
        dest = request.headers.get("Sec-Fetch-Dest")
        if dest and dest != "document":
            return response

        if request.path.startswith(tuple(settings.UNDER_CONSTRUCTION_EXCLUDE_PREFIXES)):
            return response

        # A streaming response has no .content to rewrite, and buffering one to
        # add a banner would defeat the point of streaming it.
        if getattr(response, "streaming", False):
            return response

        if not response.get("Content-Type", "").startswith("text/html"):
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

        # Django does not set Content-Length itself (the WSGI server does, after
        # this runs), but correct it if some other middleware already has.
        if response.has_header("Content-Length"):
            response["Content-Length"] = str(len(response.content))

        return response
