"""Whatever a Pi (or anything on the site LAN) sends, the fleet pages stay up
and one bad event spoils nothing else (label contract §35)."""

import itertools
import json

import pytest
from django.test import Client
from django.utils import timezone
from fleet.models import BootEvent, Machine
from fleet.services import register_document, status

from fleet import hwid

SERIAL = "0cd35697db04a4ab"
REGISTRATION = {"schema": 1,
                "machine": {"serial": SERIAL, "model": "Raspberry Pi 5 Model B Rev 1.1",
                            "revision_code": "b04171", "mem_total_kb": 2003128,
                            "macs": {"eth0": "88:a2:9e:45:85:77"}},
                "connection": {"site": "welland", "hostname": "pi-sw2-p48"}}
GOOD_PI = {"schema": "pi-identity/1", "reader": "rpi-hwid", "power_class": "gpio-poe-hat",
           "header": '["Waveshare PoE M.2 HAT+ (B)"]', "fan": "true", "rtc_battery": "false"}
GOOD_ACORN = {"schema": "fpga-identity/1", "board": "acorn", "kind": "acorn",
              "bdf": "0001:01:00.0", "dna": "0x0054b48664b04854", "idcode": "0x13636093"}

NAN, INF = "nan", "inf"
# Values that are not what a field holds: wrong JSON types, nesting,
# unhashables, non-finite numbers, and JSON rpi-hwid refuses.
BAD_VALUES = [5000, 1.5, True, None, ["acorn"], {"x": 1}, {"a": {"b": [1, {"c": 2}]}},
              "", NAN, INF, "1e999", "[1,2]", '[{"x":1}]', '{"a":1}', "[", "0x", "x" * 5000]
PI_KEYS = ["power_class", "compatible", "hat_uuid", "fan", "rtc_battery", "max_current_ma",
           "ext5v_v", "header", "macs", "usb_net", "schema", "reader"]
FPGA_KEYS = ["kind", "board", "serial", "bdf", "usb", "usb_serial", "dna", "idcode",
             "flash_uid_bits", "flash_jedec", "schema", "mcu"]


@pytest.fixture
def c():
    return Client(HTTP_HOST="welland.fpgas.online")


@pytest.fixture
def machine():
    register_document(REGISTRATION)
    status(SERIAL, {"online": True, "boot_id": "b1", "uptime_s": 60})
    return Machine.objects.get()


def send(machine, stage, detail):
    BootEvent.objects.create(machine=machine, boot_id="b1", stage=stage, detail=detail,
                             ts=timezone.now())


def assert_pages_stand(c):
    assert c.get(f"/fleet/{SERIAL}/").status_code == 200
    r = c.get(f"/fleet/{SERIAL}/rpi-hwid.json")
    assert r.status_code == 200
    json.loads(r.content)


@pytest.mark.django_db
@pytest.mark.parametrize(("key", "value"), list(itertools.product(PI_KEYS, BAD_VALUES)))
def test_a_bad_pi_identified_value_is_dropped_and_the_pages_stand(c, machine, key, value):
    send(machine, "pi-identified", GOOD_PI)
    send(machine, "pi-identified", {**GOOD_PI, key: value})
    assert_pages_stand(c)
    built = hwid.build(machine)
    # the newer event is taken whole or dropped whole, with the good one
    # used instead: never a mixture, never nothing
    taken = value if key == "power_class" else "gpio-poe-hat"
    assert built.document["summary"].get("power_class") in ("gpio-poe-hat", taken)


@pytest.mark.django_db
@pytest.mark.parametrize(("key", "value"), list(itertools.product(FPGA_KEYS, BAD_VALUES)))
def test_a_bad_board_value_spoils_no_other_board_and_the_pages_stand(c, machine, key, value):
    send(machine, "fpga-board-identified", GOOD_ACORN)
    send(machine, "fpga-board-identified", {**GOOD_ACORN, "board": "other", "bdf": "x", key: value})
    assert_pages_stand(c)
    fpga = hwid.build(machine).document["summary"].get("fpga", [])
    assert any(b.get("dna") == "0x0054b48664b04854" for b in fpga)


@pytest.mark.django_db
@pytest.mark.parametrize("detail", [
    5, "text", [], ["schema"], {}, {"schema": None}, {"schema": 1}, {"schema": ["x"]},
    {"schema": {"a": 1}}, {"schema": "pi-identity/x"}, {"schema": "pi-identity/2"},
    {"schema": "fpga-identity/1"}, {"schema": "fpga-identity/1", "kind": ""},
    # a newer version's extra keys are ignored (§16), never passed on
    {**GOOD_PI, "new_field": "x", "power_class_v2": "y"},
    {**GOOD_ACORN, "new_field": "x", "macs": "[1]"},
    # rpi-hwid refuses these, though each is valid JSON of the right shape
    {**GOOD_PI, "macs": "[1,2]"}, {**GOOD_PI, "usb_net": '[{"x":1}]'},
    {**GOOD_PI, "macs": '[{"kind":"eth","mac":"aa","signal":null,"extra":1}]'},
    {**GOOD_PI, "header": "[1]"}, {**GOOD_PI, "ext5v_v": "nan"},
])
@pytest.mark.parametrize("stage", ["pi-identified", "fpga-board-identified"])
def test_any_detail_leaves_the_pages_standing(c, machine, stage, detail):
    send(machine, stage, detail)
    assert_pages_stand(c)


@pytest.mark.django_db
def test_a_refused_event_is_named_in_a_note(machine):
    send(machine, "pi-identified", {**GOOD_PI, "macs": "[1,2]"})
    send(machine, "fpga-board-identified", {**GOOD_ACORN, "flash_uid_bits": 128})
    notes = hwid.build(machine).notes
    assert any(n.startswith("pi-identified ") and "summary.macs[0]" in n and "ignored" in n
               for n in notes)
    assert any(n.startswith("fpga-board-identified ") and "flash_uid_bits: not a string: 128" in n
               for n in notes)


@pytest.mark.django_db
@pytest.mark.parametrize("machine_section", [
    {"serial": SERIAL, "model": 5, "revision_code": ["x"], "mem_total_kb": "lots",
     "macs": ["eth0"]},
    {"serial": SERIAL, "model": {"a": 1}, "mem_total_kb": True, "macs": {"eth0": 5}},
    {"serial": SERIAL, "mem_total_kb": -1, "macs": {"eth0": None, "wlan0": ["x"]}},
])
def test_a_bad_registration_leaves_the_pages_standing(c, machine_section):
    register_document({"schema": 1, "machine": machine_section,
                       "connection": {"site": "welland", "hostname": 'bad"name\\x'}})
    assert_pages_stand(c)
    r = Client(HTTP_HOST="welland.fpgas.online").get(f"/fleet/{SERIAL}/rpi-hwid.json")
    # a host name that cannot be a file name gives the serial's
    assert r["Content-Disposition"] == f'attachment; filename="{SERIAL}.json"'
