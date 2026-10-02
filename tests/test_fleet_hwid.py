import json

import pytest
from django.utils import timezone
from fleet.models import BootEvent, Machine
from fleet.services import register_document, status

from fleet import hwid

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
USB_NET = {"iface": "eth1", "mac": "00:e0:4c:68:00:01", "vidpid": "0bda:8153", "kind": "ethernet",
           "driver": "r8152", "manufacturer": "Realtek", "product": "USB 10/100/1000 LAN",
           "usb_serial": "000001", "bcd_usb": "3.00", "usb_speed": "5000", "signal": "usb"}
PI_IDENTIFIED = {
    "schema": "pi-identity/1", "reader": "rpi-hwid",
    "power_class": "gpio-poe-hat", "compatible": "raspberrypi,5-model-b brcm,bcm2712",
    "hat_uuid": "0d9c4a0a-0000-0000-0000-000000000000",
    # lists and objects: one key, compact JSON (label contract §13)
    "header": json.dumps(["PoE+ HAT"], separators=(",", ":"), sort_keys=True),
    "macs": json.dumps([{"kind": "eth", "mac": "2c:cf:67:00:00:01", "signal": "onboard"},
                        {"kind": "wlan", "mac": "2c:cf:67:00:00:02", "signal": "onboard"}],
                       separators=(",", ":"), sort_keys=True),
    "usb_net": json.dumps([USB_NET], separators=(",", ":"), sort_keys=True),
    "fan": "true", "rtc_battery": "false", "max_current_ma": "3000", "ext5v_v": "5.08",
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
    # the firmware's HAT is not a read of the header: null, never [] ("no HAT")
    assert s["header"] is None and "hat_uuid" not in s
    # never guessed, and left out so rpi-hwid's dumps() writes its default
    assert "power_class" not in s and "fpga" not in s and "fan" not in s
    assert "no usable pi-identified event from this Pi" in built.notes
    assert "no usable fpga-board-identified event from this Pi" in built.notes


@pytest.mark.django_db
def test_pi_identified_fills_the_pi_facts_typed(machine):
    event(machine, "pi-identified", PI_IDENTIFIED)
    s = hwid.build(machine).document["summary"]
    assert s["power_class"] == "gpio-poe-hat"
    assert s["compatible"] == "raspberrypi,5-model-b brcm,bcm2712"
    assert s["header"] == ["PoE+ HAT"]
    assert s["hat_uuid"] == "0d9c4a0a-0000-0000-0000-000000000000"
    # rpi-hwid's MACs win over the registration's
    assert [m["signal"] for m in s["macs"]] == ["onboard", "onboard"]
    assert s["usb_net"] == [USB_NET]
    assert s["fan"] is True and s["rtc_battery"] is False
    assert s["max_current_ma"] == 3000 and s["ext5v_v"] == 5.08


@pytest.mark.django_db
@pytest.mark.parametrize(("value", "why"), [
    ("PoE+ HAT", "not JSON"), ('{"a":1}', "not a JSON array"), ("header0", "not JSON")])
def test_a_list_that_is_not_a_json_array_drops_that_event(machine, value, why):
    event(machine, "pi-identified", {**PI_IDENTIFIED, "header": value})
    built = hwid.build(machine)
    assert "power_class" not in built.document["summary"]
    assert any(why in n and "ignored" in n for n in built.notes)


@pytest.mark.django_db
def test_indexed_keys_are_not_read(machine):
    detail = {k: v for k, v in PI_IDENTIFIED.items() if k != "header"}
    event(machine, "pi-identified", {**detail, "header0": "Something else"})
    # header0 is not a field, so the header was not read
    assert hwid.build(machine).document["summary"]["header"] is None


@pytest.mark.django_db
def test_header_read_with_no_hat_is_kept_as_an_empty_list(machine):
    event(machine, "pi-identified", {**PI_IDENTIFIED, "header": "[]"})
    assert hwid.build(machine).document["summary"]["header"] == []


@pytest.mark.django_db
def test_pi_facts_not_read_are_left_out(machine):
    detail = {k: v for k, v in PI_IDENTIFIED.items() if k not in ("fan", "hat_uuid", "header")}
    event(machine, "pi-identified", detail)
    s = hwid.build(machine).document["summary"]
    assert "fan" not in s and "hat_uuid" not in s
    assert s["header"] is None


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
        "flash_uid_state": "read", "dna_sources": ["fpgas-verify"]}
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
def test_a_boot_with_no_usable_board_event_falls_back_to_the_one_before(machine):
    event(machine, "fpga-board-identified", ACORN, boot_id="b1")
    event(machine, "fpga-board-identified", {**ACORN, "schema": "fpga-identity/9"}, boot_id="b2")
    built = hwid.build(machine)
    assert built.document["summary"]["fpga"][0]["dna"] == ACORN["dna"]
    assert built.document["sources"]["fpga-board-identified"] == "b1"


@pytest.mark.django_db
def test_only_the_newest_boots_are_looked_at(machine):
    event(machine, "fpga-board-identified", ACORN, boot_id="b0")
    for i in range(hwid.FPGA_BOOTS_TRIED):
        event(machine, "fpga-board-identified", {**ACORN, "schema": "x"}, boot_id=f"old{i}")
    built = hwid.build(machine)
    assert "fpga" not in built.document["summary"]
    assert "no usable fpga-board-identified event from this Pi" in built.notes


