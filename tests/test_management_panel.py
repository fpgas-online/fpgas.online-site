"""A switch's front panel on the switch page (#90, #130): each port where it is, drawn so that it needs no key (Tim, 15
Oct 2026): its number, the hardware the gateway last saw on it, its /fleet/ State mark and fill (always the machine's
own), the Net column's ✗ apart where the network reading is not as it should be, a link light, green while the link is up and
dark while it is down, ⚡ while PoE delivers; a switch-wide known issue one line above the drawing; each port's
words in its title, linked to its row; a narrow drawing for a phone."""

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


def box_of(html, port, switch=1, suffix=""):
    """The served <g> of a port's box (in the narrow drawing with suffix "-n"), its markup up to its end."""
    return re.search(rf'<g id="switch-{switch}-panel-{port}{suffix}" .*?</g>', html, re.S).group(0)


def texts(box):
    """{class: text} of a box's text elements (the hardware lines as a list)."""
    found = re.findall(r'<text class="([^"]+)"[^>]*>([^<]*)</text>', box)
    out = {cls.split()[0]: text for cls, text in found if not cls.startswith("mgmt-hw")}
    out["hw"] = [text for cls, text in found if cls.startswith("mgmt-hw")]
    return out


def test_the_narrow_drawing_cuts_the_rows_into_bands_one_under_the_other():
    """A phone (the review of #137): the same two rows, NARROW_COLUMNS columns a band, the uplink block a band of its
    own; every box inside the band's width, none on another."""
    places = panel._place(range(1, 53), 52, 48, band=panel.NARROW_COLUMNS)
    assert places[1] == (0, 0) and places[2] == (0, panel.ROW)  # odd above even, as on the switch
    band = 2 * panel.ROW + panel.BAND_GAP
    n = panel.NARROW_COLUMNS
    assert places[2 * n + 1] == (0, band)  # the first port of the second band
    assert places[2 * n - 1] == ((n - 1) * panel.PITCH, 0)  # the last column of the first band
    assert places[49] == (0, 4 * band) and places[52] == (panel.PITCH, 4 * band + panel.ROW)  # uplinks: band 5
    assert max(x for x, _ in places.values()) + panel.WIDTH <= 6 * panel.PITCH
    assert len(set(places.values())) == 52


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


def test_the_widest_switch_fits_about_1300_px_and_a_phone_gets_six_columns():
    """26 columns of a 52-port switch (24 main, 2 uplink) and the gap (Tim's brief: about 1300 px); the narrow drawing
    fits a 390 px phone (16 px gutters) at full size, so its 11 px text stays 11 px."""
    wide = panel._place(range(1, 53), 52, 48)
    narrow = panel._place(range(1, 53), 52, 48, band=panel.NARROW_COLUMNS)
    ports = [panel.Port(n, *wide[n], *narrow[n], "empty", "", True, False, "", "", [], "", "") for n in wide]
    width, height = panel.size(ports)
    assert 1250 <= width <= 1370 and height == panel.ROW + panel.HEIGHT + 2
    assert panel.size(ports, narrow=True)[0] <= 390 - 2 * 16


# every word a box shows, from the page's own vocabularies: none is squeezed, each fits its line or wraps at a space
BOARD_WORDS = ("Acorn", "Arty A7", "NeTV2", "Fomu", "PCIe card", "TT FPGA", "TT chip", "TT board, not identified",
               "no board", "TT, not checked this boot", "Acorn + TT FPGA")
MODEL_WORDS = ("Pi 3B+", "Pi 3B", "Pi 4B", "Pi 5", "Pi CM4", "Pi 400", "OPi PC", "Pi Zero 2 W")


