"""A switch's front panel on the switch page (#90): each port where it is, coloured by its state with a glyph and
the words in its title and accessible name, marked where it is not as the inventory says (a known cause apart from a
new fault), linked to its row."""

import json

import pytest
from django.test import Client
from management.views import switches as page

from management import panel
from tests.test_management_switches import HOSTS, READ_AT, PortView, SwitchView, spec


def board_switch(extra_untagged=None, ports=52, poe_ports=48, model="gsm7252ps"):
    """Switch 1 as the inventory (spec()) says: board ports 1..40 in their VLANs, the gateway trunk (47) carrying them,
    PoE on 1..poe_ports, no PoE above; `extra_untagged` a VLAN every board port is also untagged in."""
    view_ports = []
    for n in range(1, ports + 1):
        vlan = 2100 + n
        board = n <= 40
        view_ports.append(PortView(
            port=n, link_up=n in (10, 47), speed_mbps=1000 if n in (10, 47) else None,
            poe_state=("delivering" if n == 10 else "searching") if n <= poe_ports else None,
            poe_watts=8.6 if n == 10 else None,
            pvid=vlan if board else 1,
            untagged_vlans=[vlan] + ([extra_untagged] if extra_untagged and board else []) if board else [1],
            tagged_vlans=[2100 + b for b in range(1, 41)] if n == 47 else []))
    return SwitchView(index=1, name="sw-1", model=model, reachable=True, error="", read_at=READ_AT,
                      good_at=READ_AT, ports=view_ports)


@pytest.fixture
def one_switch(monkeypatch):
    def install(*views):
        monkeypatch.setattr(page, "_reader", lambda: lambda cache, *a, **k: list(views))
        monkeypatch.setattr(page, "_inventory", lambda: {1: spec()})
        return views[0]
    return install


def page_html():
    return Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()


def page_json():
    return json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)["switches"]


def test_the_ports_are_in_two_rows_odd_above_even_then_the_uplink_block():
    places = panel._place(board_switch().ports, 52, 48)
    assert places[1][1] == places[3][1] < places[2][1] == places[4][1]  # odd above even
    assert places[1][0] == places[2][0] < places[3][0]  # 1 over 2, then 3 over 4
    # 49..52 carry no PoE on this model: the uplink block, after a gap
    assert places[49][0] - places[47][0] == panel.PITCH + panel.GAP and places[49][1] < places[50][1]
    assert places[49][0] == places[50][0] < places[51][0] == places[52][0]


def test_a_port_the_reading_lacks_leaves_its_gap_and_moves_no_other():
    view = board_switch()
    whole = panel._place(view.ports, 52, 48)
    view.ports = [p for p in view.ports if p.port != 5]
    assert panel._place(view.ports, 52, 48) == {n: xy for n, xy in whole.items() if n != 5}


def test_the_uplink_block_comes_from_the_model_not_from_which_ports_read_poe():
    view = board_switch()
    view.ports[47].poe_state = None  # port 48's PoE not read
    places = panel._place(view.ports, 52, 48)
    assert places[48] == (places[47][0], places[2][1])  # still under 47, not moved into the uplink block
    assert panel.state(view.ports[47], has_poe=True) == "unknown"


def test_a_port_outside_the_model_is_a_layout_error():
    with pytest.raises(panel.LayoutError, match="port 53 is not on this model"):
        panel._place([PortView(port=53)], 52, 48)


def test_a_model_with_no_poe_is_all_main_block():
    places = panel._place([PortView(port=n) for n in range(1, 11)], 10, 0)
    assert [places[n][0] for n in (1, 3, 5, 7, 9)] == [0, panel.PITCH, 2 * panel.PITCH, 3 * panel.PITCH,
                                                       4 * panel.PITCH]


def test_each_state_is_a_word_and_a_glyph():
    view = board_switch()
    states = {p.port: panel.state(p, p.port <= 48) for p in view.ports}
    assert (states[10], states[47], states[1], states[49]) == ("power", "up", "searching", "down")
    words = {"power", "starting", "up", "searching", "off", "down", "fault", "unknown"}
    assert set(panel.STATE_WORDS) == set(panel.GLYPHS) == words
    assert len(set(panel.GLYPHS.values())) == len(words)  # no two states share a glyph


@pytest.mark.parametrize("link_up, poe, has_poe, word", [
    (False, "other", True, "unknown"),  # a PoE state the poe reader does not name
    (True, "other", True, "unknown"),
    (False, None, True, "unknown"),  # a PoE port whose PoE was not read
    (True, None, True, "unknown"),
    (False, None, False, "down"),  # a port with no PoE on this model
    (True, None, False, "up"),
    (False, "delivering", True, "starting"),  # powered, its link not up yet
    (True, "delivering", True, "power"),
    (True, "searching", True, "up"),
    (True, "disabled", True, "up"),
    (False, "disabled", True, "off"),
    (True, "fault", True, "fault"),
])
def test_the_state_says_what_the_switch_said(link_up, poe, has_poe, word):
    assert panel.state(PortView(port=1, link_up=link_up, poe_state=poe), has_poe) == word


def test_a_poe_state_the_panel_does_not_know_raises():
    with pytest.raises(ValueError, match="PoE state 'melting' is not one the panel knows"):
        panel.state(PortView(port=7, poe_state="melting"), True)