@pytest.mark.django_db
def test_tt_goes_to_tinytapeout_and_fomu_gets_no_label(machine):
    event(machine, "fpga-board-identified", {
        "schema": "fpga-identity/1", "board": "tt", "kind": "tt", "usb_serial": "e6614c311b6b8a2e",
        "mcu": "RP2040", "chip": "asic", "shuttle": "tt06", "demoboard": "TT06+",
        "demoboard_version": "v2.0.1", "sdk": "2.0.1"})
    event(machine, "fpga-board-identified", {
        "schema": "fpga-identity/1", "board": "fomu", "kind": "fomu", "flash_jedec": "0xc84015"})
    built = hwid.build(machine)
    s = built.document["summary"]
    assert "fpga" not in s
    assert s["tinytapeout"] == [{"usb_serial": "e6614c311b6b8a2e", "mcu": "RP2040",
                                 "shuttle": "tt06", "chip": "asic", "demoboard": "TT06+",
                                 "demoboard_version": "v2.0.1", "sdk": "2.0.1"}]
    assert any("fomu board (fomu) was identified; it gets no label" in n for n in built.notes)


@pytest.mark.django_db
def test_second_boards_of_a_kind_go_by_kind_not_by_their_state_key(machine):
    # fpgas-verify names a second board of a kind "tt@1-1.2" (runner.py _keys)
    for board, serial in (("tt", "e6614c311b6b8a2e"), ("tt@1-1.2", "e6614c311b6b8a2f")):
        event(machine, "fpga-board-identified", {
            "schema": "fpga-identity/1", "board": board, "kind": "tt", "usb_serial": serial,
            "mcu": "RP2350", "usb": board})
    for board, usb in (("fomu", "1-1.3"), ("fomu@1-1.4", "1-1.4")):
        event(machine, "fpga-board-identified", {
            "schema": "fpga-identity/1", "board": board, "kind": "fomu", "usb": usb})
    built = hwid.build(machine)
    s = built.document["summary"]
    assert "fpga" not in s
    assert s["tinytapeout"] == [{"usb_serial": "e6614c311b6b8a2e", "mcu": "RP2350"},
                                {"usb_serial": "e6614c311b6b8a2f", "mcu": "RP2350"}]
    assert sum("gets no label" in n for n in built.notes) == 2


@pytest.mark.django_db
@pytest.mark.parametrize("usb_serial", [None, "-", ""])
def test_a_tt_board_without_usb_serial_is_dropped_with_a_note(machine, usb_serial):
    detail = {"schema": "fpga-identity/1", "board": "tt", "kind": "tt", "mcu": "RP2040"}
    if usb_serial is not None:
        detail["usb_serial"] = usb_serial
    event(machine, "fpga-board-identified", detail)
    built = hwid.build(machine)
    assert "tinytapeout" not in built.document["summary"]
    assert "tinytapeout board without usb_serial: no label" in built.notes


@pytest.mark.django_db
def test_every_kind_but_tt_and_fomu_stays_in_fpga(machine):
    # as rpi-hwid's fpga_summary keeps them (label contract §33)
    for kind, bdf in (("pcileech", "0000:01:00.0"), ("unknown-fpga", "0000:02:00.0")):
        event(machine, "fpga-board-identified", {
            "schema": "fpga-identity/1", "board": kind, "kind": kind, "bdf": bdf,
            "idcode": "0x13631093"})
    built = hwid.build(machine)
    assert [b["kind"] for b in built.document["summary"]["fpga"]] == ["pcileech", "unknown-fpga"]
    assert built.notes == ["no usable pi-identified event from this Pi"]


@pytest.mark.django_db
def test_a_board_field_read_as_none_is_left_out_but_a_pi_fact_stays_null(machine):
    # label contract §34: dumps() then fills the board's default, as
    # rpi-hwid's fpga_summary leaves its None out; §17: a Pi fact keeps null
    event(machine, "pi-identified", {**PI_IDENTIFIED, "fan": "-"})
    event(machine, "fpga-board-identified", {**ACORN, "serial": "-", "dna": "-", "flash_sfdp": "-"})
    s = hwid.build(machine).document["summary"]
    (board,) = s["fpga"]
    assert "serial" not in board and "dna" not in board and "flash_sfdp" not in board
    assert "dna_sources" not in board
    assert s["fan"] is None


@pytest.mark.django_db
def test_a_board_without_a_kind_is_noted_and_left_out(machine):
    event(machine, "fpga-board-identified", {k: v for k, v in ACORN.items() if k != "kind"})
    built = hwid.build(machine)
    assert "fpga" not in built.document["summary"]
    assert any("acorn has no kind: ignored" in n for n in built.notes)


@pytest.mark.django_db
def test_no_registration():
    m = Machine.objects.create(serial="x1", site="welland", last_seen=timezone.now())
    built = hwid.build(m)
    assert built.document["host"] == "x1" and built.document["summary"] == {"header": None}
    assert "no registration from this Pi" in built.notes


# Against rpi-hwid's own label_input.


@pytest.mark.django_db
def test_rpi_hwid_reads_back_what_the_site_writes(machine):
    from rpi_hwid import label_input
    event(machine, "pi-identified", PI_IDENTIFIED)
    event(machine, "fpga-board-identified", ACORN)
    text = hwid.dumps(hwid.build(machine).document)
    assert hwid.dumps(label_input.load(text)) == text


@pytest.mark.django_db
def test_rpi_hwid_says_registration_alone_is_not_enough(machine):
    missing = hwid.missing(hwid.build(machine).document)
    # the header nothing read, and the Pi 5 facts only pi-identified gives
    assert {"header", "fan", "rtc_battery"} <= set(missing["board"])


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
