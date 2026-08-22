"""Template filters for ttsite.

Board ``links`` and ``pmods`` are free-form JSON loaded from tt-boards.yaml,
so their URLs are not trusted enough to drop straight into an href.
"""

from urllib.parse import urlsplit

from django import template

register = template.Library()

ALLOWED_SCHEMES = {"http", "https", "mailto"}


@register.filter
def safe_url(value):
    """Return ``value`` if it is an http/https/mailto URL, otherwise ``#``.

    Anything else -- ``javascript:``, ``data:``, a scheme-relative ``//host``
    or a bare relative path -- becomes ``#`` rather than a live link.
    """
    if not isinstance(value, str):
        return "#"
    url = value.strip()
    try:
        scheme = urlsplit(url).scheme
    except ValueError:
        return "#"
    return url if scheme.lower() in ALLOWED_SCHEMES else "#"
