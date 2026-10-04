"""The FPGA board hosts shown on the /fpgas/ pages, from fleet registration
alone: nothing about them is stored here.

A Pi's identity is its registered hostname, and everything else derives from
it. ``pi-sw<s>-p<p>`` is the VLAN-per-port scheme (welland: 10.21.<s>.<p>,
gateway ssh forward <s><pp>22); ``pi<p>`` is the legacy flat scheme (PS1:
10.21.0.<100+p>, forward <100+p>22). What is on it is what its FPGA check
found this boot (fleet.services.found_boards).
"""

import re
from dataclasses import dataclass

from fleet.services import found_boards, offered_hosts

# ASCII digits, no leading zero, matched whole (fullmatch: `$` would let a
# trailing newline through): each port has exactly one spelling, so no
# registration can alias another Pi's page.
_NUMBER = r"[1-9][0-9]*"
_HOSTNAME = re.compile(
    rf"pi(?:-sw(?P<switch>{_NUMBER})-p(?P<port>{_NUMBER})|(?P<flat>{_NUMBER}))")

# fpgas-verify's board keys (fpga-board-found "board") as people know them;
# the same titles as its own status table (fpgas.online-test-designs
# scripts/collect_verify_status.py BOARD_TITLES).
BOARD_TITLES = {
    "acorn": "Acorn",
    "arty": "Arty A7",
    "netv2": "NeTV2",
    "tt": "TT FPGA",
    "fomu": "Fomu EVT",
}


def board_title(board):
    """A found board as people read it: its title and, when one was read,
    its variant ("Acorn (cle-215+)"). A board this site has no title for
    keeps the key the Pi sent."""
    title = BOARD_TITLES.get(board["board"], board["board"])
    variant = board.get("variant")
    return f"{title} ({variant})" if variant and variant != "-" else title


@dataclass(frozen=True)
class Pi:
    port: int
    switch: int | None = None  # None: the legacy flat scheme
    found: tuple = ()  # the boards its FPGA check found this boot

    @classmethod
    def from_hostname(cls, hostname, found=()):
        """The Pi a registered hostname names, or None for one that names
        no port."""
        m = _HOSTNAME.fullmatch(hostname)
        if m is None:
            return None
        if m["flat"] is not None:
            return cls(port=int(m["flat"]), found=tuple(found))
        return cls(port=int(m["port"]), switch=int(m["switch"]), found=tuple(found))

    @property
    def hostname(self):
        if self.switch is None:
            return f"pi{self.port}"
        return f"pi-sw{self.switch}-p{self.port}"

    @property
    def ip(self):
        if self.switch is None:
            return f"10.21.0.{100 + self.port}"
        return f"10.21.{self.switch}.{self.port}"

    @property
    def ssh_port(self):
        """The gateway's per-Pi ssh dnat port (e.g. 23422 -> 10.21.2.34:22)."""
        if self.switch is None:
            return (100 + self.port) * 100 + 22
        return self.switch * 10000 + self.port * 100 + 22

    @property
    def stream_url(self):
        return f"/live/{self.hostname}.m3u8"

    @property
    def whep_url(self):
        # WebRTC low-latency live view (mediamtx via nginx /cam/<host>/whep);
        # same stream key as HLS: the hostname, not the bare port.
        return f"/cam/{self.hostname}/whep"

    @property
    def boards(self):
        return ", ".join(board_title(board) for board in self.found)


def offered():
    """The Pis to offer, in (switch, port) order: those whose registered
    machine has checked in recently and passed its FPGA check this boot
    (fleet.services.offered_hosts). The others still boot and take ssh, so
    someone can log in and see what is wrong, but users are not sent to
    them."""
    found = found_boards()
    pis = (Pi.from_hostname(host, found.get(pk, ()))
           for host, pk in offered_hosts().items())
    return sorted((pi for pi in pis if pi is not None),
                  key=lambda pi: (pi.switch or 0, pi.port))


def offered_pi(hostname):
    """The offered Pi with this hostname, or None."""
    return next((pi for pi in offered() if pi.hostname == hostname), None)
