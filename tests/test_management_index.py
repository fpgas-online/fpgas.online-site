"""The management section (#66): one index on every host, in that host's own look, linked from its navigation.

Tim, 9 October 2026: "The 'management section' of fpgas.online site is not private and should be accessible
without any login/password -- everything being displaying is easily and publicly available through other means."
"""

import datetime
import html as htmllib
import re
import zoneinfo
from types import SimpleNamespace

import pytest
from django.test import Client
from django.utils import timezone
from fleet.models import BootEvent, Machine
from management.views import index as index_page
from management.views import switches as page

from management import fpga
from tests.test_management_e2e import Github, job
from tests.test_management_fpgas import REAL, pi
from tests.test_management_switches import PortView, SwitchView

TT = "tinytapeout.fpgas.online"


@pytest.fixture
def welland():
    return Client(HTTP_HOST="welland.fpgas.online")


@pytest.fixture
def tt():
    return Client(HTTP_HOST=TT)


@pytest.mark.django_db
@pytest.mark.parametrize("host", ["welland.fpgas.online", "ps1.fpgas.online", TT])
def test_the_index_answers_without_a_login_on_every_host(host):
    r = Client(HTTP_HOST=host).get("/management/")
    assert r.status_code == 200
    html = r.content.decode()
    assert "<h1>Management</h1>" in html and "management/management.css" in html
    assert "<title>Management &mdash; fpgas.online</title>" in html
    assert "/accounts/login" not in html and "password" not in html.lower()


@pytest.mark.django_db
def test_each_host_has_its_own_look(welland, tt):
    w = welland.get("/management/").content.decode()
    assert "tt-nav" not in w  # the fleet pages' look
    assert '<a href="/fleet/">Raspberry Pis</a>' in w
    t = tt.get("/management/").content.decode()
    assert 'class="tt-nav"' in t and "Tiny Tapeout" in t  # tinytapeout's own look
    # no fleet pages on this host: the Pis are its own /management/fpgas/
    assert '<a href="/management/fpgas/">Raspberry Pis</a>' in t and 'href="/fleet/' not in t


@pytest.mark.django_db
def test_management_is_not_linked_from_the_public_pages(welland, tt, settings):
    """#94, Tim: the management section is public by its address, not linked from the visitors' pages."""
    settings.DOMAIN_NAME = "welland.fpgas.online"
    Machine.objects.create(serial="s1", site="welland", hostname="pi-sw2-p4", last_seen=timezone.now())
    # the visitors' pages on each host
    for response in (tt.get("/"), welland.get("/fpgas/")):
        assert response.status_code == 200
        html = response.content.decode()
        assert '>Management</a>' not in html and '>management</a>' not in html
    # the management landing on tinytapeout: the site's own (visitors') navigation has no entry for it
    tt_page = tt.get("/management/").content.decode()
    visitor_nav = tt_page.split('class="tt-nav"', 1)[1].split("</nav>", 1)[0]
    assert "Management" not in visitor_nav
    # the pages of the section lead back to its landing through the one navigation (#131): /fleet/ is one of them,
    # and a host's own page (#93) is under Raspberry Pis
    for path in ("/fleet/", "/fleet/s1/"):
        nav = welland.get(path).content.decode().split('<nav class="mgmt-nav"', 1)[1].split("</nav>", 1)[0]
        assert '<a href="/management/">Management</a>' in nav
        assert '<a href="/fleet/" aria-current="page">Raspberry Pis</a>' in nav


def test_every_management_page_extends_one_base_that_owns_the_header():
    """The dashboards (#67, #68, #69) fill `title`, `extra_head` and `body`; the base keeps the header."""
    import pathlib

    from django.apps import apps

    base = (pathlib.Path(apps.get_app_config("management").path) / "templates/management/base.html").read_text()
    assert base.startswith("{% extends management_theme %}")
    for block in ("title", "extra_head", "body"):
        assert "{% block " + block + " %}" in base
    # one navigation (#131), at the top and the foot of every page
    assert base.count('{% include "management/nav.html" %}') == 2 and "crumb" not in base