def test_a_box_line_is_never_squeezed_it_fits_or_wraps_at_a_space():
    """The review of #137: 11 px text, no glyphs squeezed to fit. CHAR_WIDTH is set from Chrome's own widths ("gateway"
    47 px, "TT FPGA" 46, "PCIe card" 52 at 11 px): those that fit are one line, "PCIe card" wraps."""
    assert panel.CHAR_WIDTH * 7 <= panel.TEXT_WIDTH < panel.CHAR_WIDTH * 9 < panel.WIDTH + 12
    assert panel.wrap("TT FPGA") == ["TT FPGA", ""] and panel.wrap("PCIe card") == ["PCIe", "card"]
    assert panel.wrap("Acorn + TT FPGA") == ["Acorn +", "TT FPGA"]
    for word in (*BOARD_WORDS, *MODEL_WORDS, *panel.ROLE_WORDS.values()):
        for line in panel.wrap(word):
            assert panel.fits(line), (word, line)
    for word in panel.ROLE_WORDS.values():  # a role is one short word on one line (the review of #137)
        assert panel.fits(word) and " " not in word
    assert panel.wrap("TT, not checked this boot")[-1].endswith("…")  # more than two lines hold: cut, never squeezed


# --- what a box shows, from its row -----------------------------------------------------------------------------


def row(**over):
    base = {"port": 3, "machines": [], "role": "", "unregistered": "", "unregistered_title": "", "net_ok": True,
            "net_title": "as it should be: PoE", "link_up": False, "link_title": "link down", "speed": "",
            "poe_state": "searching", "poe_title": "PoE on, searching: nothing drawing power", "watts": "", "vlan": "2103",
            "lldp_name": ""}
    return {**base, **over}


def machine(model="Pi 5", board="Acorn", condition="operational", mark="●", label="Pi 5 2 GB 1234"):
    return {"model": model, "board": board, "condition": condition, "mark": mark, "label": label,
            "state_title": "operational", "lease_at": "2026-10-15 09:12:00"}


def test_a_box_shows_the_newest_machine_its_board_and_its_state():
    assert panel.box(row(machines=[machine()])) == ("operational", "●", "", ["Pi 5", "Acorn", ""])
    assert panel.box(row(machines=[machine("Pi 4B", "TT chip", "missing", "○")])) == (
        "missing", "○", "", ["Pi 4B", "TT chip", ""])
    assert panel.box(row(machines=[machine("Pi 5", "PCIe card")]))[3] == ["Pi 5", "PCIe", "card"]


def test_several_machines_on_one_port_show_the_newest_and_how_many_more():
    many = [machine("Pi 4B", "Arty A7", "attention", "!")] + [machine("Pi 4B", "TT chip", "missing", "○")] * 4
    assert panel.box(row(machines=many)) == ("attention", "!", "+4", ["Pi 4B", "Arty A7", ""])
    words = panel.title(row(machines=many))
    assert words.count("leased an address here") == 5  # every machine in the title, with its time


def test_an_infrastructure_port_shows_its_role_as_one_word_and_an_empty_one_nothing():
    assert panel.box(row(role="gateway trunk")) == ("infrastructure", "", "", ["gateway", "", ""])
    assert panel.box(row(role="house uplink"))[3][0] == "uplink"
    assert panel.box(row(role="infrastructure"))[3][0] == "infra"
    assert panel.box(row()) == ("empty", "", "", ["", "", ""])
    assert panel.box(row(unregistered="?")) == ("unregistered", "?", "", ["", "", ""])


def test_the_state_mark_is_always_the_machines_own_and_net_is_a_mark_of_its_own():
    """The review of #137, finding 1: the box's State mark means what the State column means (the machine's /fleet/
    State); a port whose network reading is not as it should be shows the Net column's ✗ apart."""
    rows = [row(machines=[machine()], net_ok=False, link_title="link down", poe_state="searching")]
    (port,) = panel.panel(rows, "switch-1", 52, 48)
    assert (port.kind, port.mark, port.net) == ("operational", "●", "✗")
    (fine,) = panel.panel([row(machines=[machine()])], "switch-1", 52, 48)
    assert (fine.kind, fine.mark, fine.net) == ("operational", "●", "")
    (gw,) = panel.panel([row(role="gateway trunk", net_ok=False)], "switch-1", 52, 48)
    assert (gw.kind, gw.mark, gw.net) == ("infrastructure", "", "✗")


