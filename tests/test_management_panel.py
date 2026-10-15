"""A switch's front panel on the switch page (#90, #130): each port where it is, drawn so that it needs no key (Tim, 15
Oct 2026): its number, the hardware the gateway last saw on it, its /fleet/ State mark and fill, a solid edge while the
link is up and a dashed one while it is down, ⚡ while PoE delivers, "!" where the network reading is not as it should
be; a switch-wide known issue one line above the drawing; each port's words in its title, linked to its row."""

import json
import re

import pytest
from django.test import Client
from management.views import switches as page

from management import panel
from tests.test_management_switches import HOSTS, READ_AT, PortView, SwitchView, seat, spec


def board_switch(extra_untagged=None, ports=52, poe_ports=48, model="gsm7252ps"):
    """Switch 1 as the inventory (spec()) says: board ports 1..40 in their VLANs, the gateway trunk (47) carrying them,
    PoE on 1..poe_ports, no PoE above; `extra_untagged` a VLAN every board port is also untagged in. Port 10 links at
    1G and draws 8.6 W, port 47 (the gateway trunk) links at 1G; the rest are down, their PoE searching."""
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


def box_of(html, port, switch=1):
    """The served <g> of a port's box, its markup up to its end."""
    return re.search(rf'<g id="switch-{switch}-panel-{port}" .*?</g>', html, re.S).group(0)


def texts(box):
    """{class: text} of a box's text elements (the hardware lines as a list)."""
    found = re.findall(r'<text class="([^"]+)"[^>]*>([^<]*)</text>', box)
    out = {cls.split()[0]: text for cls, text in found if not cls.startswith("mgmt-hw")}
    out["hw"] = [text for cls, text in found if cls.startswith("mgmt-hw")]
    return out


# --- the layout -------------------------------------------------------------------------------------------------


def test_the_ports_are_in_two_rows_odd_above_even_then_the_uplink_block():
    places = panel._place(range(1, 53), 52, 48)
    assert places[1][1] == places[3][1] < places[2][1] == places[4][1]  # odd above even
    assert places[1][0] == places[2][0] < places[3][0]  # 1 over 2, then 3 over 4
    # 49..52 carry no PoE on this model: the uplink block, after a gap
    assert places[49][0] - places[47][0] == panel.PITCH + panel.GAP and places[49][1] < places[50][1]
    assert places[49][0] == places[50][0] < places[51][0] == places[52][0]


def test_a_port_the_reading_lacks_leaves_its_gap_and_moves_no_other():
    whole = panel._place(range(1, 53), 52, 48)
    assert panel._place([n for n in range(1, 53) if n != 5], 52, 48) == {n: xy for n, xy in whole.items() if n != 5}


def test_a_port_outside_the_model_is_a_layout_error():
    with pytest.raises(panel.LayoutError, match="port 53 is not on this model"):
        panel._place([53], 52, 48)


def test_a_model_with_no_poe_is_all_main_block():
    places = panel._place(range(1, 11), 10, 0)
    assert [places[n][0] for n in (1, 3, 5, 7, 9)] == [0, panel.PITCH, 2 * panel.PITCH, 3 * panel.PITCH,
                                                       4 * panel.PITCH]


def test_the_widest_switch_fits_about_1300_px():
    """26 columns of a 52-port switch (24 main, 2 uplink) and the gap (Tim's brief: about 1300 px)."""
    ports = [panel.Port(n, x, y, "empty", "", False, "", [], "", "") for n, (x, y) in
             panel._place(range(1, 53), 52, 48).items()]
    width, height = panel.size(ports)
    assert 1200 <= width <= 1300 and height == panel.ROW + panel.HEIGHT + 2


def test_a_long_hardware_line_is_squeezed_into_its_box_and_a_short_one_is_not():
    assert panel.squeeze("Pi 5") == 0 and panel.squeeze("Acorn") == 0
    assert panel.squeeze("PCIe card") == panel.TEXT_WIDTH == panel.squeeze("Pi 4B +4")
    assert panel.TEXT_WIDTH < panel.WIDTH