NAV = [("/management/", "Management"), ("/fleet/", "Raspberry Pis"), ("/management/switches/", "Switch ports")]


def _navs(html):
    """[[(href, name, current)]] of each management navigation on the page."""
    return [[(href, name, bool(cur)) for href, cur, name in
             re.findall(r'<a href="([^"]+)"( aria-current="page")?>([^<]+)</a>', nav)]
            for nav in re.findall(r'<nav class="mgmt-nav" aria-label="Management">(.*?)</nav>', html, re.S)]


@pytest.mark.django_db
@pytest.mark.parametrize("path, current", [("/management/", "/management/"), ("/fleet/", "/fleet/"),
                                           ("/management/switches/", "/management/switches/"),
                                           ("/fleet/s1/", "/fleet/")])
def test_one_navigation_top_and_foot_of_every_page_the_same_names_the_page_marked(welland, path, current):
    """Tim, 15 Oct 2026 (site #131): "Why is there at the top of the page `fleet * boards` and the bottom of the page
    `Switch Ports, Rapsberry Pis`?" One navigation, the same links and names, at the top and the foot of every
    management page; the page you are on is marked (a Pi's own page is under Raspberry Pis)."""
    Machine.objects.create(serial="s1", site="welland", hostname="pi-sw2-p4", last_seen=timezone.now())
    html = welland.get(path).content.decode()
    navs = _navs(html)
    assert len(navs) == 2 and navs[0] == navs[1]
    assert [(href, name) for href, name, _ in navs[0]] == NAV
    assert [href for href, _, cur in navs[0] if cur] == [current]
    # no other navigation: not the theme's old "fleet · boards" line, no breadcrumb, no list of links at the foot
    assert ">fleet</a>" not in html and ">boards</a>" not in html and "mgmt-crumbs" not in html
    assert "mgmt-links" not in html


@pytest.mark.django_db
def test_on_tinytapeout_the_navigation_names_its_own_pis_page(tt):
    navs = _navs(tt.get("/management/").content.decode())
    assert [(href, name) for href, name, _ in navs[0]] == [
        ("/management/", "Management"), ("/management/fpgas/", "Raspberry Pis"),
        ("/management/switches/", "Switch ports")]


# --- the state at a glance (Tim, 11 Oct 2026) ---------------------------------------------------------------------

READ_AT = "2026-10-11T10:00:00+10:30"
WELLAND = "welland.fpgas.online"
SPEC = SimpleNamespace(index=1, access_ports=4, gateway_trunk_port=3, downstream_trunk_ports=(), house_uplink_port=None)


@pytest.fixture(autouse=True)
def site(settings, monkeypatch):
    settings.MANAGEMENT_SITE_NAME = "welland"
    settings.MANAGEMENT_TIME_ZONE = "Australia/Adelaide"
    monkeypatch.setattr(page, "_inventory", lambda: {1: SPEC, 2: SimpleNamespace(**{**SPEC.__dict__, "index": 2})})


def as_inventory_says(index, extra_untagged=None):
    """A switch with board ports 1, 2 and 4 as the inventory says, and port 3 the gateway trunk."""
    boards = {n: 2000 + 100 * index + n for n in (1, 2, 4)}
    ports = [PortView(port=n, pvid=v, untagged_vlans=sorted({v, *([extra_untagged] if extra_untagged else [])}))
             for n, v in boards.items()]
    ports.append(PortView(port=3, tagged_vlans=sorted(boards.values())))
    return SwitchView(index=index, name=f"sw-{index}", model="GSM7252PS", reachable=True, error="", read_at=READ_AT,
                      ports=sorted(ports, key=lambda p: p.port))


