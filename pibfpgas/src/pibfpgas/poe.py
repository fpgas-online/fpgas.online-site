"""Which switch ports the PoE endpoints (/snmp/status, /snmp/toggle, from the
fpgas-online-poe package) may touch: a board's own port, and nothing beyond.

Visitors use the boards, and pressing Reset is part of that: no login, no
session. A board needs its Reset most when it has hung, is restarting or is
failing its check, so none of that is asked here. What is refused is a port
that is not a board's.

settings.SNMP_SWITCH_PORT_POLICY names board_port(); the endpoints ask it
before they speak to a switch and refuse (403) what it does not allow. It
answers from the site's own data at the moment of the request, so there is
no list of ports to keep.

* On the Tiny Tapeout site (the host TTSiteHostMiddleware serves ttsite.urls
  on): the port a Pi is registered on whose boot check reported a Tiny
  Tapeout board (ttsite.boards.reported_port), on either switch. The Pi's
  report from the boot it last ran counts, whether or not it still checks in:
  a hung board is the one to reset. No catalogue is asked: a list says
  nothing about where a board is.
* On every other host: a port a board has registered on. Some machine in the
  fleet registry has a hostname that names exactly that switch and port
  (pi-sw<s>-p<p>, or pi<p> at a flat site). Whether it is online, when it
  last checked in, how its FPGA check went and whether its page is listed
  are deliberately not asked: a hung board has stopped checking in, and is
  the one to reset. A machine that moved keeps only its newest hostname, so
  it names one port.

A registration is what a board says about itself, and the fleet broker
takes it from anything on the site LAN, so this alone would let a forged one
name any port. The package bounds it: before it asks here it refuses every
port the switches file does not make an access port (trunks, uplinks, ports
outside the access range). A forged registration can therefore name another
access port at most: another board, which any visitor may reset anyway, or
an empty port. A flat site has no switches file and so no such bound.
"""

from fleet.models import Machine
from ttsite.boards import reported_port
from ttsite.middleware import serves_ttsite

from .pis import Pi


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
    if serves_ttsite(request):
        return reported_port(switch, port)
    return registered_on(switch, port)
