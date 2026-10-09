"""Tim, 9 October 2026: "Make sure the dashboard table columns widths are set by the values in the columns width,
*not* by the headers text. Shrink / change / modify headers text so that columns are not too wide."

The widths themselves are measured in a browser (the PR's preview: every header against the widest value in its
column, at 1280 and 390 px); these tests keep the headers short and the shared rule in place."""

import html as htmllib
import re

import pytest
from django.test import Client


def _ths(html, n):
    return re.findall(r"<th[^>]*>(.*?)</th>", re.findall(r"<table.*?</table>", html, re.S)[n], re.S)


def _text(fragment):
    return htmllib.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def headers(html, n):
    """The visible header texts of the n-th table on the page: without the text only a screen reader gets."""
    return [_text(re.sub(r'<span class="mgmt-sr">.*?</span>', "", th)) for th in _ths(html, n)]


def spoken(html, n):
    """What a screen reader says for each header of the n-th table: aria-hidden parts left out."""
    return [_text(re.sub(r'<(\w+)[^>]*aria-hidden="true"[^>]*>.*?</\1>', "", th)) for th in _ths(html, n)]


@pytest.fixture
def c():
    return Client(HTTP_HOST="welland.fpgas.online")


@pytest.mark.django_db
def test_the_fpga_dashboard_headers_are_short(c):
    html = c.get("/management/fpgas/").content.decode()
    assert headers(html, 0) == ["Type", "✓", "!", "✗", "?", "Σ"]  # one symbol over one-digit counts
    assert headers(html, 1) == ["Pi", "Port", "Board", "Boot check", "Last seen", "Offered"]
    # a screen reader hears each symbol's full name, not the symbol (#81)
    assert spoken(html, 0) == ["Type", "operational", "needs attention", "missing", "unknown", "total"]
    for title in ("operational", "needs attention", "missing", "unknown", "total"):
        assert f'title="{title}"' in html  # and a mouse shows it
    # the key beside the table names every symbol, in the columns' order
    key = _text(html.split('<p class="mgmt-key">', 1)[1].split("</p>", 1)[0])
    assert key == "\u2713 operational \u00b7 ! needs attention \u00b7 \u2717 missing \u00b7 ? unknown \u00b7 \u03a3 total"
    assert 'class="mgmt-table mgmt-summary mgmt-fit"' in html  # the counts table is as wide as its counts


@pytest.mark.django_db
def test_the_index_headers_are_short(c):
    assert headers(c.get("/management/").content.decode(), 0) == ["Page", "Shows", "Data from"]


def _rules(css):
    """(selectors, declarations) for every rule in a stylesheet without @-blocks, comments removed."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [([s.strip() for s in sel.split(",")], body) for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", css)]


def _may_hit_a_header(selector):
    """Whether a selector can match a column header (a th in a thead) of a management table."""
    last = selector.split()[-1] if selector.split() else ""
    return re.match(r"th\b", last) is not None and "tbody" not in selector and selector.startswith(".mgmt-")


def test_the_shared_rule_lets_headers_wrap_and_never_widen_a_column():
    import pathlib

    from django.apps import apps

    css = (pathlib.Path(apps.get_app_config("management").path) / "static/management/management.css").read_text()
    assert ".mgmt-table th { white-space: normal;" in css
    rules = _rules(css)
    assert any(".mgmt-table th" in sels for sels, _ in rules)  # the parse found the rules
    # no rule that can reach a header, in any management table, sets its width or stops it wrapping (#81)
    for sels, body in rules:
        for sel in sels:
            if _may_hit_a_header(sel):
                assert not re.search(r"(^|[;\s])(min-)?width\s*:|white-space\s*:\s*nowrap", body), (sel, body)


def test_the_selector_check_sees_what_it_must():
    assert _may_hit_a_header(".mgmt-table th") and _may_hit_a_header(".mgmt-ports thead th:first-child")
    assert not _may_hit_a_header(".mgmt-ports tbody th") and not _may_hit_a_header(".mgmt-table td")
    assert not _may_hit_a_header(".mgmt-table th abbr") and not _may_hit_a_header(".mgmt-path")