@pytest.fixture
def switches(monkeypatch):
    def install(views):
        monkeypatch.setattr(page, "_reader", lambda: lambda cache, *a, **k: views)
        return views

    return install


def ago_z(**delta):
    """A GitHub time (UTC, "Z") that long before now."""
    return (timezone.now() - datetime.timedelta(**delta)).astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def browser(monkeypatch):
    # an hour ago: within e2e.STALE_AFTER whenever the tests run
    fake = Github({1: [job("shared (welland)", "success", at=ago_z(hours=1))]})
    monkeypatch.setattr("management.e2e.requests.get", fake)
    return fake


@pytest.fixture
def fleet():
    pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    pi("pi-sw2-p47", REAL["pi-sw2-p47"])
    pi("pi-sw2-p33", REAL["pi-sw2-p33"])
    pi("pi-sw2-p7", verifying=True)
    pi("pi-sw2-p4", REAL["pi-sw2-p46"], seen_ago=60 * 7, serial="10000000f536c51f")
    pi("pi-sw2-p10", REAL["pi-sw2-p47"], seen_ago=30, serial="10000000f77b8415")


def table(host=WELLAND):
    """The page's rows as [{"state", "area", "item", "value", "seen", "href", "title", "sort"}] in page order."""
    html = Client(HTTP_HOST=host).get("/management/").content.decode()
    out = []
    for tr in re.findall(r"<tr>\s*(<td.*?)</tr>", html, re.S):
        cells = re.findall(r"<td([^>]*)>(.*?)</td>", tr, re.S)
        text = [htmllib.unescape(re.sub(r"<[^>]+>", "", re.sub(r'<span class="mgmt-sr">.*?</span>', "", c, flags=re.S))).strip()
                for _, c in cells]
        link = re.search(r'href="([^"]*)"', cells[3][1])
        title = re.search(r'title="([^"]*)"', cells[3][0])
        word = re.search(r'title="([^"]*)"', cells[0][0])  # the state's word: its mark's title (#131)
        out.append({"state": htmllib.unescape(word[1]), "mark": text[0].split()[0], "area": text[1], "item": text[2], "value": text[3], "seen": text[4],
                    "href": link and htmllib.unescape(link[1]), "title": title and htmllib.unescape(title[1]),
                    "sort": int(re.search(r'data-sort="(\d+)"', cells[0][0])[1]), "seen_html": cells[4][1]})
    return out


def find(rows, **want):
    (row,) = [r for r in rows if all(r[k] == v for k, v in want.items())]
    return row


@pytest.mark.django_db
def test_failures_come_first_by_severity_then_area_and_the_ok_rows_last(fleet, switches, browser):
    switches([as_inventory_says(1, extra_untagged=21), as_inventory_says(2),
              SwitchView(index=3, name="sw-3", model="GSM7252PS", reachable=False, error="no answer",
                         read_at=READ_AT)])
    rows = table()
    keys = [r["sort"] for r in rows]
    assert keys == sorted(keys)
    states = [r["state"] for r in rows]
    assert states.index("operational") > max(i for i, s in enumerate(states) if s != "operational")
    areas_of_failures = [(r["sort"], r["area"]) for r in rows if r["state"] != "operational"]
    assert areas_of_failures == sorted(areas_of_failures, key=lambda p: (p[0], index_page.AREAS.index(p[1])))
    assert rows[0]["item"].startswith("Switch 3")  # a switch that does not answer is the worst of these


