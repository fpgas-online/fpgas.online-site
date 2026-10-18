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

import gzip
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


class NotSetUp(Unavailable):
    """The site has no netconsole receiver (yet): a state of the site, not a fault of the Pi."""


# A boot's start is a live beat's arrival less its whole-second uptime: up to a second late, and the beat's own
# travel; the lines a few seconds before it are still this boot's (a Pi on the port before it was powered off at least
# the PoE hold before this one powered on).
SLACK = timedelta(seconds=5)

# netconsole registers within seconds of the kernel's start and sends the boot's log from its first line ("Booting
# Linux on physical CPU 0x0"; seen on deploy 47's proof). So a receiver whose oldest stored line is more than this after
# the boot began was not yet receiving when the boot's first lines were sent; within it, the oldest line may simply be
# the boot's own first one.
SENDS_WITHIN = timedelta(seconds=60)


@dataclass
class Console:
    address: str
    path: str
    since: datetime | None  # the boot's start, the gateway's clock; None: not known (no live beat yet)
    lines: list = field(default_factory=list)  # [(received, text)], oldest first, at most LIMIT
    count: int = 0  # this boot's lines in what was read
    partial: bool = False  # the read stopped before the boot's start: older lines of this boot were not read
    # when the receiver's oldest stored line arrived (any Pi's; None: it holds none), read when this boot's start is
    # known and the read reached it. Later than `since`: the gateway was not receiving yet when this boot began, so
    # the boot's earlier lines were never received (round 7 item 2)
    receiving_since: datetime | None = None

    @property
    def receiver_late(self):
        """The gateway was not yet receiving when this boot's first lines were sent: its oldest stored line, from any Pi,
        arrived more than SENDS_WITHIN after the boot began."""
        return (self.since is not None and self.receiving_since is not None
                and self.receiving_since > self.since + SLACK + SENDS_WITHIN)


def directory():
    """The receiver's directory, from the setting; NotSetUp when the site has none."""
    path = getattr(settings, "NETCONSOLE_DIR", "")
    if not path:
        raise NotSetUp("the gateway's netconsole receiver is not set up on this site yet")
    return Path(path)


def boot_start(machine):
    """When the Pi's present boot began, by the gateway's clock (fleet Machine.booted_at, from its live beats), or
    None while no live beat has said (every Pi for a minute after this field arrived; a Pi that dies before its first
    beat: the one whose last lines matter most, so the page shows the newest lines it has, saying they may be of
    earlier boots)."""
    return None if machine.booted_at is None else machine.booted_at - SLACK


def address(machine):
    """The IPv4 address the gateway's newest IPv4 lease record of the Pi gave it (netconsole sends over IPv4: the
    command line names 10.21.0.1)."""
    record = machine.gateway_records.filter(kind="lease", doc__family=4).order_by("-at", "-id").first()
    if record is None:
        raise Unavailable("the gateway has leased the Pi no IPv4 address, so its netconsole sender is not known")
    sender = record.doc.get("address")
    if not isinstance(sender, str) or not sender:  # parse_gateway_record requires one: a record from elsewhere
        raise Unavailable(f"the gateway's newest IPv4 lease record of the Pi (at {record.at.isoformat()}) carries no "
                          "address")
    return sender


def parse(line, path, number):
    """(received, text) of one stored line; ValueError, naming the file and line (`number`: "3 from the end", "1"),
    for one not in the receiver's form."""
    stamp, sep, text = line.partition(" ")
    try:
        received = datetime.fromisoformat(stamp)
    except ValueError:
        received = None
    if not sep or received is None or received.tzinfo is None:
        raise ValueError(f"{path} line {number} is not '<time with offset> <text>': {line[:80]!r}")
    return received, text


def receiving_since(where):
    """When the oldest line the receiver still holds in `where` arrived, from any Pi (the first line of each file,
    rotated and compressed ones included: logrotate keeps a week, a Pi's boot is hours), or None for none at all. The
    receiver starts a file at a sender's first datagram, so the gateway was receiving from then on; whether it was
    already receiving earlier (with no Pi sending) the files cannot say, so the page states only this time (round 7 item 2, Tim, 18 Oct 2026: "Why can't I see the netconsole
    on https://welland.fpgas.online/fleet/00000000cc479fd1/?": that Pi booted before the receiver started)."""
    oldest = None
    for name in sorted(where.glob("*.log*")):
        try:
            with (gzip.open(name, "rt", encoding="utf-8", errors="replace") if name.suffix == ".gz"
                  else open(name, encoding="utf-8", errors="replace")) as f:
                first = f.readline().rstrip("\n")
        except FileNotFoundError:  # rotated away between the listing and the open
            continue
        except (OSError, EOFError) as exc:
            raise Unavailable(f"the netconsole file {name} cannot be read: {exc}") from exc
        if not first:
            continue
        received, _ = parse(first, name, "1")
        oldest = received if oldest is None else min(oldest, received)
    return oldest


def _tail(path, read_bytes):
    """(the file's last lines, newest first, within read_bytes; whether the read began after the file's start), or
    None for a file that does not exist."""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            start = max(0, size - read_bytes)
            f.seek(start)
            data = f.read()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise Unavailable(f"the netconsole file {path} cannot be read: {exc.strerror}") from exc
    rows = data.decode("utf-8", errors="replace").split("\n")
    if start:
        rows = rows[1:]  # the first is cut by the seek
    if rows and rows[-1] == "":
        rows.pop()
    return list(reversed(rows)), bool(start)


def read(machine, limit=LIMIT, read_bytes=READ_BYTES):
    """This boot's lines of `machine` (a fleet Machine), as a Console; Unavailable says why there are none to show.
    The receiver's file and, where this boot reaches back past it, the one logrotate renamed before it (<file>.1,
    left uncompressed by delaycompress): a rotation just after a boot must not hide the boot's first lines."""
    where = directory()
    since = boot_start(machine)
    sender = address(machine)
    path = where / f"{sender}.log"
    console = Console(address=sender, path=str(path), since=since)
    kept, budget = [], read_bytes
    for name in (path, path.with_name(path.name + ".1")):
        tail = _tail(name, budget)
        if tail is None:
            continue
        rows, cut = tail
        budget -= sum(len(r.encode()) + 1 for r in rows)
        for number, line in enumerate(rows, 1):
            received, text = parse(line, name, f"{number} from the end")
            if since is not None and received < since:
                break
            kept.append((received, text))
        else:
            if cut or budget <= 0:  # every line read is of this boot, and the file holds more before them
                console.partial = True
                break
            continue  # this file began after the boot's start: the boot's earlier lines are in the rotated one
        break
    else:
        # both files read whole, every line of this boot: older ones may be in a compressed rotation (<file>.2.gz)
        console.partial = path.with_name(path.name + ".2.gz").exists()
    kept.reverse()
    console.count = len(kept)
    console.lines = kept[-limit:]
    if since is not None and not console.partial:
        console.receiving_since = receiving_since(where)
    return console
