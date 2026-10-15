"""Which switch ports the PoE endpoints (/snmp/status, /snmp/toggle,
/snmp/power, from the fpgas-online-poe package) may touch.

Visitors use the boards, and pressing Reset is part of that: no login, no
session. A board needs its Reset most when it has hung, is restarting or is
failing its check, so none of that is asked here.

Tim, 16 Oct 2026: "All access ports should *always* be switchable." On any
host of a site with a switches file (the per-port-VLAN switches), welland's
and tinytapeout's alike, every access port is switched, a board registered
or reported on it or not: an empty port, or a device that hung before it
registered, is powered from /management/switches/ as a board's is. The
package refuses every port the switches file does not make an access port
(trunks, uplinks, ports outside the access range) before it asks here, and
pibfpgas.checks refuses to run beside a package without that bound, so
nothing beyond an access port is reachable.

A flat site (one switch, no switches file) has nothing that says which of its
ports are access ports, so there a port is switched only as follows.

settings.SNMP_SWITCH_PORT_POLICY names board_port(); the endpoints ask it
before they speak to a switch and refuse (403) what it does not allow. It
answers from the site's own data at the moment of the request, so there is
no list of ports to keep.

* On the Tiny Tapeout site (the host TTSiteHostMiddleware serves ttsite.urls
  on): the port a Pi is registered on whose boot check reported a Tiny
  Tapeout board (ttsite.boards.reported_port), on either switch. What counts
  is the Pi's last report that named any board, from whichever boot: the Pi
  may since have stopped checking in, be restarting, be running its check
  again, or have found nothing this boot, and each of those is when a board
  needs its Reset. A Pi whose check has since named another kind of board is
  no longer this site's. No catalogue is asked: a list says nothing about
  where a board is.
* On every other host: a port a board has registered on. Some machine
  in the fleet registry has a hostname that names exactly that switch and port
  (pi-sw<s>-p<p>, or pi<p> at a flat site). Whether it is online, when it
  last checked in, how its FPGA check went and whether its page is listed
  are deliberately not asked: a hung board has stopped checking in, and is
  the one to reset. A machine that moved keeps only its newest hostname, so
  it names one port.

A registration is what a board says about itself, and the fleet broker
takes it from anything on the site LAN; on a flat site a forged one can name
any port, as no switches file bounds it.
"""

from fleet.models import Machine
from snmp_switch.switches import configured_specs, is_access_port
from ttsite.boards import reported_port, reported_ports
from ttsite.middleware import serves_ttsite

from .pis import Pi


def _spec(switch):
    """The switches file's description of switch `switch`, or None for a switch it does not describe."""
    return next((spec for spec in configured_specs() if spec.index == switch), None)


def _is_access_port(switch, port):
    """Whether (switch, port) is an access port by the switches file. The package refuses every other port before it
    asks this policy; the policy checks the same bound again, so it holds even if a later package asked first."""
    spec = _spec(switch)
    return spec is not None and is_access_port(spec, port)


def registered_on(switch, port):
    """Whether any machine in the fleet registry is registered on exactly
    this switch and port. Pi.from_hostname reads the name whole and in its
    one spelling, so nothing that only resembles the port's name counts."""
    name = Pi(port=port, switch=switch).hostname
    # the registered name may carry a domain after the short name
    for hostname in Machine.objects.filter(hostname__startswith=name).values_list("hostname", flat=True):
        pi = Pi.from_hostname(hostname.split(".")[0])
        if pi is not None and (pi.switch, pi.port) == (switch, port):
            return True
    return False


def board_port(request, switch, port):
    """Whether (switch, port) is a board's port on the site answering
    `request`. `switch` is the switch's index, or None at a flat site (one
    switch, Pis registered as pi<port>); `port` is an int."""
    if switch is not None:  # every access port (Tim, 16 Oct 2026)
        return _is_access_port(switch, port)
    if serves_ttsite(request):
        return reported_port(switch, port)
    return registered_on(switch, port)


def registered_ports(switch):
    """The ports of `switch` that registered_on() accepts, from ONE query (registered_on asks one per port)."""
    prefix = "pi" if switch is None else Pi(port=0, switch=switch).hostname[:-1]
    ports = set()
    for hostname in Machine.objects.filter(hostname__startswith=prefix).values_list("hostname", flat=True):
        pi = Pi.from_hostname(hostname.split(".")[0])
        if pi is not None and pi.switch == switch:
            ports.add(pi.port)
    return ports


def board_ports(request, switch, ports):
    """{port for port in ports if board_port(request, switch, port)}, answered from one pass over the registry
    instead of one per port: the switch dashboard asks for every port of every switch on each refresh."""
    if switch is not None:
        spec = _spec(switch)
        return {port for port in ports if spec is not None and is_access_port(spec, port)}
    if serves_ttsite(request):
        reported = reported_ports()
        return {port for port in ports if (switch, port) in reported}
    registered = registered_ports(switch)
    return {port for port in ports if port in registered}
