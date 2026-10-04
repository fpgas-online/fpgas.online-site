"""rpi-hwid's label-input document for one Pi, built from what the Pi sends.

The Pi's registration gives its serial, model, revision, memory and MACs.
fpgas-verify adds a `pi-identified` event (the Pi facts rpi-hwid reads: power
class, fan, RTC battery, HAT, MACs with their signal, ...) and one
`fpga-board-identified` event per FPGA board. Both carry flat-string details
named as rpi-hwid's Summary / FpgaBoard / TinyTapeoutBoard fields, with a
`schema` of `pi-identity/1` or `fpga-identity/1` (the label contract, v1).

`build` only gathers: it never guesses a value that was not sent. What a full
label still needs is rpi-hwid's to say (`missing`), and the document is
written only by rpi-hwid's own serialiser (`dumps`), so the site's file and
the one `rpi-hwid labels --this-host` makes on the Pi can be compared byte
for byte (`label_input.comparable`, which leaves out `sources`).

Not read and read-as-none differ (label contract §17): in an event an
absent key was not read and "-" was read and is none; in the document an
absent field takes rpi-hwid's default and null means not read.

The broker is open on the site LAN, so an event may carry anything. Each
event's part of the document is checked by rpi-hwid's own label_input before
it is taken, and one it refuses is dropped with a note naming it and why
(label contract §35): no event can break the page or the rest of the document.
"""

import json
import math
from dataclasses import dataclass, field

from django.db.models import Max
from rpi_hwid import label_input
from rpi_hwid.probe import nominal_memory

from .models import BootEvent

DOCUMENT_SCHEMA = label_input.SCHEMA
DOCUMENT_VERSION = label_input.VERSION

PI_STAGE = "pi-identified"
PI_SCHEMA = "pi-identity"
FPGA_STAGE = "fpga-board-identified"
FPGA_SCHEMA = "fpga-identity"
SCHEMA_MAJOR = 1

# How far back to look: the newest events of each kind, and the newest boots
# that identified boards. A Pi sends a few of each per boot.
PI_EVENTS_TRIED = 20
FPGA_BOOTS_TRIED = 5
FPGA_EVENTS_PER_BOOT = 50

# What `pi-identified` may set, by rpi-hwid Summary field. The event's values
# are flat strings; these say how each is read back.
PI_FIELDS = {
    "compatible": str, "power_class": str, "hat_uuid": str,
    "fan": bool, "rtc_battery": bool, "max_current_ma": int, "ext5v_v": float,
    # rpi-hwid's own lists: the MACs with their `signal`, and the USB
    # network adapters (§19)
    "header": list, "macs": list, "usb_net": list,
}
# rpi-hwid's FpgaBoard fields the contract names (§1); fpgas-verify's own
# extras (board, variant, bdf, ...) stay in the event and out of the document.
FPGA_FIELDS = {
    "kind": str, "serial": str, "dna": str, "idcode": str, "flash": str,
    "flash_jedec": str, "flash_extended_id": str, "flash_sfdp": str, "flash_uid": str,
    "flash_uid_bits": int, "flash_uid_state": str, "flash_uid_note": str,
    "flash_error": str, "flash_source": str, "soc_model": str,
}
# rpi-hwid's TinyTapeoutBoard fields (§10).
TT_FIELDS = {
    "usb_serial": str, "mcu": str, "shuttle": str, "chip": str, "repo": str, "commit": str,
    "demoboard": str, "demoboard_version": str, "sdk": str,
}
# Which list a board goes to, by its `kind` (§10, §27, §33): a tt board to
# tinytapeout, a fomu to none (no label in v1), and every other kind to fpga,
# as rpi-hwid's fpga_summary keeps them -- pcileech and unknown-fpga too,
# which get no label but are in the document, so the two sides compare equal.
TT_KINDS = ("tt",)
UNLABELLED_KINDS = ("fomu",)


@dataclass
class Built:
    """The document, and what the site itself could see was wrong or old
    (rpi-hwid's `missing` says what each label still needs)."""

    document: dict
    notes: list = field(default_factory=list)


