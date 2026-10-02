"""rpi-hwid's label-input document for one Pi, built from what the Pi sends.

The Pi's registration gives its serial, model, revision, memory, MACs and the
HAT the firmware saw. fpgas-verify adds a `pi-identified` event (the Pi facts
rpi-hwid reads: power class, fan, RTC battery, HAT, ...) and one
`fpga-board-identified` event per FPGA board. Both carry flat-string details
named as rpi-hwid's Summary / FpgaBoard / TinyTapeoutBoard fields, with a
`schema` of `pi-identity/1` or `fpga-identity/1` (the label contract, v1).

`build` only gathers: it never guesses a value that was not sent. What a full
label still needs is rpi-hwid's to say (`missing`), and the document is
written only by rpi-hwid's own serialiser (`dumps`), so the site's file and
the one rpi-hwid makes on the Pi can be compared byte for byte.
"""

from dataclasses import dataclass, field

from rpi_hwid.probe import nominal_memory

from .models import BootEvent

DOCUMENT_SCHEMA = "rpi-hwid/label-input"
DOCUMENT_VERSION = 1

PI_STAGE = "pi-identified"
PI_SCHEMA = "pi-identity"
FPGA_STAGE = "fpga-board-identified"
FPGA_SCHEMA = "fpga-identity"
SCHEMA_MAJOR = 1