@pytest.mark.django_db
def test_the_pis_are_a_count_per_condition_each_linking_the_pi_table_filtered_to_it(fleet, switches, browser):
    """Tim, 15 Oct 2026: "Why do both exist?": the Pis are rows on /fleet/ only; here they are counts per condition,
    and the counts add up to every registered Pi."""
    switches([as_inventory_says(1)])
    boards = [r for r in table() if r["area"] == "Raspberry Pis"]
    assert sorted(r["item"] for r in boards) == ["checking", "missing", "needs attention", "operational"]
    by = {r["item"]: r for r in boards}
    assert (by["needs attention"]["state"], by["needs attention"]["value"]) == ("needs attention", "2 Pis")
    assert by["needs attention"]["href"] == "/fleet/?condition=attention"
    assert (by["missing"]["state"], by["missing"]["value"], by["missing"]["href"]) == (
        "missing", "1 Pi", "/fleet/?condition=missing")
    assert (by["checking"]["state"], by["checking"]["value"]) == ("checking", "1 Pi")
    assert (by["operational"]["state"], by["operational"]["value"], by["operational"]["href"]) == (
        "operational", "2 Pis", "/fleet/?condition=operational")
    assert sum(int(r["value"].split()[0]) for r in boards) == Machine.objects.count()
    # which Pis and why is the count's title; the Pi table has each as a row
    assert "pi-sw2-p33 (no model registered): wiring, flash" in by["needs attention"]["title"]
    assert "10000000f77b8415 (no model registered): stopped checking in" in by["needs attention"]["title"]
    assert by["missing"]["title"] == "10000000f536c51f (no model registered): missing 7 h"
    # no row names a single Pi when nothing is wrong with the page's data
    assert not [r for r in boards if r["item"] not in fpga.CONDITION_TITLES.values()]


@pytest.mark.django_db
def test_a_condition_with_no_pi_has_no_row_but_the_operational_count_is_always_there(switches, browser):
    """The review of #133: "missing: 0 Pis" beside a green "operational" mark read as two answers."""
    switches([as_inventory_says(1)])
    pi("pi-sw2-p46", REAL["pi-sw2-p46"])
    by = {r["item"]: r for r in table() if r["area"] == "Raspberry Pis"}
    assert set(by) == {"operational"} and by["operational"]["value"] == "1 Pi"


@pytest.mark.django_db
def test_no_board_offered_is_a_failure(switches, browser):
    switches([as_inventory_says(1)])
    row = find(table(), area="Raspberry Pis", item="operational")
    assert (row["state"], row["value"]) == ("needs attention", "0 Pis")


@pytest.mark.django_db
def test_a_switch_as_the_inventory_says_is_one_row_linked_to_its_anchor(switches, browser):
    switches([as_inventory_says(1), as_inventory_says(2)])
    rows = table()
    row = find(rows, item="Switch 2: ports not as the inventory says")
    assert (row["state"], row["value"], row["href"]) == ("operational", "0 ports", "/management/switches/#switch-2")
    assert [r["item"] for r in rows if r["area"] == "Switches"] == [
        "Switch 1: ports not as the inventory says", "Switch 2: ports not as the inventory says"]


@pytest.mark.django_db
def test_vlan_21_on_the_board_ports_is_a_failure_row_per_switch_and_names_the_poe_issue(switches, browser):
    """Today every board port is also untagged in VLAN 21 (fpgas-online/fpgas.online-poe#16): the true state is a
    failure, not special-cased away."""
    switches([as_inventory_says(1, extra_untagged=21), as_inventory_says(2, extra_untagged=21)])
    rows = table()
    row = find(rows, item="Switch 1: also untagged in VLAN 21 (poe #16)")
    assert row["state"] == "needs attention" and row["href"] == "/management/switches/#switch-1"
    assert row["value"] == "3 ports"  # a count (#123); which ports, and the issue, are its title
    assert row["title"].startswith("port 1 also untagged in VLAN 21; port 2 also untagged in VLAN 21")
    assert "fpgas-online/fpgas.online-poe#16" in row["title"] and "port 2 also untagged in VLAN 21" in row["title"]
    assert row["seen"].endswith(" ago")  # when the switch was read
    assert find(rows, item="Switch 2: also untagged in VLAN 21 (poe #16)")["state"] == "needs attention"


