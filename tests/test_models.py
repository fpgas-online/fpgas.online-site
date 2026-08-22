import pytest
from ttsite.models import Board


@pytest.mark.django_db
def test_properties_derive_from_switch_and_port():
    b = Board.objects.create(slug="tt06", port=6, kind="asic", shuttle="tt06", title="TT06")
    assert b.hostname == "pi-sw1-p6"
    assert b.ip == "10.21.1.6"
    assert b.stream_url == "/live/pi-sw1-p6.m3u8"
    assert b.serial_ws_path == "/ws/board/tt06/serial"
    assert b.api_base == "/api/board/tt06"
    assert b.live is True


@pytest.mark.django_db
def test_switch_two():
    b = Board.objects.create(slug="x", switch=2, port=7, kind="asic", title="x")
    assert (b.hostname, b.ip) == ("pi-sw2-p7", "10.21.2.7")


@pytest.mark.django_db
def test_unwired_board_is_not_live_and_has_no_address():
    b = Board.objects.create(slug="kianv-1", port=None, kind="kianv", title="KianV")
    assert b.live is False
    with pytest.raises(ValueError, match="no port"):
        _ = b.hostname
    with pytest.raises(ValueError, match="no port"):
        _ = b.ip


@pytest.mark.django_db
def test_disabled_board_is_not_live():
    b = Board.objects.create(slug="tt03", port=3, kind="asic", title="TT03", enabled=False)
    assert b.live is False


@pytest.mark.django_db
def test_asic_slots_are_ten_in_order():
    Board.objects.create(slug="tt06", port=6, kind="asic", shuttle="tt06", title="TT06")
    Board.objects.create(slug="fpga-1", port=12, kind="fpga", title="F")
    slots = Board.asic_slots()
    assert [n for n, _ in slots] == list(range(1, 11))
    assert slots[5][1].slug == "tt06"
    assert all(b is None for n, b in slots if n != 6)


@pytest.mark.django_db
def test_kind_choices_and_str():
    assert dict(Board.KIND_CHOICES).keys() == {"asic", "kianv", "fpga"}
    b = Board.objects.create(slug="tt06", port=6, kind="asic", title="Tiny Tapeout 6")
    assert str(b) == "tt06 (Tiny Tapeout 6)"
