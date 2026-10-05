"""The Tiny Tapeout boards the fleet's boot checks reported, and the page each one gets.

The boot check (fpgas-verify, on every Pi) is the authority on what is
connected: it says which board is on which Pi and what it is. Nothing here,
and no catalogue, says where a board is plugged in, and no catalogue is
needed for a board to appear with every feature of its kind. A catalogue row
(models.Board) may give a board, named by its USB serial, a page address and
words a person wrote; it decides no feature.

What a board's page offers is keyed on the device the check reported:

* `chip` "fpga" (an FPGA demo board): the Commander, the gallery and upload;
* any other `chip` (a board with a Tiny Tapeout chip): the Commander;
* no `chip` (the check found the board and could not read what it is): a page
  that says so, with the check's own reason. The camera and Reset are still
  there: they are the Pi's.

Where a board is comes from its Pi's own registration at the moment of the
request (fleet.services.reporting_machines): the Pi's hostname gives its
address, its camera and its switch port, as on the /fpgas/ pages.
"""

import logging
import re
from dataclasses import dataclass, field

from fleet.services import reporting_machines
from pibfpgas.pis import Pi

from .models import Board

log = logging.getLogger(__name__)

FPGA, ASIC, UNKNOWN = "fpga", "asic", "unknown"
KIND_TITLES = {FPGA: "FPGA emulation", ASIC: "TT ASIC", UNKNOWN: "not identified", "kianv": "KianV RISC-V"}
# A board's USB serial as it may appear in a page address. The broker takes reports from anything on the site
# LAN, so a serial that is not plainly one is not given a page.
_SERIAL = re.compile(r"[0-9a-f]{8,32}")
# The address of a board no catalogue row names.
UNLISTED_PREFIX = "tt-"
# rpi-hwid's fields that say why a board's identity was not read.
_WHY_NOT = ("tinytapeout_error", "tinytapeout_note")
# The Commander that drives a board on firmware older than SDK 2: the main one needs SDK 2 or newer.
LEGACY_SDK = re.compile(r"[01]\.")


def _said(identity, key):
    """An identity field as text, or "" for one that was not read (absent, or sent as "-")."""
    value = str(identity.get(key, "")).strip()
    return "" if value in ("-", "None") else value


def kind_of(board):
    """(kind, reason) of a board a boot check reported: what its `chip` says. The variant is not asked: the
    check gives every Raspberry Pi USB device the variant `tt-fpga` before it has read anything."""
    identity = board.get("identity", {})
    chip = _said(identity, "chip").lower()
    if chip == FPGA:
        return FPGA, ""
    if chip:
        return ASIC, ""
    why = next((_said(identity, key) for key in _WHY_NOT if _said(identity, key)), "")
    return UNKNOWN, why or "the boot check found this board and did not read what it is"


@dataclass(frozen=True)
class Reported:
    """A Tiny Tapeout board as one Pi's boot check reported it."""
    usb_serial: str
    kind: str
    reason: str  # why the kind is UNKNOWN
    identity: dict = field(hash=False, compare=False)
    result: str  # the check's result for this board: "pass", "fail", "error", ...
    pi: Pi  # where it is now: the Pi's own registration
    pi_serial: str
    checked_in: bool  # whether that Pi has checked in recently


def reported():
    """{usb_serial: Reported} of every Tiny Tapeout board a registered Pi's boot check reported. A board two
    Pis reported (it was moved, and the Pi it left has not booted since) is on the Pi that is checking in, or
    the one seen last."""
    boards = {}
    seen = {}
    for machine in reporting_machines():
        pi = Pi.from_hostname(machine["hostname"])
        if pi is None:
            continue
        for board in machine["boards"]:
            if board["board"] != "tt":
                continue
            identity = board.get("identity", {})
            serial = _said(identity, "usb_serial") or _said(identity, "serial")
            if not _SERIAL.fullmatch(serial):
                log.warning("%s reported a Tiny Tapeout board with no usable USB serial (%r): not shown",
                            machine["hostname"], serial)
                continue
            rank = (machine["checked_in"], machine["last_seen"])
            if serial in seen and seen[serial] >= rank:
                continue
            seen[serial] = rank
            kind, reason = kind_of(board)
            boards[serial] = Reported(usb_serial=serial, kind=kind, reason=reason, identity=identity,
                                      result=board["result"], pi=pi, pi_serial=machine["serial"],
                                      checked_in=machine["checked_in"])
    return boards