def shown(value, limit=60):
    """`value` for a note: its repr, cut short."""
    text = repr(value)
    return text if len(text) <= limit else text[:limit - 3] + "..."


def schema_major(detail, name):
    """The major version of `detail`'s schema if it is `name`/<n>, else None."""
    value = detail.get("schema") if isinstance(detail, dict) else None
    if not isinstance(value, str):
        return None
    prefix, _, version = value.partition("/")
    if prefix != name:
        return None
    major = version.split(".")[0]
    return int(major) if major.isdigit() else None


def typed(value, kind):
    """One flat-string value as its field's type (label contract §13): "-"
    is a value that was read and is none; a list or object is one key whose
    value is compact JSON. Anything else -- a value that is not a string, or
    does not parse as its type -- raises ValueError."""
    if not isinstance(value, str):
        raise ValueError(f"not a string: {shown(value)}")
    if value == "-":
        return None
    if kind in (list, dict):
        try:
            parsed = json.loads(value)
        except ValueError:
            raise ValueError(f"not JSON: {shown(value)}") from None
        if not isinstance(parsed, kind):
            raise ValueError(f"not a JSON {'array' if kind is list else 'object'}: {shown(value)}")
        return parsed
    if kind is bool:
        if value in ("true", "false"):
            return value == "true"
        raise ValueError(f"not a boolean: {shown(value)}")
    if kind is int:
        try:
            return int(value, 0)
        except ValueError:
            raise ValueError(f"not an integer: {shown(value)}") from None
    if kind is float:
        try:
            number = float(value)
        except ValueError:
            raise ValueError(f"not a number: {shown(value)}") from None
        if not math.isfinite(number):
            raise ValueError(f"not a finite number: {shown(value)}")
        return number
    return value


def pick(detail, fields, drop_none=False):
    """The fields of `detail` that `fields` names, typed; ValueError naming
    the field when one is not its type. A key that is absent was not read, so
    it stays absent. With `drop_none`, a value read as none ("-") is left
    out too, as rpi-hwid leaves out a board's None (fpga_summary for an FPGA
    board, label contract §34; this_host for a Tiny Tapeout board), so
    dumps() fills the same default."""
    out = {}
    for name, kind in fields.items():
        if name in detail:
            try:
                value = typed(detail[name], kind)
            except ValueError as exc:
                raise ValueError(f"{name}: {exc}") from None
            if value is not None or not drop_none:
                out[name] = value
    return out


def refused(summary):
    """Why rpi-hwid's label_input would refuse a document with this
    summary, or [] when it would take it."""
    return label_input.check({"schema": DOCUMENT_SCHEMA, "version": DOCUMENT_VERSION,
                              "host": "-", "summary": summary, "sources": {}})


def registration_summary(doc, notes):
    """The Summary fields the registration document gives, each one only
    if rpi-hwid takes it."""
    machine = doc.get("machine") if isinstance(doc, dict) else None
    machine = machine if isinstance(machine, dict) else {}
    found = {}
    for name, key in (("model", "model"), ("serial", "serial"), ("revision", "revision_code")):
        if machine.get(key):
            found[name] = machine[key]
    kb = machine.get("mem_total_kb")
    if isinstance(kb, int) and not isinstance(kb, bool) and kb > 0:
        found["memory"] = nominal_memory(kb)
    macs = machine.get("macs")
    if isinstance(macs, dict) and macs:
        found["macs"] = [{"kind": "wlan" if str(iface).startswith("wlan") else "eth",
                          "mac": mac, "signal": None}
                         for iface, mac in sorted(macs.items(), key=lambda item: str(item[0]))]
    summary = {}
    for name, value in found.items():
        problems = refused({name: value})
        if problems:
            notes.append(f"registration {name}: {'; '.join(problems)}: ignored")
        else:
            summary[name] = value
    # No HAT from here: the registration's is only what the firmware
    # exposed, not a read of the header. The Pi facts (header, hat_uuid,
    # power class, ...) come from pi-identified alone (label contract §8).
    return summary