@pytest.mark.django_db
def test_the_panel_is_drawn_with_its_states_glyphs_marks_titles_and_links(one_switch):
    view = board_switch()
    view.ports[2].pvid = 1  # port 3: a new fault
    view.ports[20].poe_state = "other"  # port 21: PoE state not known
    one_switch(view)
    html = page_html()
    assert '<svg class="mgmt-panel" id="switch-1-panel"' in html and 'role="group"' in html and 'role="img"' not in html
    # the panel's name counts the ports in each state: 48 PoE ports less 10 (delivering), 47 (link up) and 21
    assert ('role="group" aria-label="Front panel of Switch 1: sw-1 (GSM7252PS): 1 link up, PoE delivering; 1 link up, '
            'no PoE drawn; 45 link down, PoE on and searching; 4 link down, no PoE on the port; 1 PoE state not known '
            '(the switch gave none it names, or it was not read). A port links to its row."') in html
    assert '<a href="#switch-1-port-10" aria-label="port 10 · link up, PoE delivering · ' in html
    assert '<g id="switch-1-panel-10" class="mgmt-port mgmt-port-power">' in html
    assert 'id="switch-1-panel-3" class="mgmt-port mgmt-port-searching mgmt-port-wrong">' in html
    assert "port 3 · link down, PoE on and searching" in html and "not as the inventory says: port 3 PVID 1" in html
    assert 'id="switch-1-panel-21" class="mgmt-port mgmt-port-unknown">' in html
    assert "port 21 · PoE state not known (the switch gave none it names, or it was not read) · link down" in html
    assert 'class="mgmt-glyph" x="134" y="63" text-anchor="middle" aria-hidden="true">P</text>' in html  # 10: col 5, row 2
    assert "port 47 · link up, no PoE drawn · gateway trunk" in html  # the inventory's role
    assert '<tr id="switch-1-port-10">' in html  # the row the panel links to
    assert "red dashed edge: not as the inventory says" in html  # the key, in words
    assert '<text class="mgmt-glyph" x="8" y="11" text-anchor="middle">?</text></svg> PoE state not known' in html
    assert 'tabindex="0" role="region" aria-label="Front panel of Switch 1: sw-1 (GSM7252PS)"' in html  # the scroll box


@pytest.mark.django_db
def test_a_known_cause_is_marked_apart_from_a_new_fault(one_switch):
    view = board_switch(extra_untagged=21)  # every board port also untagged in VLAN 21: poe #16
    view.ports[4].untagged_vlans = [*view.ports[4].untagged_vlans, 99]  # port 5: and a new one
    one_switch(view)
    ports = {p["port"]: p for p in page_json()[0]["panel"]}
    assert ports[1]["mismatch"] and ports[1]["known"] and "known: poe #16" in ports[1]["title"]
    assert ports[5]["mismatch"] and not ports[5]["known"]  # VLAN 99 has no known cause
    assert not ports[47]["mismatch"] and not ports[49]["mismatch"]
    html = page_html()
    assert 'id="switch-1-panel-1" class="mgmt-port mgmt-port-searching mgmt-port-known">' in html
    assert 'id="switch-1-panel-5" class="mgmt-port mgmt-port-searching mgmt-port-wrong">' in html


@pytest.mark.django_db
def test_the_refresh_script_keeps_the_panel_with_the_table(one_switch):
    one_switch(board_switch())
    script = page_html().split("<script>")[1].split("</script>")[0]
    assert 'getElementById(anchor + "-panel-" + p.port)' in script and 'querySelector("title").textContent' in script
    assert 'querySelector(".mgmt-glyph").textContent = p.glyph' in script
    assert 'g.parentNode.setAttribute("aria-label", p.title)' in script
    assert 'svg.setAttribute("aria-label", s.panel_label' in script
    assert 'tr.id = anchor + "-port-" + p.port' in script and "innerHTML" not in script
    # a port that came or went: the page is drawn afresh, not left with a stale layout
    panel_part = script.split("the front panel (#90)")[1]
    assert "location.reload()" in panel_part.split("panel.forEach")[0]


@pytest.mark.django_db
def test_a_switch_that_does_not_answer_has_no_panel(monkeypatch):
    dead = SwitchView(index=2, name="sw-2", model="gsm7252ps", reachable=False, error="no answer", read_at=READ_AT)
    monkeypatch.setattr(page, "_reader", lambda: lambda cache, *a, **k: [dead])
    data = page_json()[0]
    assert data["panel"] == [] and data["panel_width"] == 0 and data["panel_problem"] == ""


@pytest.mark.django_db
def test_a_model_the_registry_does_not_know_draws_no_panel_and_says_why(one_switch):
    one_switch(board_switch(model="xyz9000"))
    data = page_json()[0]
    assert data["panel"] == [] and data["panel_problem"] == "No front panel: unknown switch model: 'xyz9000'."
    assert '<p class="mgmt-problem">No front panel: unknown switch model: &#x27;xyz9000&#x27;.</p>' in page_html()


@pytest.mark.django_db
def test_a_reading_that_does_not_fit_the_model_draws_no_panel_and_says_why(one_switch):
    one_switch(board_switch(ports=54))
    data = page_json()[0]
    assert data["panel"] == []
    assert data["panel_problem"] == ("No front panel: the reading does not fit the GSM7252PS: port 53 is not on this "
                                     "model (ports 1..52).")


@pytest.mark.django_db
def test_the_legacy_switch_links_to_rows_by_its_place_on_the_page(one_switch):
    legacy = board_switch()
    legacy.index = None
    other = board_switch()
    other.index = 5
    one_switch(other, legacy)
    data = page_json()
    assert data[1]["index"] is None and data[1]["panel"][0]["href"] == "#switch-1-port-1"  # second on the page
    html = page_html()
    assert '<section class="mgmt-switch" id="switch-1">' in html and 'id="switch-1-panel-1"' in html
    assert '<tr id="switch-1-port-1">' in html
