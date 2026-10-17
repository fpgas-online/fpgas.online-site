"""A Pi's kernel console lines of this boot, as the gateway's netconsole receiver stored them (round 6 item 5, Tim,
18 Oct 2026: "What is the state of netconsole logs? They should be appearing on pages like
https://welland.fpgas.online/fleet/00000000cc479fd1/").

Every Pi's kernel command line sends its kernel log to the gateway over UDP (netconsole=@/eth-uplink,@10.21.0.1/).
The receiver (fpgas.online-infra's netconsole role) appends each line to ``<NETCONSOLE_DIR>/<sender IPv4>.log`` as
``<receive time, ISO 8601 with its offset> <the kernel's text>``. A port's address is the same for every Pi that sits
on it, so the file holds earlier Pis' lines too: this boot's are the ones received since the Pi booted (its last
status's time less the uptime it reported), from the address its newest lease gave it.

Nothing here guesses: a site with no NETCONSOLE_DIR, a Pi with no lease or no uptime, a line not in the receiver's
form, each is said in the section (the page stands), never shown as an empty console."""

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings

# The most lines the page shows (the newest), and the most of the file's end it reads for them.
LIMIT = 300
READ_BYTES = 1024 * 1024


class Unavailable(Exception):
    """This boot's lines cannot be shown; the message says why, in the page's words."""


@dataclass
class Console:
    address: str
    path: str
    since: datetime  # the boot's start, the gateway's clock
    lines: list = field(default_factory=list)  # [(received, text)], oldest first, at most LIMIT
    count: int = 0  # this boot's lines in what was read
    partial: bool = False  # the read began after the boot's start: older lines of this boot were not read


def directory():
    """The receiver's directory, from the setting; Unavailable when the site has none."""
    path = getattr(settings, "NETCONSOLE_DIR", "")
    if not path:
        raise Unavailable("this site has no netconsole receiver configured (NETCONSOLE_DIR is not set)")
    return Path(path)


def boot_start(machine):
    """When the Pi's present boot began, by the gateway's clock: its last status's time less the uptime it reported."""
    if not machine.last_seen or not machine.last_uptime_s:
        raise Unavailable("the Pi has reported no uptime, so this boot's start is not known")
    return machine.last_seen - timedelta(seconds=machine.last_uptime_s)


def address(machine):
    """The IPv4 address the gateway's newest lease record of the Pi gave it."""
    record = machine.gateway_records.filter(kind="lease").order_by("-at", "-id").first()
    if record is None:
        raise Unavailable("the gateway has leased the Pi no address, so its netconsole sender is not known")
    sender = record.doc.get("address")
    if not isinstance(sender, str) or not sender:  # parse_gateway_record requires one: a record from elsewhere
        raise Unavailable(f"the gateway's newest lease record of the Pi (at {record.at.isoformat()}) carries no address")
    return sender


def parse(line, path, number):
    """(received, text) of one stored line; ValueError, naming the file and line, for one not in the receiver's form."""
    stamp, sep, text = line.partition(" ")
    try:
        received = datetime.fromisoformat(stamp)
    except ValueError:
        received = None
    if not sep or received is None or received.tzinfo is None:
        raise ValueError(f"{path} line {number} from the end is not '<time with offset> <text>': {line[:80]!r}")
    return received, text


def read(machine, limit=LIMIT, read_bytes=READ_BYTES):
    """This boot's lines of `machine` (a fleet Machine), as a Console; Unavailable says why there are none to show."""
    where = directory()
    since = boot_start(machine)
    sender = address(machine)
    path = where / f"{sender}.log"
    console = Console(address=sender, path=str(path), since=since)
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            start = max(0, size - read_bytes)
            f.seek(start)
            data = f.read()
    except FileNotFoundError:
        return console  # nothing received from that address yet: an empty console, said as such by the page
    except OSError as exc:
        raise Unavailable(f"the netconsole file {path} cannot be read: {exc.strerror}") from exc
    rows = data.decode("utf-8", errors="replace").split("\n")
    if start:
        rows = rows[1:]  # the first is cut by the seek
    if rows and rows[-1] == "":
        rows.pop()
    kept = []
    for number, line in enumerate(reversed(rows), 1):
        received, text = parse(line, path, number)
        if received < since:
            break
        kept.append((received, text))
    else:
        console.partial = bool(start)  # every line read is of this boot, and the file holds more before them
    kept.reverse()
    console.count = len(kept)
    console.lines = kept[-limit:]
    return console