def latest(machine, stage):
    return BootEvent.objects.filter(machine=machine, stage=stage).order_by("-id")


def named(event):
    return f"{event.stage} {event.id} at {event.ts:%Y-%m-%dT%H:%M:%SZ}"


def pi_facts(machine, notes):
    """The newest usable `pi-identified` event's Summary fields, or {}. An
    event with any field rpi-hwid would refuse is dropped whole, with a
    note, and the one before it is tried."""
    for event in latest(machine, PI_STAGE)[:PI_EVENTS_TRIED]:
        detail = event.detail
        if schema_major(detail, PI_SCHEMA) != SCHEMA_MAJOR:
            notes.append(f"{named(event)} has schema {shown(_schema(detail))}, "
                         f"not {PI_SCHEMA}/{SCHEMA_MAJOR}: ignored")
            continue
        if detail.get("reader") == "none":
            notes.append(f"{named(event)}: rpi-hwid is not installed on this Pi, so nothing read "
                         "its power class, fan, RTC battery or HAT EEPROM")
            return event, {}
        try:
            facts = pick(detail, PI_FIELDS)
        except ValueError as exc:
            notes.append(f"{named(event)}: {exc}: ignored")
            continue
        problems = refused(facts)
        if problems:
            notes.append(f"{named(event)}: {'; '.join(problems)}: ignored")
            continue
        return event, facts
    notes.append(f"no usable {PI_STAGE} event from this Pi")
    return None, {}


def board_key(detail):
    """What tells two boards on one Pi apart, as text whatever was sent."""
    def text(*keys):
        return next((str(detail[k]) for k in keys if detail.get(k)), "")
    return text("board", "kind"), text("serial", "bdf", "usb", "usb_serial")


def board(event, notes):
    """(list, record) for one fpga-board-identified event: ("fpga", ...),
    ("tinytapeout", ...), or (None, None) for one that gets no place in the
    document, with a note saying why."""
    detail = event.detail
    kind = detail.get("kind")
    where = detail.get("board") if isinstance(detail.get("board"), str) else None
    where = where or (kind if isinstance(kind, str) else None) or "a board"
    if not kind:
        notes.append(f"{named(event)} for {where} has no kind: ignored")
        return None, None
    if not isinstance(kind, str):
        notes.append(f"{named(event)} for {where}: kind: not a string: {shown(kind)}: ignored")
        return None, None
    if kind in UNLABELLED_KINDS:
        notes.append(f"{named(event)}: a {kind} board ({where}) was identified; it gets no label")
        return None, None
    try:
        if kind in TT_KINDS:
            # a field read as none is left out, as rpi-hwid's this_host
            # leaves out a TT board's None (identity_tinytapeout)
            part, record = "tinytapeout", pick(detail, TT_FIELDS, drop_none=True)
            if not record.get("usb_serial"):
                # rpi-hwid drops it on the Pi too (label contract §31)
                notes.append(f"{named(event)} for {where}: "
                             "tinytapeout board without usb_serial: no label")
                return None, None
        else:
            part, record = "fpga", pick(detail, FPGA_FIELDS, drop_none=True)
            if record.get("dna"):
                # who read the DNA, as rpi-hwid records it when it puts
                # fpgas-verify's reading on a board (fpga.merge_dna, §32)
                record["dna_sources"] = ["fpgas-verify"]
    except ValueError as exc:
        notes.append(f"{named(event)} for {where}: {exc}: ignored")
        return None, None
    problems = refused({part: [record]})
    if problems:
        notes.append(f"{named(event)} for {where}: {'; '.join(problems)}: ignored")
        return None, None
    return part, record


