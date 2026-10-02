import importlib.util

import pytest
from django.utils import timezone
from fleet.models import BootEvent, Machine
from fleet.services import register_document, status

from fleet import hwid

HAVE_LABEL_INPUT = importlib.util.find_spec("rpi_hwid.label_input") is not None

SERIAL = "c36b093f773d46b8"
REGISTRATION = {
    "schema": 1,
    "machine": {"serial": SERIAL, "model": "Raspberry Pi 5 Model B Rev 1.0",
                "revision_code": "c04170", "mem_total_kb": 4045440,
                "macs": {"wlan0": "2c:cf:67:00:00:02", "eth0": "2c:cf:67:00:00:01"}},
    "connection": {"site": "welland", "hostname": "pi-sw2-p48"},
    "peripherals": {"usb": [], "pcie": [], "cameras": [],
                    "hats": [{"product": "PoE+ HAT", "vendor": "Raspberry Pi",
                              "product_id": "0x0001", "product_ver": "0x0001",
                              "uuid": "0d9c4a0a-0000-0000-0000-000000000000"}]},
}
PI_IDENTIFIED = {
    "schema": "pi-identity/1", "reader": "rpi-hwid",
    "power_class": "gpio-poe-hat", "compatible": "raspberrypi,5-model-b brcm,bcm2712",
    "hat_uuid": "0d9c4a0a-0000-0000-0000-000000000000",
    "header0": "PoE+ HAT", "fan": "true", "rtc_battery": "false",
    "max_current_ma": "3000", "ext5v_v": "5.08",
}
# the p48 values of the contract's golden fixture (identity-v1-acorn-p48.json)
ACORN = {
    "schema": "fpga-identity/1", "board": "acorn", "kind": "acorn", "variant": "cle-215+",
    "bdf": "0000:01:00.0", "dna": "0x0054b48664b04854", "idcode": "0x13636093",
    "flash_jedec": "0x010219", "flash_extended_id": "0x4d0180", "flash": "S25FL256S",
    "flash_uid": "edcbeececb2b2a88b04f914d2e46af90", "flash_uid_bits": "128",
    "flash_uid_state": "read", "flash_size_bytes": "33554432",
}


@pytest.fixture
def machine():
    register_document(REGISTRATION)
    status(SERIAL, {"online": True, "boot_id": "b2", "uptime_s": 60})
    return Machine.objects.get()


def event(machine, stage, detail, boot_id="b2"):
    return BootEvent.objects.create(machine=machine, boot_id=boot_id, stage=stage,
                                    detail=detail, ts=timezone.now())


@pytest.mark.django_db
def test_registration_alone_gives_the_pi_basics_and_says_what_did_not_come(machine):
    built = hwid.build(machine)
    doc = built.document
    assert doc["schema"] == "rpi-hwid/label-input" and doc["version"] == 1
    assert doc["host"] == "pi-sw2-p48"
    s = doc["summary"]
    assert (s["model"], s["serial"], s["revision"]) == (
        "Raspberry Pi 5 Model B Rev 1.0", SERIAL, "c04170")
    assert s["memory"] == "4 GB"
    assert s["macs"] == [{"kind": "eth", "mac": "2c:cf:67:00:00:01", "signal": None},
                         {"kind": "wlan", "mac": "2c:cf:67:00:00:02", "signal": None}]
    assert s["header"] == ["Raspberry Pi PoE+ HAT"]
    assert s["hat_uuid"] == "0d9c4a0a-0000-0000-0000-000000000000"
    # never guessed: rpi-hwid's missing() names it
    assert "power_class" not in s and "fpga" not in s
    assert "no pi-identified event from this Pi" in built.notes
    assert "no fpga-board-identified event from this Pi" in built.notes


@pytest.mark.django_db
def test_pi_identified_fills_the_pi_facts_typed(machine):
    event(machine, "pi-identified", PI_IDENTIFIED)
    s = hwid.build(machine).document["summary"]
    assert s["power_class"] == "gpio-poe-hat"
    assert s["compatible"] == "raspberrypi,5-model-b brcm,bcm2712"
    assert s["header"] == ["PoE+ HAT"]           # rpi-hwid's reading wins
    assert s["fan"] is True and s["rtc_battery"] is False
    assert s["max_current_ma"] == 3000 and s["ext5v_v"] == 5.08


@pytest.mark.django_db
def test_a_value_read_as_none_is_kept_as_none(machine):
    event(machine, "pi-identified", {**PI_IDENTIFIED, "fan": "-", "rtc_battery": "-"})
    s = hwid.build(machine).document["summary"]
    assert s["fan"] is None and s["rtc_battery"] is None


@pytest.mark.django_db
def test_pi_without_rpi_hwid_says_so(machine):
    event(machine, "pi-identified", {"schema": "pi-identity/1", "reader": "none"})
    built = hwid.build(machine)
    assert "power_class" not in built.document["summary"]
    assert any("rpi-hwid is not installed" in n for n in built.notes)


@pytest.mark.django_db
def test_unknown_schema_major_is_refused(machine):
    event(machine, "pi-identified", {**PI_IDENTIFIED, "schema": "pi-identity/2"})
    event(machine, "fpga-board-identified", {**ACORN, "schema": "fpga-identity/2"})
    event(machine, "fpga-board-identified", ["not", "a", "dict"])
    built = hwid.build(machine)
    assert "power_class" not in built.document["summary"]
    assert "fpga" not in built.document["summary"]
    assert sum("ignored" in n for n in built.notes) == 3


