from pathlib import Path

import pytest
from django.core.management import call_command
from ttsite.models import Board

DATA = Path(__file__).parent / "data" / "tt-boards.yaml"


@pytest.mark.django_db
def test_load_creates_rows():
    call_command("ttsite_loadboards", str(DATA))
    assert set(Board.objects.values_list("slug", flat=True)) == {"tt06", "tt03", "kianv-1", "fpga-1"}
    tt06 = Board.objects.get(slug="tt06")
    assert (tt06.port, tt06.kind, tt06.shuttle, tt06.pcb) == (6, "asic", "tt06", "TT demo board v3 (RP2040)")
    assert tt06.links == [{"label": "TT06 chip page", "url": "https://tinytapeout.com/chips/tt06/"}]
    assert Board.objects.get(slug="tt03").enabled is False
    assert Board.objects.get(slug="kianv-1").port is None
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
    Board.objects.create(slug="gone", port=40, kind="asic", title="gone")
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
