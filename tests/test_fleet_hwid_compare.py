"""The site's label input for a Pi against the one rpi-hwid makes on that Pi,
compared as the label contract says (§15, §25): label_input.comparable().

The Pi side is pi-sw2-p48 as rpi-hwid's own tests have it:
- data/pi-sw2-p48-pi-facts.json is the Pi part of rpi-hwid's ACORN_HOST
  fixture (tests/conftest.py, rpi-hwid origin/main 0393d6c);
- data/identity-v1-acorn-p48.json is the contract's golden fpgas-verify
  identity document (§11), byte for byte rpi-hwid's copy.
Its FPGA list is built by rpi-hwid's own code, as `rpi-hwid labels
--this-host` does: fpgas-verify's reading put on the board found on PCIe.

The site side is what that Pi sends: a registration, and the pi-identified
and fpga-board-identified events, written as fpgas-verify writes details
(flat strings, label contract §2 and §13).
"""

import copy
import json
import pathlib

import pytest
from django.utils import timezone
from fleet.models import BootEvent, Machine
from fleet.services import register_document, status
from rpi_hwid import fpga, label_input
from rpi_hwid.probe import nominal_memory

from fleet import hwid

DATA = pathlib.Path(__file__).parent / "data"
PI_FACTS = json.loads((DATA / "pi-sw2-p48-pi-facts.json").read_text())
IDENTITY_TEXT = (DATA / "identity-v1-acorn-p48.json").read_text()
HOST = "pi-sw2-p48"
# The fixture has no MemTotal; a Pi 5 2 GB's, so both sides say "2 GB".
MEM_TOTAL_KB = 2003128

# What pi-identified carries: every Summary field rpi-hwid reads on the Pi.
PI_IDENTIFIED_FIELDS = tuple(hwid.PI_FIELDS)


def flat(value):
    """One value as a fleet-event detail string (label contract §2, §13)."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    return str(value)


# A second board, of a kind that gets no label but is in the document
# (label contract §33), with a field read as none: null in fpgas-verify's
# identity document, "-" in its event (§34).
PCILEECH = {"board": "pcileech", "kind": "pcileech", "dna": "0x00112233445566ff",
            "idcode": "0x13631093", "serial": None, "flash_uid": None}


def identity(*extra):
    doc = json.loads(IDENTITY_TEXT)
    doc["boards"] += extra
    return doc


def pi_side(*extra):
    """The label input `rpi-hwid labels --this-host` builds on p48, with
    fpgas-verify having identified the `extra` boards as well."""
    summary = copy.deepcopy(PI_FACTS)
    summary["memory"] = nominal_memory(MEM_TOTAL_KB)
    read, why = fpga.identity_parse(json.dumps(identity(*extra)))
    assert why is None
    # what sysfs shows: the Acorn on PCIe
    boards = [{"kind": "acorn", "slot": read[0]["bdf"]}]
    assert fpga.merge_identity(boards, read) == []
    summary["fpga"] = fpga.fpga_summary(boards)
    return label_input.build(HOST, summary, dict.fromkeys(summary, "rpi-hwid"))


@pytest.fixture
def machine():
    register_document({
        "schema": 1,
        "machine": {"serial": PI_FACTS["serial"], "model": PI_FACTS["model"],
                    "revision_code": PI_FACTS["revision"], "mem_total_kb": MEM_TOTAL_KB,
                    "macs": {"eth0": PI_FACTS["macs"][0]["mac"]}},
        "connection": {"site": "welland", "hostname": HOST},
        "peripherals": {"usb": [], "pcie": [], "hats": [], "cameras": []},
    })
    status(PI_FACTS["serial"], {"online": True, "boot_id": "b1", "uptime_s": 60})
    return Machine.objects.get()


def send(machine, stage, detail):
    BootEvent.objects.create(machine=machine, boot_id="b1", stage=stage, detail=detail,
                             ts=timezone.now())


def pi_identified():
    detail = {"schema": "pi-identity/1", "reader": "rpi-hwid"}
    detail.update((k, flat(PI_FACTS[k])) for k in PI_IDENTIFIED_FIELDS if k in PI_FACTS)
    return detail


def fpga_board_identified(board=None):
    """One board's event, as fpgas-verify sends it: the p48 Acorn's by default."""
    if board is None:
        (board,) = json.loads(IDENTITY_TEXT)["boards"]
    detail = {"schema": "fpga-identity/1"}
    detail.update((k, flat(v)) for k, v in board.items())
    return detail


@pytest.mark.django_db
def test_a_board_without_a_label_and_a_field_read_as_none_still_agree(machine):
    send(machine, "pi-identified", pi_identified())
    send(machine, "fpga-board-identified", fpga_board_identified())
    send(machine, "fpga-board-identified", fpga_board_identified(PCILEECH))
    built = hwid.build(machine)
    assert built.notes == []
    assert [b["kind"] for b in built.document["summary"]["fpga"]] == ["acorn", "pcileech"]
    assert label_input.comparable(built.document) == label_input.comparable(pi_side(PCILEECH))


@pytest.mark.django_db
def test_the_site_and_the_pi_agree_on_p48(machine):
    send(machine, "pi-identified", pi_identified())
    send(machine, "fpga-board-identified", fpga_board_identified())
    built = hwid.build(machine)
    assert built.notes == []
    assert label_input.comparable(built.document) == label_input.comparable(pi_side())
    assert not any(label_input.missing(built.document).values())


@pytest.mark.django_db
def test_the_comparison_leaves_out_only_what_is_measured_or_provenance(machine):
    # a different voltage, current and MAC signal, and the site's own
    # sources: still the same document
    facts = pi_identified() | {"ext5v_v": "5.11", "max_current_ma": "5000",
                               "macs": flat([{"kind": "eth", "mac": PI_FACTS["macs"][0]["mac"],
                                              "signal": "carrier"}])}
    send(machine, "pi-identified", facts)
    send(machine, "fpga-board-identified", fpga_board_identified())
    site = hwid.build(machine).document
    assert label_input.dumps(site) != label_input.dumps(pi_side())
    assert label_input.comparable(site) == label_input.comparable(pi_side())


@pytest.mark.django_db
def test_a_different_fact_is_a_difference(machine):
    send(machine, "pi-identified", pi_identified() | {"power_class": "usbc-supply"})
    send(machine, "fpga-board-identified", fpga_board_identified())
    assert label_input.comparable(hwid.build(machine).document) != \
        label_input.comparable(pi_side())


@pytest.mark.django_db
def test_without_the_events_the_site_cannot_match(machine):
    built = hwid.build(machine)
    assert label_input.comparable(built.document) != label_input.comparable(pi_side())
    assert any(label_input.missing(built.document).values())