@pytest.mark.django_db
def test_a_new_fault_does_not_hide_behind_the_known_vlan_21_one(switches, browser):
    view = as_inventory_says(1, extra_untagged=21)
    view.ports[1].untagged_vlans = [*view.ports[1].untagged_vlans, 99]
    switches([view])
    items = [r["item"] for r in table() if r["area"] == "Switches"]
    assert items == ["Switch 1: untagged VLANs not as the inventory says", "Switch 1: also untagged in VLAN 21 (poe #16)"]


@pytest.mark.django_db
def test_vlans_5_and_121_on_board_ports_are_one_row_naming_poe_18(switches, browser):
    view = as_inventory_says(2, extra_untagged=21)
    view.ports[0].untagged_vlans = [*view.ports[0].untagged_vlans, 5]
    view.ports[1].untagged_vlans = [*view.ports[1].untagged_vlans, 121]
    switches([view])
    row = find(table(), item="Switch 2: also untagged in VLAN 5, 121 (poe #18)")
    assert row["state"] == "needs attention" and row["value"] == "2 ports"
    assert "port 1 also untagged in VLAN 5" in row["title"]
    assert "fpgas-online/fpgas.online-poe#18" in row["title"] and "port 2 also untagged in VLAN 121" in row["title"]


@pytest.mark.django_db
def test_a_wrong_pvid_and_a_trunk_without_a_vlan_are_failures_and_the_first_kind_is_shown(switches, browser):
    view = as_inventory_says(1)
    view.ports[0].pvid = 1
    view.ports[2].tagged_vlans = [2101]
    switches([view])
    rows = table()
    pvid = find(rows, item="Switch 1: PVID not the port's VLAN")
    assert (pvid["value"], pvid["title"]) == ("1 port", "port 1 PVID 1, not 2101")
    trunk = find(rows, item="Switch 1: gateway trunk lacks board VLANs")
    assert trunk["value"] == "2 VLANs"  # the trunk's missing board VLANs, counted
    assert "gateway trunk lacks 2 board VLANs, first 2102" in trunk["title"]


@pytest.mark.django_db
def test_a_switch_read_time_that_is_not_iso_is_a_failure_row_for_that_switch_only(switches, browser):
    bad = as_inventory_says(1)
    bad.read_at = "yesterday"
    switches([bad, as_inventory_says(2)])
    rows = table()
    assert find(rows, item="Switch 1")["state"] == "needs attention" and "yesterday" in find(rows, item="Switch 1")["value"]
    assert find(rows, item="Switch 2: ports not as the inventory says")["state"] == "operational"


@pytest.mark.django_db
def test_a_switch_that_does_not_answer_is_a_failure_row(switches, browser):
    switches([SwitchView(index=2, name="sw-2", model="GSM7252PS", reachable=False, error="no answer", read_at=READ_AT)])
    row = find(table(), item="Switch 2")
    assert (row["state"], row["value"], row["title"], row["href"]) == (
        "needs attention", "✗", "not answering: no answer", "/management/switches/#switch-2")


@pytest.mark.django_db
def test_switches_that_cannot_be_read_at_all_are_one_failure_row(browser):
    # the installed poe package has no switch reader here, or it failed: the reason is the row's value
    row = find(table(), area="Switches")
    assert row["state"] == "needs attention" and row["item"] == "all switches" and row["href"] == "/management/switches/"
    assert (row["value"], row["title"]) == ("✗", page.NOT_INSTALLED)  # a result is a mark, its words the title


@pytest.mark.django_db
def test_the_newest_boot_check_is_the_one_boot_checks_row(fleet, switches, browser):
    switches([as_inventory_says(1)])
    rows = table()
    newest = find(rows, area="Boot checks", item="newest check")
    # the value is the result alone; which Pi it was is the title, its page the link
    # a result is a mark (#131); its words, and which Pi, are the title
    assert (newest["value"], newest["state"]) == ("✓", "operational")
    assert newest["title"] == "passed: 10000000f77b8415 (no model registered) on sw2 p10"
    assert newest["href"] == "/fleet/10000000f77b8415/"
    # the Pi whose check failed is counted under Boards; it is not counted again under Boot checks
    assert [r["item"] for r in rows if r["area"] == "Boot checks"] == ["newest check"]


