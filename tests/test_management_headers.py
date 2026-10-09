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
    # this parse reads flat rules only: an @media or @supports block would hide its rules from the checks below
    assert "@" not in css, "management.css has an @-block: teach _rules to read nested rules first"
    return [([s.strip() for s in sel.split(",")], body) for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", css)]


def _may_hit_a_header(selector):
    """Whether a selector can match a column header (a th in a thead): its last compound names th, or matches any
    element (*, an attribute such as [scope], :is()/:where() with th), and nothing confines it to a tbody."""
    # spaces inside :is( ) / :where( ) are not combinators
    selector = re.sub(r"\([^)]*\)", lambda m: m.group(0).replace(" ", ""), selector.strip())
    compounds = re.split(r"\s*[>+~]\s*|\s+", selector)
    last = compounds[-1] if compounds else ""
    if "tbody" in selector or not last:
        return False
    if re.match(r"th\b", last) or re.search(r":(is|where)\([^)]*\bth\b", last):
        return True
    # a compound with no element name at all (".x", "*", "[scope=col]", ":first-child") can be a th, but only
    # where the selector is about a table, or is bare: a class on its own names other elements of this app
    no_element = re.match(r"[a-z]", last) is None
    return no_element and (last.startswith(("*", "[")) or re.search(r"\b(table|thead|tr)\b|\.mgmt-table", selector[:-len(last)]) is not None)


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
                widen = r"(^|[;\s])(min-)?(width|inline-size)\s*:|white-space\s*:\s*nowrap"
                assert not re.search(widen, body), (sel, body)


@pytest.mark.parametrize("selector", [
    ".mgmt-table th", ".mgmt-ports thead th:first-child", "th", "table th", "th.x", ":is(th)", ":where(th, td)",
    "thead > tr > *", "tr > *", "[scope=col]", ".mgmt-table *", "*", ".mgmt-table thead > tr > th",
])
def test_the_selector_check_sees_a_selector_that_can_reach_a_header(selector):
    assert _may_hit_a_header(selector)


@pytest.mark.parametrize("selector", [
    ".mgmt-ports tbody th", ".mgmt-table td", ".mgmt-table th abbr", ".mgmt-path", ".mgmt-sr",
    ".mgmt-ports tbody th, .x",
])
def test_the_selector_check_leaves_what_cannot(selector):
    assert not _may_hit_a_header(selector.split(",")[0])


def test_the_width_check_catches_every_way_to_widen_a_header():
    widen = r"(^|[;\s])(min-)?(width|inline-size)\s*:|white-space\s*:\s*nowrap"
    for body in (" width: 4em", " min-width:4em", "a: b; width : 1px", " inline-size: 3em", " min-inline-size: 3em",
                 " white-space: nowrap"):
        assert re.search(widen, body), body
    for body in (" max-width: 4em", " border-width: 1px", " white-space: normal", " max-inline-size: 2em"):
        assert not re.search(widen, body), body


def test_screen_reader_text_has_one_definition_that_hides_it():
    """.mgmt-sr serves the summary's symbol headers (#81) and the switch page's PoE cells (#85): one rule, clipped,
    out of the flow, so it takes no width."""
    import pathlib

    from django.apps import apps

    css = (pathlib.Path(apps.get_app_config("management").path) / "static/management/management.css").read_text()
    sr = [body for sels, body in _rules(css) if ".mgmt-sr" in sels]
    assert len(sr) == 1
    for decl in ("position: absolute", "width: 1px", "height: 1px", "overflow: hidden", "clip: rect(0 0 0 0)"):
        assert decl in sr[0]