# --- the drawing on the page ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_panel_needs_no_key_each_box_says_its_state_by_mark_link_light_and_bolt(one_switch):
    seat("pi-sw1-p10", "10000000aaaa0010", lease=(1, 10))  # an operational Pi 5 with an Acorn on port 10
    seat("pi-sw1-p4", "10000000aaaa0004", lease=(1, 4))  # an operational Pi 5 on port 4, whose link is down
    view = board_switch()
    view.ports[2].pvid = 1  # port 3: a new fault (not as the inventory says)
    view.ports[11].link_up, view.ports[11].poe_state = True, "delivering"  # port 12: something, nothing registered
    one_switch(view)
    html = page_html()
    assert "mgmt-panel-key" not in html  # no key
    assert '<svg class="mgmt-panel mgmt-panel-wide" id="switch-1-panel"' in html and 'role="group"' in html
    # port 10: link up (link light lit), PoE delivering (⚡), the Pi's model and board, its ● in the State's colour, no ✗
    ten = box_of(html, 10)
    assert 'class="mgmt-port mgmt-box-operational mgmt-link-up"' in ten
    assert texts(ten)["mgmt-bolt"] == "⚡" and texts(ten)["mgmt-pmark"] == "●" and texts(ten)["mgmt-net"] == ""
    assert texts(ten)["hw"] == ["Pi 5", "Acorn", ""]
    assert '<text class="mgmt-pmark mgmt-state-operational"' in ten
    # port 4: the machine's own ● (the State column's), and apart from it the Net ✗ in red: link down under it
    four = box_of(html, 4)
    assert 'mgmt-box-operational mgmt-link-down' in four
    assert texts(four)["mgmt-pmark"] == "●" and texts(four)["mgmt-net"] == "✗"
    assert '<text class="mgmt-net mgmt-test-fail"' in four and "not as it should be: link down under" in four
    # port 1: an empty port, link down (link light dark), no bolt, no mark, nothing on it
    one = box_of(html, 1)
    assert 'class="mgmt-port mgmt-box-empty mgmt-link-down"' in one
    assert texts(one)["mgmt-bolt"] == "" and texts(one)["mgmt-pmark"] == "" and texts(one)["hw"] == ["", "", ""]
    assert texts(one)["mgmt-net"] == ""
    # port 3: nothing on it, the Net ✗ where the network reading is not as it should be, its reason in the title
    three = box_of(html, 3)
    assert 'mgmt-box-empty' in three and texts(three)["mgmt-pmark"] == "" and texts(three)["mgmt-net"] == "✗"
    assert "not as it should be: port 3 PVID 1, not 2103" in three
    # port 12: power drawn and a link, nothing registered: "?"
    twelve = box_of(html, 12)
    assert 'mgmt-box-unregistered mgmt-link-up' in twelve and texts(twelve)["mgmt-pmark"] == "?"
    assert "not registered: a link up and PoE power drawn" in twelve
    # port 47: the gateway trunk by its role, one word
    assert texts(box_of(html, 47))["hw"] == ["gateway", "", ""]
    # each box links its row, its title is its accessible name
    assert re.search(r'<a href="#switch-1-port-10" aria-label="port 10 · Pi 5 2 GB 10000000aaaa0010, Acorn, '
                     r'operational, leased an address here [^"]+ · link up 1G · PoE delivering power 8.6 W', html)
    assert '<tr id="switch-1-port-10">' in html
    assert 'role="region" aria-label="Front panel of Switch 1: sw-1 (GSM7252PS)"' in html
    # the panel's name counts the boxes by state, the Net failures, the links and the power
    assert ('aria-label="Front panel of Switch 1: sw-1 (GSM7252PS): 1 not registered; 2 operational; '
            '12 infrastructure; 37 empty; 2 not as it should be; 3 with a link up; 2 delivering PoE. A port links to '
            'its row."' in html)
    # the narrow drawing: the same boxes, its own ids, scaled to the phone's width by its viewBox
    assert '<svg class="mgmt-panel mgmt-panel-narrow" id="switch-1-panel-n" viewBox="0 0 ' in html
    assert texts(box_of(html, 4, suffix="-n")) == texts(four)


