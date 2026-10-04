"""Which switch ports the PoE endpoints (/snmp/status, /snmp/toggle, from the
fpgas-online-poe package) may touch: a port with a board the site answering
the request offers, and no other.

settings.SNMP_SWITCH_PORT_POLICY names board_port(); the endpoints ask it
before they speak to a switch and refuse (403) what it does not allow. It
answers from what the pages are built from, at the moment of the request, so
there is no list of ports to keep: an uplink, a trunk, a service port or an
empty port is refused because no page offers it.

* On the Tiny Tapeout site (the host TTSiteHostMiddleware serves ttsite.urls
  on): the port of a board whose page shows the power-cycle button
  (ttsite.models.Board.can_power_cycle).
* On every other host: the port of a Pi the /fpgas/ pages list now
  (pibfpgas.pis.listed): registered, checked in recently, its FPGA check
  passed this boot, and carrying a board shown here. Only those have a page,
  and so a Reset button. A Pi that is registered but not listed is refused.

The endpoints need no login, so this decides what anyone at all may
power-cycle. Whether a visitor needs a session on the board's page as well
is a separate question, not answered here.
"""

from ttsite.middleware import serves_ttsite
from ttsite.models import Board

from .pis import listed


def board_port(request, switch, port):
    """Whether (switch, port) is a board the site answering `request` offers.
    `switch` is the switch's index, or None at a flat site (one switch, Pis
    registered as pi<port>); `port` is an int."""
    if serves_ttsite(request):
        return any(board.can_power_cycle for board in Board.objects.filter(switch=switch, port=port))
    return any((pi.switch, pi.port) == (switch, port) for pi in listed())
