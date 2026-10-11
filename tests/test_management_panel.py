"""A switch's front panel on the switch page (#90): each port where it is, coloured by its state with the words in its
title, marked where it is not as the inventory says (a known cause apart from a new fault), linked to its row."""

import json

import pytest
from django.test import Client

from management import panel
from management.views import switches as page
from tests.test_management_switches import HOSTS, READ_AT, PortView, SwitchView, spec


def board_switch(extra_untagged=None, ports=52, poe_ports=48):
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
    return SwitchView(index=1, name="sw-1", model="GSM7252PS", reachable=True, error="", read_at=READ_AT,
                      good_at=READ_AT, ports=view_ports)


@pytest.fixture
def one_switch(monkeypatch):
    def install(view):
        monkeypatch.setattr(page, "_reader", lambda: lambda cache, *a, **k: [view])
        monkeypatch.setattr(page, "_inventory", lambda: {1: spec()})
        return view
    return install


def test_the_ports_are_in_two_rows_odd_above_even_then_the_uplink_block():
    places = panel._place(board_switch().ports)
    assert places[1][1] == places[3][1] < places[2][1] == places[4][1]  # odd above even
    assert places[1][0] == places[2][0] < places[3][0]  # 1 over 2, then 3 over 4
    # 49..52 carry no PoE: the uplink block, after a gap
    assert places[49][0] - places[47][0] == panel.PITCH + panel.GAP and places[49][1] < places[50][1]


def test_each_state_is_a_word():
    view = board_switch()
    states = {p.port: panel.state(p) for p in view.ports}
    assert (states[10], states[47], states[1], states[49]) == ("power", "up", "searching", "down")
    assert set(panel.STATE_WORDS) == {"power", "up", "searching", "off", "down", "fault"}


@pytest.mark.django_db
def test_the_panel_is_drawn_with_its_states_marks_titles_and_links(one_switch):
    view = board_switch()
    view.ports[2].pvid = 1  # port 3: a new fault
    one_switch(view)
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    assert '<svg class="mgmt-panel"' in html and 'role="img"' in html
    assert '<a href="#switch-1-port-10"><g id="switch-1-panel-10" class="mgmt-port mgmt-port-power">' in html
    assert 'id="switch-1-panel-3" class="mgmt-port mgmt-port-searching mgmt-port-wrong">' in html
    assert "port 3 · link down, PoE on and searching" in html and "not as the inventory says: port 3 PVID 1" in html
    assert '<tr id="switch-1-port-10">' in html  # the row the panel links to
    assert "red dashed edge: not as the inventory says" in html  # the key, in words


@pytest.mark.django_db
def test_a_known_cause_is_marked_apart_from_a_new_fault(one_switch):
    view = board_switch(extra_untagged=21)  # every board port also untagged in VLAN 21: poe #16
    view.ports[4].untagged_vlans = [*view.ports[4].untagged_vlans, 99]  # port 5: and a new one
    one_switch(view)
    data = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)["switches"][0]
    ports = {p["port"]: p for p in data["panel"]}
    assert ports[1]["mismatch"] and ports[1]["known"] and "known: poe #16" in ports[1]["title"]
    assert ports[5]["mismatch"] and not ports[5]["known"]  # VLAN 99 has no known cause
    assert not ports[47]["mismatch"] and not ports[49]["mismatch"]
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    assert 'id="switch-1-panel-1" class="mgmt-port mgmt-port-searching mgmt-port-known">' in html
    assert 'id="switch-1-panel-5" class="mgmt-port mgmt-port-searching mgmt-port-wrong">' in html


@pytest.mark.django_db
def test_the_refresh_script_keeps_the_panel_with_the_table(one_switch):
    one_switch(board_switch())
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    script = html.split("<script>")[1].split("</script>")[0]
    assert 'getElementById(anchor + "-panel-" + p.port)' in script and 'querySelector("title").textContent' in script
    assert 'tr.id = anchor + "-port-" + p.port' in script and "innerHTML" not in script


@pytest.mark.django_db
def test_a_switch_that_does_not_answer_has_no_panel(monkeypatch):
    dead = SwitchView(index=2, name="sw-2", model="GSM7252PS", reachable=False, error="no answer", read_at=READ_AT)
    monkeypatch.setattr(page, "_reader", lambda: lambda cache, *a, **k: [dead])
    data = json.loads(Client(HTTP_HOST=HOSTS[0]).get("/management/switches.json").content)["switches"][0]
    assert data["panel"] == [] and data["panel_width"] == 0