def fpga_boards(machine, notes):
    """(fpga, tinytapeout, boot_id) from the newest boot that identified a
    board: every board identified in that boot, the newest usable event of
    each. A board not seen in that boot has gone, so it is not carried over.
    A TT board seen at boot and unplugged since stays here, where rpi-hwid on
    the Pi leaves it out: the site cannot see USB (label contract §28).

    Only the newest few boots are looked at, newest first, and only as far
    as the first with an event of the schema this reads."""
    events = latest(machine, FPGA_STAGE)
    boots = events.values("boot_id").annotate(newest=Max("id")).order_by("-newest") \
        .values_list("boot_id", flat=True)[:FPGA_BOOTS_TRIED]
    for boot_id in boots:
        good = []
        for event in events.filter(boot_id=boot_id)[:FPGA_EVENTS_PER_BOOT]:
            if schema_major(event.detail, FPGA_SCHEMA) == SCHEMA_MAJOR:
                good.append(event)
            else:
                notes.append(f"{named(event)} has schema {shown(_schema(event.detail))}, "
                             f"not {FPGA_SCHEMA}/{SCHEMA_MAJOR}: ignored")
        if not good:
            continue
        if machine.last_boot_id and boot_id != machine.last_boot_id:
            notes.append(f"the FPGA boards were last identified in boot {boot_id}, "
                         f"not in the boot running now ({machine.last_boot_id})")
        placed, found = set(), {"fpga": [], "tinytapeout": []}
        for event in good:
            key = board_key(event.detail)
            if key in placed:
                continue
            part, record = board(event, notes)
            if part is not None:
                placed.add(key)
                found[part].append(record)
        # oldest first, as the boards were found
        return found["fpga"][::-1], found["tinytapeout"][::-1], boot_id
    notes.append(f"no usable {FPGA_STAGE} event from this Pi")
    return [], [], None


def _schema(detail):
    return detail.get("schema") if isinstance(detail, dict) else None


def host(machine):
    """The name rpi-hwid gives this Pi's document (its file stem)."""
    return machine.hostname or machine.serial


def build(machine):
    """rpi-hwid's label-input document for `machine`, and the site's notes.
    The document is always one rpi-hwid's label_input takes."""
    notes = []
    snapshot = machine.latest_snapshot
    if snapshot is None:
        notes.append("no registration from this Pi")
        registered = {}
    else:
        registered = registration_summary(snapshot.document, notes)
    summary = dict(registered)
    pi_event, facts = pi_facts(machine, notes)
    summary.update(facts)
    # A field nobody sent is left out, and rpi-hwid's dumps() writes the
    # default the Pi's probe would. Not the header: its default, [], says
    # "read, no HAT", so a header nothing read is an explicit null, which
    # missing() lists and the Pi label refuses (label contract §17).
    summary.setdefault("header", None)
    fpga, tinytapeout, fpga_boot = fpga_boards(machine, notes)
    if fpga:
        summary["fpga"] = fpga
    if tinytapeout:
        summary["tinytapeout"] = tinytapeout
    sources = {"collected_by": "fpgas.online-site"}
    if snapshot is not None:
        sources["registration"] = snapshot.fingerprint
    if pi_event is not None:
        sources[PI_STAGE] = f"{pi_event.boot_id} {pi_event.ts:%Y-%m-%dT%H:%M:%SZ}"
    if fpga_boot is not None:
        sources[FPGA_STAGE] = str(fpga_boot)
    problems = refused(summary)
    if problems:
        # Each part was checked on its own; this is the last guard, for a
        # combination of them rpi-hwid refuses. The registration alone was
        # checked field by field.
        notes.append(f"rpi-hwid refuses the document built from the events "
                     f"({'; '.join(problems)}): only the registration is used")
        summary = dict(registered, header=None)
    document = {"schema": DOCUMENT_SCHEMA, "version": DOCUMENT_VERSION,
                "host": host(machine), "summary": summary, "sources": sources}
    return Built(document=document, notes=notes)


def dumps(document):
    """The document as rpi-hwid writes it, and only as rpi-hwid writes it."""
    return label_input.dumps(document)


def missing(document):
    """{label: [fields]} that rpi-hwid says each label still needs."""
    return label_input.missing(document)
