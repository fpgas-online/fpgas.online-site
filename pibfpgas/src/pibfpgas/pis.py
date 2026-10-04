"""The FPGA board hosts shown on the /fpgas/ pages, from fleet registration
alone: nothing about them is stored here.

A Pi's identity is its registered hostname, and everything else derives from
it. ``pi-sw<s>-p<p>`` is the VLAN-per-port scheme (welland: 10.21.<s>.<p>,
gateway ssh forward <s><pp>22); ``pi<p>`` is the legacy flat scheme (PS1:
10.21.0.<100+p>, forward <100+p>22). What is on it is what its FPGA check
reported this boot (fleet.services.fpga_reports).

A Tiny Tapeout board is shown here only when it is an FPGA board; an ASIC
board is on tinytapeout.fpgas.online alone
(docs/superpowers/specs/2026-10-04-tinytapeout-listing-design.md).
"""

import logging
import re
from dataclasses import dataclass, replace

from fleet.services import offered_boards

log = logging.getLogger(__name__)

# ASCII digits, no leading zero, matched whole (fullmatch: `$` would let a
# trailing newline through): each port has exactly one spelling, so no
# registration can alias another Pi's page.
_NUMBER = r"[1-9][0-9]*"
_HOSTNAME = re.compile(
    rf"pi(?:-sw(?P<switch>{_NUMBER})-p(?P<port>{_NUMBER})|(?P<flat>{_NUMBER}))")

# fpgas-verify's board kinds (fpga-verified "board<i>") as people know them;
# the same titles as its own status table (fpgas.online-test-designs
# scripts/collect_verify_status.py BOARD_TITLES).
BOARD_TITLES = {
    "acorn": "Acorn",
    "arty": "Arty A7",
    "netv2": "NeTV2",
    "fomu": "Fomu EVT",
}


# A Tiny Tapeout board is named by what it is, which is its variant.
TT_TITLES = {
    "tt-fpga": "TT FPGA",
    "tt-asic": "TT ASIC",
}

# The Tiny Tapeout variants this site's own list shows. An allow-list: an
# ASIC board, one that was not identified, and anything unexpected are not
# shown.
TT_SHOWN_HERE = ("tt-fpga",)


def is_tiny_tapeout(board):
    """Whether a reported board is, or says it is, a Tiny Tapeout board: by
    the board module that checked it or by its identity's kind."""
    return "tt" in (board["board"].lower(), board.get("identity", {}).get("kind", "").lower())


def shown_here(board):
    """Whether this site's own pages show a board a Pi reported. An entry
    that could not be read is not shown. A Tiny Tapeout board is shown only
    when the board module and the identity's kind (when there is one) both
    say `tt` and the variant is on the allow-list: anything that disagrees
    with itself is hidden rather than believed."""
    if not board["board"]:
        return False
    if not is_tiny_tapeout(board):
        return True
    kind = board.get("identity", {}).get("kind", "tt")
    return board["board"] == "tt" and kind == "tt" and board.get("variant") in TT_SHOWN_HERE


def board_title(board):
    """A reported board as people read it: its title and, when one was read,
    its variant ("Acorn (cle-215+)"). A Tiny Tapeout board's title says its
    variant already. A board this site has no title for keeps the kind the
    Pi sent."""
    if board["board"] == "tt" and board.get("variant") in TT_TITLES:
        return TT_TITLES[board["variant"]]
    title = BOARD_TITLES.get(board["board"], "Tiny Tapeout" if board["board"] == "tt" else board["board"])
    variant = board.get("variant")
    return f"{title} ({variant})" if variant and variant != "-" else title


@dataclass(frozen=True)
class Pi:
    port: int
    switch: int | None = None  # None: the legacy flat scheme
    found: tuple = ()  # the boards its FPGA check reported this boot

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
        return ", ".join(board_title(board) for board in self.found if board["board"])


def offered():
    """The Pis to offer, in (switch, port) order: those whose registered
    machine has checked in recently and passed its FPGA check this boot
    (fleet.services.offered_boards). The others still boot and take ssh, so
    someone can log in and see what is wrong, but users are not sent to
    them."""
    pis = (Pi.from_hostname(host, boards) for host, boards in offered_boards().items())
    return sorted((pi for pi in pis if pi is not None),
                  key=lambda pi: (pi.switch or 0, pi.port))


def offered_pi(hostname):
    """The offered Pi with this hostname, or None. What ping and upload go
    by: they serve every offered Pi, whatever boards it carries."""
    return next((pi for pi in offered() if pi.hostname == hostname), None)


def listed():
    """The offered Pis this site's own pages show, each with only the boards
    shown here (shown_here). A Pi that reported boards and has none left is
    not listed; one whose report named no board at all is listed as it
    always was."""
    pis = []
    for pi in offered():
        shown = tuple(board for board in pi.found if shown_here(board))
        if shown or not pi.found:
            pis.append(replace(pi, found=shown))
        else:
            log.info("%s is not listed: none of the boards it reported is shown here", pi.hostname)
    return pis


def listed_pi(hostname):
    """The listed Pi with this hostname, or None."""
    return next((pi for pi in listed() if pi.hostname == hostname), None)