@pytest.mark.django_db
def test_a_boot_check_from_the_future_fails_its_row_only(fleet, switches, browser):
    switches([as_inventory_says(1)])
    event = BootEvent.objects.filter(stage="fpga-verified").latest("id")
    BootEvent.objects.filter(id=event.id).update(ts=timezone.now() + datetime.timedelta(hours=2))
    rows = table()
    newest = find(rows, area="Boot checks", item="newest check")
    assert newest["state"] == "needs attention" and "future" in newest["value"]
    assert find(rows, area="Raspberry Pis", item="operational")["state"] == "operational"  # the other areas stand


@pytest.mark.django_db
def test_a_browser_test_older_than_three_scheduled_runs_is_a_failure(switches, monkeypatch):
    switches([as_inventory_says(1)])
    monkeypatch.setattr("management.e2e.requests.get", Github({1: [job("shared (welland)", "success", at=ago_z(hours=8))]}))
    row = find(table(), area="Browser test")
    assert (row["state"], row["value"], row["title"]) == ("needs attention", "⊘", "stopped: no completed run for 8 h (the last passed)")
    assert row["href"] == "https://github.com/fpgas-online/fpgas.online-e2e-tests/actions/workflows/e2e.yml"


@pytest.mark.django_db
def test_the_browser_test_row_is_the_newest_run_for_this_site(switches, browser):
    switches([as_inventory_says(1)])
    row = find(table(), area="Browser test")
    assert (row["state"], row["item"], row["value"], row["title"]) == ("operational", "welland shared job of e2e.yml", "✓", "passed")
    assert row["href"].startswith("https://github.com/fpgas-online/fpgas.online-e2e-tests/actions/runs/1/job/")


@pytest.mark.django_db
def test_a_failed_browser_test_is_a_failing_row(switches, monkeypatch):
    switches([as_inventory_says(1)])
    monkeypatch.setattr("management.e2e.requests.get", Github({1: [job("shared (welland)", "failure", at=ago_z(hours=1))]}))
    assert find(table(), area="Browser test")["state"] == "needs attention"
    assert (find(table(), area="Browser test")["value"], find(table(), area="Browser test")["title"]) == ("✗", "failed")


@pytest.mark.django_db
def test_when_the_github_api_cannot_be_read_the_browser_test_is_a_failure_row_with_the_reason(switches):
    switches([as_inventory_says(1)])  # the autouse fixture makes every request raise ConnectionError
    row = find(table(), area="Browser test")
    assert row["state"] == "needs attention"
    assert (row["value"], row["title"]) == ("✗", "cannot be read: GitHub API not reachable (ConnectionError)")
    assert row["href"] == "https://github.com/fpgas-online/fpgas.online-e2e-tests/actions/workflows/e2e.yml"


@pytest.mark.django_db
def test_the_github_answer_is_cached_between_page_views(switches, browser):
    switches([as_inventory_says(1)])
    table()
    asked = len(browser.calls)
    assert asked >= 2
    table()
    assert len(browser.calls) == asked
    assert set(browser.timeouts) == {5}  # short timeout


@pytest.mark.django_db
def test_freshness_is_x_ago_with_the_exact_local_time_in_a_title(fleet, switches, browser):
    switches([as_inventory_says(1)])
    rows = table()
    event = BootEvent.objects.filter(stage="fpga-verified").latest("id")
    local = event.ts.astimezone(zoneinfo.ZoneInfo("Australia/Adelaide"))
    newest = find(rows, area="Boot checks", item="newest check")
    assert newest["seen"].endswith(" ago")
    assert f'title="{local:%Y-%m-%d %H:%M:%S} {local.tzname()}"' in newest["seen_html"]