@pytest.mark.django_db
def test_a_switch_wide_known_issue_is_one_line_above_the_drawing_not_a_mark_on_every_port(one_switch):
    view = board_switch(extra_untagged=21)  # every board port also untagged in VLAN 21: poe #16
    view.ports[4].untagged_vlans = [*view.ports[4].untagged_vlans, 99]  # port 5: and a new one
    one_switch(view)
    sw = page_json()[0]
    assert sw["known_issues"] == [{"text": "40 board ports also untagged in VLAN 21", "issue": "poe #16",
                                   "url": "https://github.com/fpgas-online/fpgas.online-poe/issues/16"}]
    ports = {p["port"]: p for p in sw["panel"]}
    assert ports[1]["kind"] == "empty" and ports[1]["mark"] == "" and ports[1]["net"] == ""  # no mark for it
    assert "as it should be apart from the known issue above (poe #16)" in ports[1]["title"]
    assert ports[5]["net"] == "✗" and ports[5]["mark"] == ""  # VLAN 99 has no known cause: Net ✗
    html = page_html()
    section = html.split('id="switch-1"')[1]
    assert section.count('<ul class="mgmt-known"><li>40 board ports also untagged in VLAN 21: '
                         '<a href="https://github.com/fpgas-online/fpgas.online-poe/issues/16">poe #16</a></li></ul>') == 1
    assert section.index('class="mgmt-known"') < section.index('class="mgmt-panel mgmt-panel-wide"')  # above the drawing
    assert "mgmt-port-known" not in html and "mgmt-port-wrong" not in html


@pytest.mark.django_db
def test_the_refresh_script_keeps_the_panel_with_the_table(one_switch):
    one_switch(board_switch())
    script = page_html().split("<script>")[1].split("</script>")[0]
    assert 'getElementById(anchor + "-panel-" + p.port)' in script and 'querySelector("title").textContent' in script
    assert '"mgmt-port mgmt-box-" + p.kind + " mgmt-link-" + (p.link_up ? "up" : "down")' in script
    assert 'querySelector(".mgmt-bolt").textContent = p.bolt' in script
    assert 'querySelector(".mgmt-net").textContent = p.net' in script
    assert 'querySelector(".mgmt-more").textContent = p.more' in script
    assert 'pm.textContent = p.mark' in script and 'querySelectorAll(".mgmt-hw")' in script
    assert 'g.parentNode.setAttribute("aria-label", p.title)' in script
    assert 'drawing.setAttribute("aria-label", s.panel_label' in script
    assert 'anchor + "-panel-" + p.port + "-n"' in script  # the narrow drawing's twin too
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
    assert '<tr id="switch-1-port-1" class="mgmt-empty-port">' in html


def test_an_unregistered_ports_box_names_what_it_announces_over_lldp():
    """#148: the device a reader wants named is the unregistered one: its box carries its LLDP name, wrapped."""
    assert panel.box(row(unregistered="?", lldp_name="rpi5-new-13f59c")) == (
        "unregistered", "?", "", ["rpi5-", "new-", "13f59c"])
    assert panel.wrap_name("a-very-long-name-indeed-yes", 3)[-1].endswith("…")
    assert panel.box(row(unregistered="?", lldp_name="x")) == ("unregistered", "?", "", ["x", "", ""])
    assert panel.box(row(unregistered="?")) == ("unregistered", "?", "", ["", "", ""])