# --- what a box shows, from its row -----------------------------------------------------------------------------


def row(**over):
    base = {"port": 3, "machines": [], "role": "", "unregistered": "", "unregistered_title": "", "net_ok": True,
            "net_title": "as it should be: PoE", "link_up": False, "link_title": "link down", "speed": "",
            "poe_state": "searching", "poe_title": "PoE on, nothing drawing power", "watts": "", "vlan": "2103",
            "lldp_name": ""}
    return {**base, **over}


def machine(model="Pi 5", board="Acorn", condition="operational", mark="●", label="Pi 5 2 GB 1234"):
    return {"model": model, "board": board, "condition": condition, "mark": mark, "label": label,
            "state_title": "operational", "lease_at": "2026-10-15 09:12:00"}


def test_a_box_shows_the_newest_machine_its_board_and_its_state():
    assert panel.box(row(machines=[machine()])) == ("operational", "●", ["Pi 5", "Acorn"])
    assert panel.box(row(machines=[machine("Pi 4B", "TT chip", "missing", "○")])) == ("missing", "○", ["Pi 4B", "TT chip"])


def test_several_machines_on_one_port_show_the_newest_and_how_many_more():
    many = [machine("Pi 4B", "Arty A7", "attention", "!")] + [machine("Pi 4B", "TT chip", "missing", "○")] * 4
    assert panel.box(row(machines=many)) == ("attention", "!", ["Pi 4B +4", "Arty A7"])
    words = panel.title(row(machines=many))
    assert words.count("leased an address here") == 5  # every machine in the title, with its time


def test_an_infrastructure_port_shows_its_role_and_an_empty_one_nothing():
    assert panel.box(row(role="gateway trunk")) == ("infrastructure", "", ["gateway", "trunk"])
    assert panel.box(row(role="house uplink")) == ("infrastructure", "", ["house", "uplink"])
    assert panel.box(row()) == ("empty", "", ["", ""])
    assert panel.box(row(unregistered="?")) == ("unregistered", "?", ["", ""])


def test_a_port_not_as_it_should_be_is_needs_attention_whatever_is_on_it():
    assert panel.box(row(machines=[machine()], net_ok=False)) == ("attention", "!", ["Pi 5", "Acorn"])
    assert panel.box(row(role="gateway trunk", net_ok=False))[:2] == ("attention", "!")
    assert panel.box(row(net_ok=False))[:2] == ("attention", "!")


# --- the drawing on the page ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_panel_needs_no_key_each_box_says_its_state_by_mark_edge_and_bolt(one_switch):
    seat("pi-sw1-p10", "10000000aaaa0010", lease=(1, 10))  # an operational Pi 5 with an Acorn on port 10
    view = board_switch()
    view.ports[2].pvid = 1  # port 3: a new fault (not as the inventory says)
    view.ports[11].link_up, view.ports[11].poe_state = True, "delivering"  # port 12: something, nothing registered
    one_switch(view)
    html = page_html()
    assert "mgmt-panel-key" not in html and "<ul class=\"mgmt-panel-key\"" not in html  # no key
    assert '<svg class="mgmt-panel" id="switch-1-panel"' in html and 'role="group"' in html
    # port 10: link up (solid edge), PoE delivering (⚡), the Pi's model and board, its ● in the State's colour
    ten = box_of(html, 10)
    assert 'class="mgmt-port mgmt-box-operational mgmt-edge-up"' in ten
    assert texts(ten)["mgmt-bolt"] == "⚡" and texts(ten)["mgmt-pmark"] == "●" and texts(ten)["hw"] == ["Pi 5", "Acorn"]
    assert '<text class="mgmt-pmark mgmt-state-operational"' in ten
    # port 1: an empty port, link down (dashed edge), no bolt, no mark, nothing on it
    one = box_of(html, 1)
    assert 'class="mgmt-port mgmt-box-empty mgmt-edge-down"' in one
    assert texts(one)["mgmt-bolt"] == "" and texts(one)["mgmt-pmark"] == "" and texts(one)["hw"] == ["", ""]
    # port 3: "!" where the network reading is not as it should be, its reason in the title
    three = box_of(html, 3)
    assert 'mgmt-box-attention' in three and texts(three)["mgmt-pmark"] == "!"
    assert "not as it should be: port 3 PVID 1, not 2103" in three
    # port 12: power drawn and a link, nothing registered: "?"
    twelve = box_of(html, 12)
    assert 'mgmt-box-unregistered mgmt-edge-up' in twelve and texts(twelve)["mgmt-pmark"] == "?"
    assert "not registered: a link up and PoE power drawn" in twelve
    # port 47: the gateway trunk by its role
    assert texts(box_of(html, 47))["hw"] == ["gateway", "trunk"]
    # each box links its row, its title is its accessible name
    assert re.search(r'<a href="#switch-1-port-10" aria-label="port 10 · Pi 5 2 GB 10000000aaaa0010, Acorn, '
                     r'operational, leased an address here [^"]+ · link up 1G · PoE delivering power 8.6 W', html)
    assert '<tr id="switch-1-port-10">' in html
    assert 'tabindex="0" role="region" aria-label="Front panel of Switch 1: sw-1 (GSM7252PS)"' in html
    # the panel's name counts the boxes by state, and the links and the power
    assert ('aria-label="Front panel of Switch 1: sw-1 (GSM7252PS): 1 needs attention; 1 not registered; '
            '1 operational; 12 infrastructure; 37 empty; 3 with a link up; 2 delivering PoE. A port links to its row."'
            in html)


