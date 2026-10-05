"""The Tiny Tapeout boards the fleet's boot checks reported, and the page each one gets.

The boot check (fpgas-verify, on every Pi) is the authority on what is
connected: it says which board is on which Pi and what it is. Nothing here,
and no catalogue, says where a board is plugged in, and no catalogue is
needed for a board to appear with every feature of its kind. A catalogue row
(models.Board) may give a board, named by its USB serial, a page address and
words a person wrote; it decides no feature.

What a board's page offers is keyed on the device the check reported in the
boot its Pi is running now:

* `chip` "fpga" (an FPGA demo board): the Commander, the gallery and upload;
* any other `chip` (a board with a Tiny Tapeout chip): the Commander;
* no `chip` (the check found the board and could not read what it is): a page
  that says so, with the check's own reason;
* not reported in this boot (the Pi is restarting, its check is running, or
  this boot's check did not find the board): a page that says that. The board
  is still that Pi's: the last check that named a board on that Pi named this
  one.

The camera and Reset are the Pi's and are on every one of those pages: a
board that has hung, is restarting or has dropped off its USB port is the one
that needs its Reset.

Where a board is comes from its Pi's own registration at the moment of the
request (fleet.services.reporting_machines): the Pi's hostname gives its
address, its camera and its switch port, as on the /fpgas/ pages.

What this trusts. The fleet broker takes registrations and reports from
anything on the site LAN, as the /fpgas/ pages already assume (pibfpgas/poe.py
says what bounds that for Reset). A machine on that LAN can therefore report a
board's serial and have that board's page lead to it. What a report can do
here is bounded to that: an address is only ever built from a registered
`pi-sw<s>-p<p>` name and must be an address; reported text is cut short and
escaped; a serial that is not plainly one gets no page.
"""

import ipaddress
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
# LAN, so a serial that is not plainly one is not given a page. The catalogue's loader holds a row to the same.
SERIAL = re.compile(r"[0-9a-f]{8,32}")
# The address of a board no catalogue row names.
UNLISTED_PREFIX = "tt-"
# The identity's fields that say why a board's own word was not read (fpgas-verify: rpi-hwid's two, and
# `error` for an identity that could not be sent).
_WHY_NOT = ("tinytapeout_error", "tinytapeout_note", "error")
# The Commander that drives a board on firmware older than SDK 2: the main one needs SDK 2 or newer.
LEGACY_SDK = re.compile(r"v?[01]\.", re.IGNORECASE)
# A shuttle's name as it may go into a link to its chip page.
_SHUTTLE = re.compile(r"[a-z0-9]{1,16}")
# How much of a reported field, and of a reason, a page shows.
MAX_FIELD, MAX_REASON = 60, 300


def _said(identity, key, limit=MAX_FIELD):
    """An identity field as text, or "" for one that was not read (absent, or sent as "-"); cut to `limit`."""
    value = str(identity.get(key, "")).strip()
    return "" if value in ("-", "None") else value[:limit]


def usb_serial_of(board):
    """The USB serial a reported board gave, or "" when it gave none that is plainly one."""
    identity = board.get("identity", {})
    serial = _said(identity, "usb_serial") or _said(identity, "serial")
    return serial if SERIAL.fullmatch(serial) else ""


def kind_of(board):
    """(kind, reason) of a board a boot check reported: what its `chip` says. The variant is not asked: the
    check gives every Raspberry Pi USB device the variant `tt-fpga` before it has read anything
    (fpgas.online-test-designs issue #124)."""
    identity = board.get("identity", {})
    chip = _said(identity, "chip").lower()
    if chip == FPGA:
        return FPGA, ""
    if chip:
        return ASIC, ""
    why = next((_said(identity, key, MAX_REASON) for key in _WHY_NOT if _said(identity, key)), "")
    return UNKNOWN, why or "the boot check found this board and did not read what it is"


# What a Pi's boot check is doing when it has not reported a board in the boot the Pi is running.
_NOT_CURRENT = {
    "": "its Raspberry Pi has restarted and the boot check has not started yet",
    "verifying": "its Raspberry Pi's boot check is running",
}


@dataclass(frozen=True)
class Reported:
    """A Tiny Tapeout board as a Pi's boot check reported it."""
    usb_serial: str
    kind: str
    reason: str  # why the kind is UNKNOWN
    identity: dict = field(hash=False, compare=False)
    result: str  # the check's result for this board: "pass", "fail", "error", ...
    pi: Pi  # where it is now: the Pi's own registration
    pi_serial: str
    checked_in: bool  # whether that Pi has checked in recently
    current: bool  # whether the check named the board in the boot the Pi is running now
    waiting: str  # when not current: what the Pi's check is doing instead


def _address(pi):
    """Whether a registered name gives an address: `pi-sw300-p99999` is a name and no address."""
    try:
        ipaddress.IPv4Address(pi.ip)
    except ValueError:
        return False
    return True


