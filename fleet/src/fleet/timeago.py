""""x ago" for the dashboards (Tim, 9 Oct 2026: freshness is "x ago", the exact time in a title).

One definition for every page that shows how long ago something happened: the management index, the FPGA hosts
page and the fleet pages. In a template: `{% load fleet_time %}{{ moment|ago_time }}`. The exact time in the title
is in the zone that is active when it is rendered, so a page wraps its rendering in
`timezone.override(management.localtime.zone())`, as the management views do."""

import datetime

from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe

# A time this far ahead of now is a clock that is wrong (a Pi stamps events with its own clock), not "just now".
FUTURE_SLACK = datetime.timedelta(minutes=5)


def duration(delta):
    """A span as one number and one unit: "40 s", "12 min", "3 h", "5 d". Never negative."""
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


def ago(moment, now=None):
    """"3 min ago". A moment a little ahead of `now` (clocks differ) reads "0 s ago"; one further ahead than
    FUTURE_SLACK is a ValueError saying so (fail loud: the time is wrong, and the page must not hide it)."""
    if timezone.is_naive(moment):
        raise ValueError(f"{moment!r} has no time zone")
    delta = (now or timezone.now()) - moment
    if delta < -FUTURE_SLACK:
        raise ValueError(f"{moment.isoformat()} is {duration(-delta)} in the future")
    return f"{duration(delta)} ago"


def exact(moment):
    """The exact time in the active zone: "2026-10-11 18:05:00 ACDT"."""
    return timezone.localtime(moment).strftime("%Y-%m-%d %H:%M:%S %Z")


def ago_time(moment, now=None):
    """`<time datetime="..." title="exact local time">3 min ago</time>`; "" for no moment."""
    if moment is None:
        return mark_safe("")
    return format_html('<time datetime="{}" title="{}">{}</time>', moment.isoformat(), exact(moment),
                       ago(moment, now))
