"""The installation's own time zone, for the management pages: clock times are local, never UTC (Tim's rule).

Django's TIME_ZONE stays UTC for the site as a whole. These pages are shown in, in order:
MANAGEMENT_TIME_ZONE when the site's settings name one; else the zone of the host the site runs on (its
gateway, whose /etc/localtime names the installation's zone); else Django's TIME_ZONE."""

import os
import zoneinfo

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

LOCALTIME = "/etc/localtime"


def _host_zone(path=LOCALTIME):
    """The IANA name of the zone /etc/localtime links to (".../zoneinfo/Australia/Adelaide"), or ""."""
    try:
        target = os.path.realpath(path)
    except OSError:
        return ""
    _, sep, name = target.partition("/zoneinfo/")
    return name if sep and _valid(name) else ""


def _valid(name):
    try:
        zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return False
    return True


def zone_name():
    """The name of the zone the management pages show times in."""
    named = getattr(settings, "MANAGEMENT_TIME_ZONE", "")
    if named:
        if not _valid(named):  # a setting that names no zone is a mistake to say, not to step over
            raise ImproperlyConfigured(f"MANAGEMENT_TIME_ZONE = {named!r} is not a time zone")
        return named
    return _host_zone() or settings.TIME_ZONE


def zone():
    return zoneinfo.ZoneInfo(zone_name())
