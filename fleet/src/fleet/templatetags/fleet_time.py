"""Template filters for times on the dashboards: see fleet.timeago."""

from django import template

from fleet import timeago

register = template.Library()


@register.filter
def ago(moment):
    """"3 min ago"."""
    return timeago.ago(moment)


@register.filter
def ago_time(moment):
    """`<time>` with "3 min ago" and the exact local time in its title."""
    return timeago.ago_time(moment)
