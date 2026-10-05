from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from ttsite.models import Board

DATA = Path(__file__).parent / "data" / "tt-boards.yaml"


@pytest.mark.django_db
def test_load_creates_rows():
    call_command("ttsite_loadboards", str(DATA))
    assert set(Board.objects.values_list("slug", flat=True)) == {"tt06", "tt03", "kianv-1", "fpga-1"}
    tt06 = Board.objects.get(slug="tt06")
    assert (tt06.kind, tt06.shuttle, tt06.pcb) == ("asic", "tt06", "TT demo board v3 (RP2040)")
    assert tt06.links == [{"label": "TT06 chip page", "url": "https://tinytapeout.com/chips/tt06/"}]
    # DATA is a file from before the catalogue was by USB serial: its switch, port, enabled and commander
    # are not read, and no row gets a serial
    assert set(Board.objects.values_list("usb_serial", flat=True)) == {""}
    assert not {"switch", "port", "enabled", "commander"} & {f.name for f in Board._meta.get_fields()}
    assert Board.objects.get(slug="fpga-1").sort_order == 5


@pytest.mark.django_db
def test_load_is_idempotent_and_updates():
    call_command("ttsite_loadboards", str(DATA))
    Board.objects.filter(slug="tt06").update(title="stale")
    call_command("ttsite_loadboards", str(DATA))
    assert Board.objects.count() == 4
    assert Board.objects.get(slug="tt06").title == "Tiny Tapeout 6"


@pytest.mark.django_db
def test_prune_removes_missing_only_with_flag(tmp_path):
    call_command("ttsite_loadboards", str(DATA))
    Board.objects.create(slug="gone", kind="asic", title="gone")
    call_command("ttsite_loadboards", str(DATA))
    assert Board.objects.filter(slug="gone").exists()
    call_command("ttsite_loadboards", str(DATA), "--prune")
    assert not Board.objects.filter(slug="gone").exists()


