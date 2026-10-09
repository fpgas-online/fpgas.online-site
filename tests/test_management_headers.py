"""Tim, 9 October 2026: "Make sure the dashboard table columns widths are set by the values in the columns width,
*not* by the headers text. Shrink / change / modify headers text so that columns are not too wide."

The widths themselves are measured in a browser (the PR's preview: every header against the widest value in its
column, at 1280 and 390 px); these tests keep the headers short and the shared rule in place."""

import html as htmllib
import re

import pytest
from django.test import Client


def headers(html, n):
    """The visible header texts of the n-th table on the page."""
    table = re.findall(r"<table.*?</table>", html, re.S)[n]
    return [htmllib.unescape(re.sub(r"<[^>]+>", "", th)).strip()
            for th in re.findall(r"<th[^>]*>(.*?)</th>", table, re.S)]


@pytest.fixture
def c():
    return Client(HTTP_HOST="welland.fpgas.online")


@pytest.mark.django_db
def test_the_fpga_dashboard_headers_are_short(c):
    html = c.get("/management/fpgas/").content.decode()
    assert headers(html, 0) == ["Type", "✓", "!", "✗", "?", "Σ"]  # one symbol over one-digit counts
    assert headers(html, 1) == ["Pi", "Port", "Board", "Check", "Seen", "Offered"]
    # each symbol says its full name, and the key beside the table names them all
    for title in ("operational", "needs attention", "missing", "unknown", "total"):
        assert f'title="{title}"' in html and title in html.split('class="mgmt-key"', 1)[1].split("</p>", 1)[0]
    assert 'class="mgmt-table mgmt-summary mgmt-fit"' in html  # the counts table is as wide as its counts


@pytest.mark.django_db
def test_the_index_headers_are_short(c):
    assert headers(c.get("/management/").content.decode(), 0) == ["Page", "Shows", "Data from"]


def test_the_shared_rule_lets_headers_wrap_and_never_widen_a_column():
    import pathlib

    from django.apps import apps

    css = (pathlib.Path(apps.get_app_config("management").path) / "static/management/management.css").read_text()
    assert ".mgmt-table th { white-space: normal;" in css
    # nothing in the shared rules sets a header's width or stops it wrapping
    shared = css.split("/* Switch port dashboard", 1)[0]
    assert not re.search(r"th[^{]*\{[^}]*(min-width|white-space:\s*nowrap)", shared)