def _named():
    """(machine, its Pi, a Tiny Tapeout board of the machine's last report that named any board, its serial)
    for every such board on every registered machine that has an address."""
    for machine in reporting_machines():
        pi = Pi.from_hostname(machine["hostname"])
        if pi is None or not _address(pi):
            continue
        for last in machine["last_boards"]:
            if last["board"] != "tt":
                continue
            serial = usb_serial_of(last)
            if not serial:
                log.warning("%s reported a Tiny Tapeout board with no usable USB serial: not shown",
                            machine["hostname"])
                continue
            yield machine, pi, last, serial


def reported():
    """{usb_serial: Reported} of every Tiny Tapeout board a registered Pi's boot check reported: the boards of
    each Pi's last report that named any (fleet.services.reporting_machines), `current` when the boot the Pi
    is running named it too.

    A board several Pis name is on the one whose report came last, among the Pis that are checking in (among
    all of them when none is). So a board moved from a Pi that keeps running is on the Pi it was moved to,
    and stays there while that Pi restarts: the Pi it left still names it in a boot it is still running, and
    that older word does not win it back."""
    boards = {}
    best = {}
    for machine, pi, last, serial in _named():
        rank = (machine["checked_in"], machine["last_report"])
        if serial in best and best[serial] >= rank:
            continue
        best[serial] = rank
        now = {usb_serial_of(b): b for b in machine["boards"] if b["board"] == "tt"}
        # a Pi that has stopped checking in is not running the boot its last report came from
        current = serial in now and machine["checked_in"]
        if current:
            waiting = ""
        elif not machine["checked_in"]:
            waiting = "its Raspberry Pi has stopped reporting"
        else:
            waiting = _NOT_CURRENT.get(
                machine["state"],
                f"its Raspberry Pi's boot check did not name it this boot (the check's result: {machine['state'][:20]})")
        board = now.get(serial, last)
        kind, reason = kind_of(board)
        boards[serial] = Reported(
            usb_serial=serial, kind=kind, reason=reason, identity=board.get("identity", {}),
            result=str(board["result"])[:20], pi=pi, pi_serial=machine["serial"],
            checked_in=machine["checked_in"], current=current, waiting=waiting)
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
        return f"Tiny Tapeout board {self.live.usb_serial}" + (f" ({self.shuttle})" if self.shuttle else "")

    def _words(self, name, default=""):
        return getattr(self.row, name) if self.row else default

    blurb = property(lambda self: self._words("blurb"))
    description = property(lambda self: self._words("description"))
    pcb = property(lambda self: self._words("pcb") or self.facts.get("demoboard", ""))
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
        """The chip's shuttle: what the board said (when it is a name), or a row's word for a board nothing
        reported."""
        if self.live:
            said = _said(self.live.identity, "shuttle").lower()
            return said if _SHUTTLE.fullmatch(said) else ""
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
        """Which Commander drives the board: "legacy" for firmware older than SDK 2, by the SDK the board said.
        A board that named no SDK gets the main one."""
        return "legacy" if LEGACY_SDK.match(self.facts.get("sdk", "")) else "main"

    @property
    def has_commander(self):
        return self.live is not None and self.live.current and self.live.kind in (FPGA, ASIC)

    @property
    def has_gallery(self):
        return self.live is not None and self.live.current and self.live.kind == FPGA

    @property
    def why_no_controls(self):
        """For a board some Pi reported whose page offers no controls: why, for the visitor."""
        if self.live is None or self.has_commander:
            return ""
        if not self.live.current:
            return f"This board is not ready: {self.live.waiting}."
        return ("The boot check on this board's Raspberry Pi found the board but could not read what it is: "
                f"{self.live.reason}")

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
        """Whether the page shows Reset: every board some Pi's check named, whether or not that Pi still checks
        in, is restarting, or found the board this boot (a hung board is the one to reset). The same Pis
        decide what /snmp/toggle accepts (reported_port)."""
        return self.live is not None


def pages():
    """Every page, in the catalogue's order with the boards no row names after them."""
    live = reported()
    listed = []
    for row in Board.objects.all():
        listed.append(Page(live=live.pop(row.usb_serial, None) if row.usb_serial else None, row=row))
    taken = {page.slug for page in listed}
    # ttsite_loadboards refuses a row whose slug is a board's own address; one made by hand does not hide a board
    unlisted = [Page(live=board, row=None) for board in live.values()
                if UNLISTED_PREFIX + board.usb_serial not in taken]
    return sorted(listed + unlisted, key=lambda page: (page.sort_order, page.slug))


def page(slug):
    """The page at this address, or None."""
    return next((p for p in pages() if p.slug == slug), None)


def reported_port(switch, port):
    """Whether the Pi registered on this switch and port is one whose boot check named a Tiny Tapeout board
    (in its last report that named any board, whatever the Pi is doing now). Every such Pi counts, also one
    whose board another Pi has named since: until its own check says otherwise the board may still be there."""
    return any((pi.switch, pi.port) == (switch, port) for _, pi, _, _ in _named())