@pytest.mark.django_db
def test_the_table_is_striped_sortable_and_plain(fleet, switches, browser):
    switches([as_inventory_says(1)])
    html = Client(HTTP_HOST=WELLAND).get("/management/").content.decode()
    assert 'class="mgmt-table mgmt-striped mgmt-sortable mgmt-glance"' in html
    assert '<script src="/static/management/table.js" defer></script>' in html
    assert html.count("<table") == 1
    # no prose between the heading and the table, nor between the table and the navigation at the foot (#131)
    middle = html.split("<h1>Management</h1>")[1].split('<nav class="mgmt-nav"')[0]
    assert "<p" not in middle
    assert "coming" not in html and "SNMP" not in html


@pytest.mark.django_db
@pytest.mark.parametrize("host", [WELLAND, TT])
def test_both_hosts_show_the_same_state(host, fleet, switches, browser):
    switches([as_inventory_says(1)])
    rows = table(host)
    assert {r["area"] for r in rows} == set(index_page.AREAS)
    fleet_links = [r["href"] for r in rows if r["href"] and r["href"].startswith("/fleet/")]
    assert bool(fleet_links) == (host == WELLAND)  # tinytapeout.fpgas.online has no fleet pages


@pytest.mark.django_db
def test_no_host_name_reaches_the_page(fleet, switches, browser, monkeypatch):
    monkeypatch.setattr(page.socket, "gethostname", lambda: "gwhost")
    monkeypatch.setattr(page.socket, "getfqdn", lambda: "gwhost.example.org")
    page._gateway_names.cache_clear()
    try:
        view = as_inventory_says(1, extra_untagged=21)
        view.name = "gwhost.example.org"
        switches([view])
        html = Client(HTTP_HOST=WELLAND).get("/management/").content.decode().lower()
    finally:
        page._gateway_names.cache_clear()
    for name in ("gwhost", "example.org", "ten64"):
        assert name not in html


@pytest.mark.django_db
def test_the_number_of_queries_does_not_grow_with_the_fleet(fleet, switches, browser, django_assert_max_num_queries):
    switches([as_inventory_says(1)])
    with django_assert_max_num_queries(9):
        Client(HTTP_HOST=WELLAND).get("/management/")
    for n in range(20):
        pi(f"pi-sw1-p{n + 1}", REAL["pi-sw2-p46"], serial=f"20000000{n:08x}")
    with django_assert_max_num_queries(9):
        Client(HTTP_HOST=WELLAND).get("/management/")


@pytest.mark.django_db
def test_a_condition_the_page_does_not_know_is_a_failure_row_naming_it(switches, browser, monkeypatch):
    switches([as_inventory_says(1)])
    pi("pi-sw2-p5", REAL["pi-sw2-p46"])
    real = fpga.hosts

    def odd(now=None):
        hosts = real(now)
        hosts[0].condition = "sulking"
        return hosts

    monkeypatch.setattr(fpga, "hosts", odd)
    row = find(table(), area="Raspberry Pis", item="pi-sw2-p5 (no model registered) on sw2 p5")
    assert (row["state"], row["value"]) == ("needs attention", "unknown condition 'sulking'")
    assert row["href"] == "/fleet/pi-sw2-p5/"


@pytest.mark.django_db
def test_a_check_name_or_result_the_page_does_not_know_is_a_failure_row_naming_it(switches, browser):
    switches([as_inventory_says(1)])
    detail = {**REAL["pi-sw2-p33"], "board0_tests": "Bad_Name!=fail"}
    pi("pi-sw2-p5", detail)
    row = find(table(), item="pi-sw2-p5 (no model registered) on sw2 p5")
    assert row["state"] == "needs attention" and "check name 'Bad_Name!'" in row["value"]
    pi("pi-sw2-p6", {"result": "sulking"})
    row = find(table(), item="pi-sw2-p6 (no model registered) on sw2 p6")
    assert row["state"] == "needs attention" and "boot check result 'sulking'" in row["value"]