@pytest.mark.django_db
def test_a_value_that_does_not_parse_drops_that_event_only(machine):
    event(machine, "pi-identified", PI_IDENTIFIED)
    event(machine, "pi-identified", {**PI_IDENTIFIED, "fan": "maybe"})
    built = hwid.build(machine)
    assert built.document["summary"]["fan"] is True
    assert any("not a boolean" in n for n in built.notes)


@pytest.mark.django_db
def test_acorn_is_copied_with_only_rpi_hwid_fields(machine):
    event(machine, "fpga-board-identified", ACORN)
    built = hwid.build(machine)
    (board,) = built.document["summary"]["fpga"]
    assert board == {
        "kind": "acorn", "dna": "0x0054b48664b04854", "idcode": "0x13636093",
        "flash_jedec": "0x010219", "flash_extended_id": "0x4d0180", "flash": "S25FL256S",
        "flash_uid": "edcbeececb2b2a88b04f914d2e46af90", "flash_uid_bits": 128,
        "flash_uid_state": "read"}
    assert built.document["sources"]["fpga-board-identified"] == "b2"
    assert not built.notes[1:]  # only the missing pi-identified


@pytest.mark.django_db
def test_only_the_newest_boot_that_identified_boards_counts(machine):
    event(machine, "fpga-board-identified", {**ACORN, "dna": "0x0000000000000001"}, boot_id="b1")
    event(machine, "fpga-board-identified", {**ACORN, "dna": "0x0000000000000002"}, boot_id="b2")
    event(machine, "fpga-board-identified", {**ACORN, "dna": "0x0000000000000003"}, boot_id="b2")
    (board,) = hwid.build(machine).document["summary"]["fpga"]
    assert board["dna"] == "0x0000000000000003"


@pytest.mark.django_db
def test_boards_from_an_earlier_boot_are_flagged(machine):
    event(machine, "fpga-board-identified", ACORN, boot_id="b1")
    built = hwid.build(machine)
    assert built.document["summary"]["fpga"][0]["kind"] == "acorn"
    assert any("not in the boot running now (b2)" in n for n in built.notes)


@pytest.mark.django_db
def test_tt_goes_to_tinytapeout_and_fomu_gets_no_label(machine):
    event(machine, "fpga-board-identified", {
        "schema": "fpga-identity/1", "board": "tt", "usb_serial": "e6614c311b6b8a2e",
        "mcu": "RP2040", "chip": "asic", "shuttle": "tt06", "demoboard": "TT06+",
        "demoboard_version": "v2.0.1", "sdk": "2.0.1"})
    event(machine, "fpga-board-identified", {
        "schema": "fpga-identity/1", "board": "fomu", "flash_jedec": "0xc84015"})
    built = hwid.build(machine)
    s = built.document["summary"]
    assert "fpga" not in s
    assert s["tinytapeout"] == [{"usb_serial": "e6614c311b6b8a2e", "mcu": "RP2040",
                                 "shuttle": "tt06", "chip": "asic", "demoboard": "TT06+",
                                 "demoboard_version": "v2.0.1", "sdk": "2.0.1"}]
    assert any("fomu board was identified; it gets no label" in n for n in built.notes)


@pytest.mark.django_db
def test_no_registration():
    m = Machine.objects.create(serial="x1", site="welland", last_seen=timezone.now())
    built = hwid.build(m)
    assert built.document["host"] == "x1" and built.document["summary"] == {}
    assert "no registration from this Pi" in built.notes


# Against rpi-hwid's own label_input: these run once the release that has it
# is installed (pyproject.toml pins it before this merges).
needs_label_input = pytest.mark.skipif(not HAVE_LABEL_INPUT,
                                       reason="rpi-hwid release with label_input not yet on PyPI")


@needs_label_input
@pytest.mark.django_db
def test_rpi_hwid_reads_back_what_the_site_writes(machine):
    from rpi_hwid import label_input
    event(machine, "pi-identified", PI_IDENTIFIED)
    event(machine, "fpga-board-identified", ACORN)
    text = hwid.dumps(hwid.build(machine).document)
    assert hwid.dumps(label_input.load(text)) == text


@needs_label_input
@pytest.mark.django_db
def test_rpi_hwid_says_registration_alone_is_not_enough(machine):
    missing = hwid.missing(hwid.build(machine).document)
    assert any("power_class" in fields for fields in missing.values())


@needs_label_input
@pytest.mark.django_db
def test_rpi_hwid_needs_nothing_more_for_a_full_acorn_pi(machine):
    event(machine, "pi-identified", PI_IDENTIFIED)
    event(machine, "fpga-board-identified", ACORN)
    assert not any(hwid.missing(hwid.build(machine).document).values())


def test_schema_major():
    assert hwid.schema_major({"schema": "fpga-identity/1"}, "fpga-identity") == 1
    assert hwid.schema_major({"schema": "fpga-identity/1.2"}, "fpga-identity") == 1
    assert hwid.schema_major({"schema": "pi-identity/1"}, "fpga-identity") is None
    assert hwid.schema_major({}, "fpga-identity") is None
    assert hwid.schema_major("x", "fpga-identity") is None
