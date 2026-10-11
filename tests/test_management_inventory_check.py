"""The inventory checks behind the index's Switches rows (management/inventory_check.py)."""

from types import SimpleNamespace

from management import inventory_check as ic

SPEC = SimpleNamespace(index=1, access_ports=4, gateway_trunk_port=3, downstream_trunk_ports=(5,),
                       house_uplink_port=6)


def port(n, pvid=None, untagged=(), tagged=()):
    return SimpleNamespace(port=n, pvid=pvid, untagged_vlans=list(untagged), tagged_vlans=list(tagged))


def good():
    return SimpleNamespace(ports=[port(1, 2101, [2101]), port(2, 2102, [2102]),
                                  port(3, None, [], [2101, 2102, 2104]), port(4, 2104, [2104])])


def test_the_named_trunks_and_uplink_are_not_board_ports_even_inside_the_access_range():
    assert ic.board_ports(SPEC) == [1, 2, 4]
    assert ic.board_vlan(2, 37) == 2237


def test_a_switch_as_the_inventory_says_has_no_failure():
    assert ic.check(SPEC, good()) == []


def test_a_wrong_pvid_is_a_pvid_failure():
    view = good()
    view.ports[1].pvid = 1
    (f,) = ic.check(SPEC, view)
    assert (f.kind, f.port, f.text) == ("pvid", 2, "port 2 PVID 1, not 2102")


def test_a_board_port_also_untagged_in_vlan_21_is_an_untagged_failure_per_port():
    view = good()
    for p in (view.ports[0], view.ports[1], view.ports[3]):
        p.untagged_vlans = sorted([p.pvid, 21])
    found = ic.check(SPEC, view)
    assert [(f.kind, f.port, f.vlan) for f in found] == [("untagged", 1, 21), ("untagged", 2, 21), ("untagged", 4, 21)]
    assert found[0].text == "port 1 also untagged in VLAN 21"


def test_a_board_port_not_untagged_in_its_own_vlan_is_a_failure():
    view = good()
    view.ports[0].untagged_vlans = []
    view.ports[0].tagged_vlans = [2101]
    assert [f.text for f in ic.check(SPEC, view)] == ["port 1 not untagged in its VLAN 2101"]


def test_the_gateway_trunk_must_carry_every_board_vlan_tagged():
    view = good()
    view.ports[2].tagged_vlans = [2101]
    (f,) = ic.check(SPEC, view)
    assert (f.kind, f.text) == ("trunk", "gateway trunk lacks 2 board VLANs, first 2102")


def test_a_reading_that_cannot_be_checked_is_a_failure_not_a_pass():
    view = good()
    view.ports[0] = port(1)  # no VLAN data at all
    del view.ports[3]  # port 4 not reported
    assert [(f.kind, f.text) for f in ic.check(SPEC, view)] == [
        ("unread", "port 1 has no VLAN data"), ("unread", "port 4 not read")]
