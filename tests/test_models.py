"""The catalogue: words about a board named by its USB serial. Nothing in it says where a board is."""

import pytest
from django.db import IntegrityError
from ttsite.models import Board


@pytest.mark.django_db
def test_a_row_says_nothing_of_where_a_board_is():
    names = {f.name for f in Board._meta.get_fields()}
    assert "usb_serial" in names
    assert not {"switch", "port", "ip", "hostname", "enabled", "commander"} & names
    b = Board.objects.create(slug="tt06", usb_serial="06060606aaaa0006", kind="asic", title="TT06")
    for gone in ("hostname", "ip", "stream_url", "live"):
        assert not hasattr(b, gone)


@pytest.mark.django_db
def test_one_row_per_board_and_any_number_without_a_serial():
    Board.objects.create(slug="a", usb_serial="06060606aaaa0006", kind="asic", title="a")
    Board.objects.create(slug="soon-1", kind="asic", title="x")
    Board.objects.create(slug="soon-2", kind="asic", title="y")
    with pytest.raises(IntegrityError):
        Board.objects.create(slug="b", usb_serial="06060606aaaa0006", kind="asic", title="b")


@pytest.mark.django_db
def test_boards_order_by_sort_order_then_slug():
    Board.objects.create(slug="b", kind="asic", title="b", sort_order=1)
    Board.objects.create(slug="a", kind="asic", title="a", sort_order=1)
    Board.objects.create(slug="z", kind="asic", title="z", sort_order=0)
    assert list(Board.objects.values_list("slug", flat=True)) == ["z", "a", "b"]


@pytest.mark.django_db
def test_kind_choices_and_str():
    b = Board.objects.create(slug="fpga-1", kind="fpga", title="FPGA 1")
    assert b.get_kind_display() == "FPGA emulation"
    assert str(b) == "fpga-1 (FPGA 1)"
