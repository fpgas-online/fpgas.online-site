""""x ago" (fleet.timeago): the one helper every dashboard shows freshness with."""

import zoneinfo
from datetime import datetime, timedelta, timezone

import pytest
from django.template import Context, Template
from django.utils import timezone as djtz

from fleet import timeago

NOW = datetime(2026, 10, 11, 8, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("seconds, text", [
    (0, "0 s ago"), (59, "59 s ago"), (60, "1 min ago"), (3599, "59 min ago"), (3600, "1 h ago"),
    (86399, "23 h ago"), (86400, "1 d ago"), (86400 * 9 + 5, "9 d ago"),
])
def test_ago_reads_one_number_and_one_unit(seconds, text):
    assert timeago.ago(NOW - timedelta(seconds=seconds), NOW) == text


def test_a_little_clock_skew_reads_zero_and_a_big_one_is_an_error():
    assert timeago.ago(NOW + timedelta(minutes=4), NOW) == "0 s ago"
    with pytest.raises(ValueError, match="in the future"):
        timeago.ago(NOW + timedelta(hours=2), NOW)


def test_a_time_with_no_zone_is_an_error():
    with pytest.raises(ValueError, match="no time zone"):
        timeago.ago(datetime(2026, 10, 11, 8, 0, 0), NOW)


def test_the_exact_local_time_is_in_the_title_in_the_active_zone():
    moment = datetime.now(timezone.utc) - timedelta(minutes=3)
    with djtz.override(zoneinfo.ZoneInfo("Australia/Adelaide")):
        out = timeago.ago_time(moment)
    local = moment.astimezone(zoneinfo.ZoneInfo("Australia/Adelaide"))
    assert f'title="{local:%Y-%m-%d %H:%M:%S} {local.tzname()}"' in out and ">3 min ago</time>" in out
    assert f'datetime="{moment.isoformat()}"' in out
    assert timeago.ago_time(None) == ""


def test_the_filters_are_usable_from_any_template():
    moment = djtz.now() - timedelta(hours=2)
    out = Template("{% load fleet_time %}{{ m|ago }}|{{ m|ago_time }}").render(Context({"m": moment}))
    assert out.startswith("2 h ago|<time datetime=") and out.endswith(">2 h ago</time>")