@pytest.mark.django_db
def test_a_switch_wide_known_issue_is_one_line_above_the_drawing_not_a_mark_on_every_port(one_switch):
    view = board_switch(extra_untagged=21)  # every board port also untagged in VLAN 21: poe #16
    view.ports[4].untagged_vlans = [*view.ports[4].untagged_vlans, 99]  # port 5: and a new one
    one_switch(view)
    sw = page_json()[0]
    assert sw["known_issues"] == [{"text": "40 board ports also untagged in VLAN 21", "issue": "poe #16",
                                   "url": "https://github.com/fpgas-online/fpgas.online-poe/issues/16"}]
    ports = {p["port"]: p for p in sw["panel"]}
    assert ports[1]["kind"] == "empty" and ports[1]["mark"] == ""  # the known cause marks no port
    assert "known: also untagged in VLAN 21 (poe #16)" in ports[1]["title"]
    assert ports[5]["kind"] == "attention" and ports[5]["mark"] == "!"  # VLAN 99 has no known cause
    html = page_html()
    section = html.split('id="switch-1"')[1]
    assert section.count('<ul class="mgmt-known"><li>40 board ports also untagged in VLAN 21: '
                         '<a href="https://github.com/fpgas-online/fpgas.online-poe/issues/16">poe #16</a></li></ul>') == 1
    assert section.index('class="mgmt-known"') < section.index('class="mgmt-panel"')  # above the drawing
    assert "mgmt-port-known" not in html and "mgmt-port-wrong" not in html


@pytest.mark.django_db
def test_the_refresh_script_keeps_the_panel_with_the_table(one_switch):
    one_switch(board_switch())
    script = page_html().split("<script>")[1].split("</script>")[0]
    assert 'getElementById(anchor + "-panel-" + p.port)' in script and 'querySelector("title").textContent' in script
    assert '"mgmt-port mgmt-box-" + p.kind + " mgmt-edge-" + (p.link_up ? "up" : "down")' in script
    assert 'querySelector(".mgmt-bolt").textContent = p.bolt' in script
    assert 'pm.textContent = p.mark' in script and 'querySelectorAll(".mgmt-hw")' in script
    assert 'g.parentNode.setAttribute("aria-label", p.title)' in script
    assert 'svg.setAttribute("aria-label", s.panel_label' in script
    assert 'tr.id = anchor + "-port-" + p.port' in script and "innerHTML" not in script
    assert "known(box, s.known_issues)" in script and "MgmtTable.resort(table)" in script
    # a port that came or went: the page is drawn afresh, not left with a stale layout
    panel_part = script.split("the front panel (#90, #130)")[1]
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