@dataclass(frozen=True)
class Page:
    """One board's page: what a boot check reported (`live`), with the words of its catalogue row (`row`) when
    it has one. Either may be missing, not both."""
    live: Reported | None
    row: Board | None

    # -- the address, and what a person wrote --
    @property
    def slug(self):
        return self.row.slug if self.row else UNLISTED_PREFIX + self.live.usb_serial

    @property
    def title(self):
        if self.row:
            return self.row.title
        if self.live.kind == FPGA:
            return f"TT FPGA demo board {self.live.usb_serial}"
        shuttle = _said(self.live.identity, "shuttle")
        return f"Tiny Tapeout board {self.live.usb_serial}" + (f" ({shuttle})" if shuttle else "")

    def _words(self, name, default=""):
        return getattr(self.row, name) if self.row else default

    blurb = property(lambda self: self._words("blurb"))
    description = property(lambda self: self._words("description"))
    pcb = property(lambda self: self._words("pcb") or (_said(self.live.identity, "demoboard") if self.live else ""))
    pmods = property(lambda self: self._words("pmods", []))
    links = property(lambda self: self._words("links", []))
    sort_order = property(lambda self: self._words("sort_order", 1000))

    # -- what the device is: the report's word, and a row's only where nothing was reported --
    @property
    def kind(self):
        return self.live.kind if self.live else self.row.kind

    @property
    def kind_title(self):
        return KIND_TITLES.get(self.kind, self.kind)

    @property
    def shuttle(self):
        """The chip's shuttle: what the board said, or a row's word for a board nothing reported."""
        if self.live:
            return _said(self.live.identity, "shuttle")
        return self.row.shuttle

    @property
    def facts(self):
        """What the board said about itself, for the page: only the fields that were read."""
        if not self.live:
            return {}
        return {key: _said(self.live.identity, key) for key in ("demoboard", "sdk", "mcu")
                if _said(self.live.identity, key)}

    @property
    def commander(self):
        """Which Commander drives the board: "legacy" for firmware older than SDK 2, by the SDK the board said."""
        return "legacy" if self.live and LEGACY_SDK.match(_said(self.live.identity, "sdk")) else "main"

    @property
    def has_commander(self):
        return self.live is not None and self.live.kind in (FPGA, ASIC)

    @property
    def has_gallery(self):
        return self.live is not None and self.live.kind == FPGA

    # -- where it is: the Pi's own registration --
    def _pi(self, name, default=None):
        return getattr(self.live.pi, name) if self.live else default

    hostname = property(lambda self: self._pi("hostname"))
    ip = property(lambda self: self._pi("ip"))
    switch = property(lambda self: self._pi("switch"))
    port = property(lambda self: self._pi("port"))
    stream_url = property(lambda self: self._pi("stream_url"))
    whep_url = property(lambda self: self._pi("whep_url"))

    @property
    def serial_ws_path(self):
        return f"/ws/board/{self.slug}/serial"

    @property
    def api_base(self):
        return f"/api/board/{self.slug}"

    @property
    def can_power_cycle(self):
        """Whether the page shows Reset: every board some Pi reported, whether or not that Pi still checks in
        (a hung board is the one to reset). The same Pis decide what /snmp/toggle accepts (reported_port)."""
        return self.live is not None


def pages():
    """Every page, in the catalogue's order with the boards no row names after them."""
    live = reported()
    listed = []
    for row in Board.objects.all():
        listed.append(Page(live=live.pop(row.usb_serial, None) if row.usb_serial else None, row=row))
    unlisted = [Page(live=board, row=None) for board in live.values()]
    return sorted(listed + unlisted, key=lambda page: (page.sort_order, page.slug))


def page(slug):
    """The page at this address, or None."""
    return next((p for p in pages() if p.slug == slug), None)


def reported_port(switch, port):
    """Whether a Pi that reported a Tiny Tapeout board is registered on this switch and port."""
    return any((board.pi.switch, board.pi.port) == (switch, port) for board in reported().values())