@pytest.mark.django_db
def test_an_area_that_cannot_be_worked_out_is_one_failure_row_naming_the_reason(switches, browser, monkeypatch):
    switches([as_inventory_says(1)])
    pi("pi-sw2-p5", {"result": "pass"})
    BootEvent.objects.update(detail={"nothing": 1})
    row = find(table(), area="Boot checks")
    assert row["state"] == "needs attention" and row["item"] == "this page"
    assert row["value"].startswith("ValueError: the boot check event") and "has no result" in row["value"]
    assert table()[0]["area"] == "Boot checks"  # an error is the worst kind of failure


@pytest.mark.django_db
def test_a_boot_check_time_in_the_future_is_loud(switches, browser):
    switches([as_inventory_says(1)])
    m = pi("pi-sw2-p5", {"result": "pass"})
    BootEvent.objects.filter(machine=m).update(ts=timezone.now() + datetime.timedelta(days=2))
    row = find(table(), area="Boot checks")
    assert row["state"] == "needs attention" and "in the future" in row["value"]



@pytest.mark.django_db
def test_two_pis_last_seen_on_one_port_are_both_counted_each_named_by_its_label(switches, browser):
    """The label station's port cycles boards: two registrations last seen on one port are two Pis, each named by
    its label in the count's title (the port is an observation on /fleet/)."""
    switches([as_inventory_says(1)])
    pi("pi-sw2-p48", REAL["pi-sw2-p46"], seen_ago=60 * 7, serial="10000000aaaa0048")
    pi("pi-sw2-p48", REAL["pi-sw2-p47"], seen_ago=60 * 8, serial="10000000bbbb0048")
    row = find(table(), area="Raspberry Pis", item="missing")
    assert row["value"] == "2 Pis"
    assert "10000000aaaa0048 (no model registered): missing 7 h" in row["title"]
    assert "10000000bbbb0048 (no model registered): missing 8 h" in row["title"]


@pytest.mark.django_db
def test_a_pi_with_no_report_this_boot_is_counted_as_needing_attention(switches, browser):
    from fleet.models import HardwareSnapshot

    switches([as_inventory_says(1)])
    m = pi("pi-sw1-p38", seen_ago=60 * 2, serial="10000000cccc0038")
    m.latest_snapshot = HardwareSnapshot.objects.create(machine=m, fingerprint="f", document={
        "fpga": {"boards": [{"kind": "acorn-cle-215+"}]}})
    m.save()
    row = find(table(), area="Raspberry Pis", item="needs attention")
    assert (row["state"], row["value"]) == ("needs attention", "1 Pi")
    assert row["title"] == "10000000cccc0038 (no model registered): stopped checking in"


@pytest.mark.django_db
def test_the_index_and_the_pi_table_use_one_set_of_state_marks_and_words(fleet, switches, browser):
    """Site #131 (Tim, 15 Oct 2026, and the review of the live pages: /fleet/ said operational / needs attention,
    /management/ ok / failing): one set, management.states, the same mark and word for a state on every page."""
    from management import states

    switches([as_inventory_says(1)])
    for row in table():
        assert (row["mark"], row["state"]) in set(states.STATES.values())
    pis = Client(HTTP_HOST=WELLAND).get("/fleet/").content.decode()
    cells = re.findall(r'<td class="mgmt-c-state [^"]*" data-sort="\d+" data-label="State" title="([^":]+)'
                       r'(?::[^"]*)?">(.)<', pis)
    assert len(cells) == Machine.objects.count()  # every Pi's State cell was read
    for word, mark in cells:
        assert (word, mark) in {(w, m) for m, w in states.STATES.values()}
