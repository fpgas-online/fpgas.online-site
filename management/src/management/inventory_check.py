"""Is a switch as the inventory says? The checks behind the index page's Switches rows.

The inventory is switches.yml (`snmp_switch.switches.configured_specs`): per switch its access ports, the gateway
trunk, the trunks to downstream switches and the house uplink. From it each board port has one VLAN, 2000 + 100 *
switch + port (fpgas.online-vlan-per-port), and the gateway trunk carries all of them. Three checks:

* pvid: a board port's PVID is its VLAN;
* untagged: a board port is untagged in its own VLAN only;
* trunk: the gateway trunk carries every board port's VLAN tagged.

A reading that cannot be checked (a board port the switch did not report, or no VLAN data at all) is a failure of its
own kind, not a pass."""

from dataclasses import dataclass

VLAN_BASE = 2000
KINDS = ("pvid", "untagged", "trunk", "unread")  # the order the first failure is chosen in

# Known causes of "also untagged in VLAN <n>" on board ports, by the other VLAN: the issue that tracks it, named on
# the row (a note: the failure is still a failure). Keyed by VLAN, not by port: which port it is on is what the
# switch says now.
POE_ISSUES = "https://github.com/fpgas-online/fpgas.online-poe/issues/"
KNOWN_CAUSES = {
    21: ("poe #16", f"fpgas-online/fpgas.online-poe#16 ({POE_ISSUES}16): board ports are also untagged in VLAN 21"),
    5: ("poe #18", f"fpgas-online/fpgas.online-poe#18 ({POE_ISSUES}18): board ports also untagged in VLAN 5 or 121"),
    121: ("poe #18", f"fpgas-online/fpgas.online-poe#18 ({POE_ISSUES}18): board ports also untagged in VLAN 5 or 121"),
}


@dataclass(frozen=True)
class Failure:
    kind: str
    port: int | None
    text: str
    vlan: int | None = None  # the other VLAN, for an "also untagged" failure


def board_vlan(index, port):
    return VLAN_BASE + 100 * index + port


def board_ports(spec):
    """The ports of the switch the inventory says have a board: 1..access_ports without the ports it gives another job
    (the same bound as snmp_switch.switches.is_access_port, which is not imported: it is the poe package's)."""
    others = {spec.gateway_trunk_port, spec.house_uplink_port, *(spec.downstream_trunk_ports or ())}
    return [p for p in range(1, spec.access_ports + 1) if p not in others]


def check(spec, view):
    """The Failures of one reachable switch `view` against its `spec`, first kind first, then by port."""
    found = []
    ports = {p.port: p for p in view.ports}
    wanted = {}
    for number in board_ports(spec):
        vlan = wanted[number] = board_vlan(spec.index, number)
        port = ports.get(number)
        if port is None:
            found.append(Failure("unread", number, f"port {number} not read"))
            continue
        untagged = set(port.untagged_vlans or ())
        if port.pvid is None and not untagged and not (port.tagged_vlans or ()):
            found.append(Failure("unread", number, f"port {number} has no VLAN data"))
            continue
        if port.pvid != vlan:
            found.append(Failure("pvid", number, f"port {number} PVID {port.pvid}, not {vlan}"))
        for other in sorted(untagged - {vlan}):
            found.append(Failure("untagged", number, f"port {number} also untagged in VLAN {other}", other))
        if vlan not in untagged:
            found.append(Failure("untagged", number, f"port {number} not untagged in its VLAN {vlan}"))
    trunk_port = spec.gateway_trunk_port
    if trunk_port is not None:
        trunk = ports.get(trunk_port)
        if trunk is None:
            found.append(Failure("unread", trunk_port, f"gateway trunk (port {trunk_port}) not read"))
        else:
            absent = [v for v in wanted.values() if v not in set(trunk.tagged_vlans or ())]
            if absent:
                found.append(Failure("trunk", trunk_port,
                                     f"gateway trunk lacks {len(absent)} board VLANs, first {absent[0]}"))
    return sorted(found, key=lambda f: (KINDS.index(f.kind), f.port))