@pytest.mark.django_db
def test_bad_shape_raises(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- just: list\n")
    with pytest.raises(Exception, match="tt_boards"):
        call_command("ttsite_loadboards", str(p))


@pytest.mark.django_db
def test_unknown_kind_raises(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("tt_boards:\n  - {slug: x, port: 1, kind: banana, title: x}\n")
    with pytest.raises(Exception, match="kind"):
        call_command("ttsite_loadboards", str(p))


BY_SERIAL = """tt_boards:
  - {slug: fpga-1, usb_serial: "4df39a7a6856f86f", kind: fpga, title: "TT FPGA emulation board 1"}
  - {slug: fpga-4, usb_serial: "a2961e5cac65b25f", kind: fpga, title: "TT FPGA emulation board 4"}
  - {slug: tt09, kind: asic, shuttle: tt09, title: "Tiny Tapeout 9"}
"""


@pytest.mark.django_db
def test_a_catalogue_by_usb_serial_loads(tmp_path):
    p = tmp_path / "tt-boards.yaml"
    p.write_text(BY_SERIAL)
    call_command("ttsite_loadboards", str(p))
    assert dict(Board.objects.values_list("slug", "usb_serial")) == {
        "fpga-1": "4df39a7a6856f86f", "fpga-4": "a2961e5cac65b25f", "tt09": ""}
    # a serial taken away is followed
    p.write_text(BY_SERIAL.replace('usb_serial: "a2961e5cac65b25f", ', ""))
    call_command("ttsite_loadboards", str(p))
    assert Board.objects.get(slug="fpga-4").usb_serial == ""


SWAPS = [
    # the two serials change places
    ("4df39a7a6856f86f", "a2961e5cac65b25f", "a2961e5cac65b25f", "4df39a7a6856f86f"),
    # a serial moves to the row listed earlier, and to the one listed later
    ("", "a2961e5cac65b25f", "a2961e5cac65b25f", ""),
    ("4df39a7a6856f86f", "", "", "4df39a7a6856f86f"),
]


@pytest.mark.django_db
@pytest.mark.parametrize("a_was, b_was, a_now, b_now", SWAPS)
def test_a_serial_moves_from_one_row_to_another(tmp_path, a_was, b_was, a_now, b_now):
    def write(a, b):
        p = tmp_path / "tt-boards.yaml"
        p.write_text(f'tt_boards:\n  - {{slug: a, usb_serial: "{a}", kind: fpga, title: a}}\n'
                     f'  - {{slug: b, usb_serial: "{b}", kind: fpga, title: b}}\n')
        return str(p)

    call_command("ttsite_loadboards", write(a_was, b_was))
    call_command("ttsite_loadboards", write(a_now, b_now))
    assert dict(Board.objects.values_list("slug", "usb_serial")) == {"a": a_now, "b": b_now}


@pytest.mark.django_db
def test_a_serial_held_by_a_row_that_is_not_in_the_file_is_a_clear_refusal(tmp_path):
    Board.objects.create(slug="by-hand", usb_serial="4df39a7a6856f86f", kind="fpga", title="x")
    p = tmp_path / "tt-boards.yaml"
    p.write_text(BY_SERIAL)
    with pytest.raises(CommandError, match="belongs to a row that is not in the file"):
        call_command("ttsite_loadboards", str(p))
    assert list(Board.objects.values_list("slug", flat=True)) == ["by-hand"]
    call_command("ttsite_loadboards", str(p), "--prune")
    assert Board.objects.get(slug="fpga-1").usb_serial == "4df39a7a6856f86f"


@pytest.mark.django_db
@pytest.mark.parametrize("serial", ["0xA2961E5CAC65B25F", "A2961E5CAC65B25F", "a2961e5cac65b25f ", "abc", "-"])
def test_a_serial_a_board_could_not_report_is_refused(tmp_path, serial):
    p = tmp_path / "bad.yaml"
    p.write_text(f'tt_boards:\n  - {{slug: x, usb_serial: "{serial}", kind: asic, title: x}}\n')
    with pytest.raises(CommandError, match="is not a serial as a board reports it"):
        call_command("ttsite_loadboards", str(p))


@pytest.mark.django_db
def test_a_load_that_fails_part_way_changes_nothing(tmp_path):
    """The first row would load; the second names a serial held by a row that is not in the file."""
    Board.objects.create(slug="by-hand", usb_serial="a2961e5cac65b25f", kind="fpga", title="x")
    Board.objects.create(slug="fpga-1", usb_serial="", kind="fpga", title="old title")
    p = tmp_path / "tt-boards.yaml"
    p.write_text(BY_SERIAL)
    with pytest.raises(CommandError, match="belongs to a row that is not in the file"):
        call_command("ttsite_loadboards", str(p))
    assert dict(Board.objects.values_list("slug", "usb_serial")) == {"by-hand": "a2961e5cac65b25f", "fpga-1": ""}
    assert Board.objects.get(slug="fpga-1").title == "old title"


@pytest.mark.django_db
@pytest.mark.parametrize("rows, why", [
    ("  - {slug: 123, kind: asic, title: x}\n", "slug that is text"),
    ("  - {slug: x, kind: asic, title: x}\n  - {slug: x, kind: fpga, title: y}\n", "two entries have the slug"),
])
def test_a_slug_that_is_not_text_or_is_used_twice_is_refused(tmp_path, rows, why):
    p = tmp_path / "bad.yaml"
    p.write_text("tt_boards:\n" + rows)
    with pytest.raises(CommandError, match=why):
        call_command("ttsite_loadboards", str(p))


@pytest.mark.django_db
def test_a_slug_that_is_a_boards_own_address_is_refused(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("tt_boards:\n  - {slug: tt-a2961e5cac65b25f, kind: asic, title: x}\n")
    with pytest.raises(CommandError, match="address of a board no row names"):
        call_command("ttsite_loadboards", str(p))


@pytest.mark.django_db
def test_two_rows_naming_one_board_are_refused(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(BY_SERIAL.replace("a2961e5cac65b25f", "4df39a7a6856f86f"))
    with pytest.raises(Exception, match="same usb_serial"):
        call_command("ttsite_loadboards", str(p))
    assert Board.objects.count() == 0


@pytest.mark.django_db
def test_a_serial_yaml_read_as_a_number_is_refused(tmp_path):
    """An all-digit serial written without quotes is a number to YAML, not the text on the label."""
    p = tmp_path / "bad.yaml"
    p.write_text("tt_boards:\n  - {slug: x, usb_serial: 1234567890123456, kind: asic, title: x}\n")
    with pytest.raises(Exception, match="quoted text"):
        call_command("ttsite_loadboards", str(p))


@pytest.mark.django_db
def test_a_key_nobody_reads_is_refused(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("tt_boards:\n  - {slug: x, kind: asic, title: x, usb_serail: abc}\n")
    with pytest.raises(Exception, match="unknown keys"):
        call_command("ttsite_loadboards", str(p))


@pytest.mark.django_db
def test_non_dict_entry_raises(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("tt_boards:\n  - just-a-string\n")
    with pytest.raises(Exception, match="entry"):
        call_command("ttsite_loadboards", str(p))


@pytest.mark.django_db
def test_prune_refuses_empty_file_without_allow_empty(tmp_path):
    call_command("ttsite_loadboards", str(DATA))
    empty = tmp_path / "empty.yaml"
    empty.write_text("tt_boards: []\n")
    with pytest.raises(Exception, match="--allow-empty"):
        call_command("ttsite_loadboards", str(empty), "--prune")
    assert Board.objects.count() == 4


@pytest.mark.django_db
def test_prune_allows_empty_file_with_flag(tmp_path):
    call_command("ttsite_loadboards", str(DATA))
    empty = tmp_path / "empty.yaml"
    empty.write_text("tt_boards: []\n")
    call_command("ttsite_loadboards", str(empty), "--prune", "--allow-empty")
    assert Board.objects.count() == 0


@pytest.mark.django_db
def test_empty_file_without_prune_is_a_no_op(tmp_path):
    call_command("ttsite_loadboards", str(DATA))
    empty = tmp_path / "empty.yaml"
    empty.write_text("tt_boards: []\n")
    call_command("ttsite_loadboards", str(empty))
    assert Board.objects.count() == 4