# What `pi-identified` may set, by rpi-hwid Summary field. The event's values
# are flat strings; these say how each is read back.
PI_FIELDS = {
    "compatible": str, "power_class": str, "hat_uuid": str,
    "fan": bool, "rtc_battery": bool, "max_current_ma": int, "ext5v_v": float,
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
TT_BOARDS = ("tt",)
# Recorded, but no label in v1 (§10).
UNLABELLED_BOARDS = ("fomu",)


@dataclass
class Built:
    """The document, and what the site itself could see was wrong or old
    (rpi-hwid's `missing` says what each label still needs)."""

    document: dict
    notes: list = field(default_factory=list)


def schema_major(detail, name):
    """The major version of `detail`'s schema if it is `name`/<n>, else None."""
    value = detail.get("schema", "") if isinstance(detail, dict) else ""
    prefix, _, version = str(value).partition("/")
    if prefix != name:
        return None
    major = version.split(".")[0]
    return int(major) if major.isdigit() else None


def typed(value, kind):
    """One flat-string value as its field's type. "-" is a value that was
    read and is none (fpgas-verify's flatten() writes None so); a value that
    does not parse raises ValueError."""
    if value is None or value == "-":
        return None
    if kind is bool:
        if value in ("true", "false"):
            return value == "true"
        raise ValueError(f"not a boolean: {value!r}")
    if kind is int:
        return int(value, 0)
    if kind is float:
        return float(value)
    return str(value)


def pick(detail, fields):
    """The fields of `detail` that `fields` names, typed. A key that is
    absent was not read, so it stays absent."""
    out = {}
    for name, kind in fields.items():
        if name in detail:
            out[name] = typed(detail[name], kind)
    return out


def indexed(detail, name):
    """A list sent as name0, name1, ... (fpgas-verify's flatten()), or None
    when no item was sent."""
    items = []
    while f"{name}{len(items)}" in detail:
        items.append(str(detail[f"{name}{len(items)}"]))
    return items or None


def registration_summary(doc):
    """The Summary fields the registration document gives."""
    machine = doc.get("machine", {})
    summary = {}
    for name, key in (("model", "model"), ("serial", "serial"), ("revision", "revision_code")):
        if machine.get(key):
            summary[name] = machine[key]
    memory = nominal_memory(machine.get("mem_total_kb"))
    if memory:
        summary["memory"] = memory
    macs = []
    for iface, mac in sorted(machine.get("macs", {}).items()):
        kind = "wlan" if iface.startswith("wlan") else "eth"
        macs.append({"kind": kind, "mac": mac, "signal": None})
    if macs:
        summary["macs"] = macs
    hats = doc.get("peripherals", {}).get("hats", [])
    if hats:
        # what the firmware exposes in /proc/device-tree/hat, named as the
        # probe names a HAT it knows only that way
        hat = hats[0]
        summary["header"] = [f"{hat.get('vendor', '')} {hat.get('product', '')}".strip()]
        if hat.get("uuid"):
            summary["hat_uuid"] = hat["uuid"]
    return summary


def latest(machine, stage):
    return BootEvent.objects.filter(machine=machine, stage=stage).order_by("-id")


def pi_facts(machine, notes):
    """The newest usable `pi-identified` event's Summary fields, or {}."""
    for event in latest(machine, PI_STAGE):
        if schema_major(event.detail, PI_SCHEMA) != SCHEMA_MAJOR:
            notes.append(f"{PI_STAGE} at {event.ts:%Y-%m-%dT%H:%M:%SZ} has schema "
                         f"{_schema(event.detail)!r}, not {PI_SCHEMA}/{SCHEMA_MAJOR}: ignored")
            continue
        if event.detail.get("reader") == "none":
            notes.append(f"{PI_STAGE}: rpi-hwid is not installed on this Pi, so nothing read "
                         "its power class, fan, RTC battery or HAT EEPROM")
            return event, {}
        try:
            facts = pick(event.detail, PI_FIELDS)
        except ValueError as exc:
            notes.append(f"{PI_STAGE} at {event.ts:%Y-%m-%dT%H:%M:%SZ}: {exc}: ignored")
            continue
        header = indexed(event.detail, "header")
        if header is not None:
            facts["header"] = header
        return event, facts
    notes.append(f"no {PI_STAGE} event from this Pi")
    return None, {}


def board_key(detail):
    """What tells two boards on one Pi apart."""
    return (detail.get("board") or detail.get("kind") or "",
            detail.get("serial") or detail.get("bdf") or detail.get("usb") or detail.get("usb_serial") or "")


def fpga_boards(machine, notes):
    """(fpga, tinytapeout, boot_id) from the newest boot that identified a
    board: every board identified in that boot, the newest event of each.
    A board not seen in that boot has gone, so it is not carried over."""
    events = list(latest(machine, FPGA_STAGE))
    good = []
    for event in events:
        if schema_major(event.detail, FPGA_SCHEMA) == SCHEMA_MAJOR:
            good.append(event)
        else:
            notes.append(f"{FPGA_STAGE} at {event.ts:%Y-%m-%dT%H:%M:%SZ} has schema "
                         f"{_schema(event.detail)!r}, not {FPGA_SCHEMA}/{SCHEMA_MAJOR}: ignored")
    if not good:
        notes.append(f"no {FPGA_STAGE} event from this Pi")
        return [], [], None
    boot_id = good[0].boot_id
    if machine.last_boot_id and boot_id != machine.last_boot_id:
        notes.append(f"the FPGA boards were last identified in boot {boot_id}, "
                     f"not in the boot running now ({machine.last_boot_id})")
    seen, fpga, tinytapeout = set(), [], []
    for event in good:
        if event.boot_id != boot_id or board_key(event.detail) in seen:
            continue
        seen.add(board_key(event.detail))
        board = event.detail.get("board") or event.detail.get("kind") or ""
        try:
            if board in TT_BOARDS:
                tinytapeout.append(pick(event.detail, TT_FIELDS))
            elif board in UNLABELLED_BOARDS:
                notes.append(f"a {board} board was identified; it gets no label")
            else:
                fpga.append(pick(event.detail, FPGA_FIELDS))
        except ValueError as exc:
            notes.append(f"{FPGA_STAGE} for {board or 'a board'}: {exc}: ignored")
    # oldest first, as the boards were found
    return fpga[::-1], tinytapeout[::-1], boot_id


def _schema(detail):
    return detail.get("schema") if isinstance(detail, dict) else None


def host(machine):
    """The name rpi-hwid gives this Pi's document (its file stem)."""
    return machine.hostname or machine.serial


def build(machine):
    """rpi-hwid's label-input document for `machine`, and the site's notes."""
    notes = []
    snapshot = machine.latest_snapshot
    if snapshot is None:
        notes.append("no registration from this Pi")
        summary = {}
    else:
        summary = registration_summary(snapshot.document)
    pi_event, facts = pi_facts(machine, notes)
    summary.update(facts)
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
        sources[FPGA_STAGE] = fpga_boot
    document = {"schema": DOCUMENT_SCHEMA, "version": DOCUMENT_VERSION,
                "host": host(machine), "summary": summary, "sources": sources}
    return Built(document=document, notes=notes)


def dumps(document):
    """The document as rpi-hwid writes it, and only as rpi-hwid writes it."""
    from rpi_hwid import label_input
    return label_input.dumps(document)


def missing(document):
    """{label: [fields]} that rpi-hwid says each label still needs."""
    from rpi_hwid import label_input
    return label_input.missing(document)
