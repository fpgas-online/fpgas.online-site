"""tinytapeout.fpgas.online follows the device each Pi's boot check reported.

Tim, 4 October 2026: "Don't hardcode port names and which device is on which port, just record the hardware
exists using names / values on the generated labels." 5 October 2026: "The features shown on the board
specific page should be keyed on the device reported by the verify." and "The verify should be the authority
on what is connected."

So: no list is needed for a board to appear and work; a catalogue row adds words to a USB serial and decides
nothing; a board moved to another port keeps its page; a board the check could not read says so.
"""

import datetime

import pytest
from django.test import Client
from django.utils import timezone
from fleet.models import BootEvent, Machine
from ttsite.models import Board

from ttsite import boards, daemon

from .fleet_pis import machine, verified_detail, verified_pi

FPGA4, CHIP6, UNREAD = "a2961e5cac65b25f", "06060606aaaa0006", "0bad0bad0bad0bad"
# The identity the boot check sent for the FPGA demo board Tim moved on 5 October 2026 (read on its Pi).
REAL_FPGA = {"usb_serial": FPGA4, "serial": FPGA4, "usb": "1-1.2", "mcu": "RP2350", "shuttle": "-", "chip": "fpga",
             "repo": "-", "commit": "-", "demoboard": "TTDBv3 [3.2]", "demoboard_version": "-", "sdk": "3.1.0"}
MAIN_PY = "the demo board's main.py is not known to be the SDK's own"


def tt(usb_serial, chip=None, **identity):
    """A Tiny Tapeout board as a boot check reports it: the variant is `tt-fpga` whatever the board is."""
    return ("tt", "tt-fpga", {"usb_serial": usb_serial, **({"chip": chip} if chip else {}), **identity})


@pytest.fixture
def c():
    return Client(HTTP_HOST="tinytapeout.fpgas.online")


@pytest.fixture
def commander(settings):
    settings.TTSITE_COMMANDER_VERSION = "0.2.0"
    settings.TTSITE_COMMANDER_LEGACY_VERSION = "0.1.0"


# --- no list is needed --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_board_in_no_catalogue_has_the_full_page_of_its_kind(c, commander):
    assert Board.objects.count() == 0
    verified_pi("pi-sw2-p13", ("tt", "tt-fpga", REAL_FPGA))
    index = c.get("/").content.decode()
    assert f'href="/board/tt-{FPGA4}/"' in index and "/live/pi-sw2-p13.m3u8" in index
    html = c.get(f"/board/tt-{FPGA4}/").content.decode()
    assert "TT FPGA demo board " + FPGA4 in html
    assert 'data-kind="fpga"' in html and 'id="tt-commander"' in html
    assert 'id="tt-gallery"' in html and 'id="tt-upload"' in html and 'id="tt-power"' in html
    assert f'data-ws-path="/ws/board/tt-{FPGA4}/serial"' in html
    assert f'data-api-base="/api/board/tt-{FPGA4}"' in html
    assert "/live/pi-sw2-p13.m3u8" in html and 'data-pistat-groups="pi-sw2-p13"' in html
    assert "TTDBv3 [3.2]" in html and "SDK 3.1.0" in html and "USB serial " + FPGA4 in html
    assert "tt-commander/0.2.0/tt-commander-embed.js" in html


@pytest.mark.django_db
def test_a_chip_board_in_no_catalogue_has_the_commander_and_no_gallery(c, commander):
    verified_pi("pi-sw2-p6", tt(CHIP6, "asic", shuttle="tt06", sdk="2.0.4"))
    html = c.get(f"/board/tt-{CHIP6}/").content.decode()
    assert f"Tiny Tapeout board {CHIP6} (tt06)" in html and 'data-kind="asic"' in html
    assert 'id="tt-commander"' in html and 'data-shuttle="tt06"' in html
    assert "tinytapeout.com/chips/tt06/" in html
    assert 'id="tt-gallery"' not in html and 'id="tt-upload"' not in html
    assert c.get(f"/api/board/tt-{CHIP6}/designs").status_code == 404


@pytest.mark.django_db
def test_the_design_api_serves_a_board_in_no_catalogue(c, monkeypatch):
    verified_pi("pi-sw2-p13", ("tt", "tt-fpga", REAL_FPGA))
    asked = []
    monkeypatch.setattr(daemon, "designs", lambda b: asked.append(b.ip) or (200, {"enabled": None, "designs": []}))
    assert c.get(f"/api/board/tt-{FPGA4}/designs").status_code == 200
    assert asked == ["10.21.2.13"]


# --- the catalogue adds words and decides nothing ---------------------------------------------------


@pytest.mark.django_db
def test_a_catalogue_row_names_the_page_and_does_not_decide_its_features(c, commander):
    """The row says `asic` and names a shuttle; the board said it carries the FPGA breakout."""
    Board.objects.create(slug="fpga-4", usb_serial=FPGA4, kind="asic", shuttle="tt06",
                         title="TT FPGA emulation board 4", description="Fourth from the left.")
    verified_pi("pi-sw2-p13", ("tt", "tt-fpga", REAL_FPGA))
    html = c.get("/board/fpga-4/").content.decode()
    assert "TT FPGA emulation board 4" in html and "Fourth from the left." in html
    assert 'data-kind="fpga"' in html and 'id="tt-gallery"' in html
    assert 'data-shuttle=""' in html and "tinytapeout.com/chips/" not in html
    # one page per board: the serial's own address is not a second one
    assert c.get(f"/board/tt-{FPGA4}/").status_code == 404
    index = c.get("/").content.decode()
    card = index.index("TT FPGA emulation board 4")
    assert index.index('<section id="fpga">') < card < index.index('<section id="kianv">')
    assert [p.slug for p in boards.pages() if p.kind == "fpga"] == ["fpga-4"]


@pytest.mark.django_db
def test_the_commander_follows_the_sdk_the_board_named(c, commander):
    verified_pi("pi-sw2-p3", tt("03050305aaaa0035", "asic", shuttle="tt03p5", sdk="1.2.2"))
    verified_pi("pi-sw2-p6", tt(CHIP6, "asic", shuttle="tt06", sdk="2.0.4"))
    old = c.get("/board/tt-03050305aaaa0035/").content.decode()
    assert "tt-commander/legacy-0.1.0/tt-commander-embed.js" in old and "tt-commander/0.2.0/" not in old
    new = c.get(f"/board/tt-{CHIP6}/").content.decode()
    assert "tt-commander/0.2.0/tt-commander-embed.js" in new and "legacy" not in new


# --- a board is where its Pi says it is ---------------------------------------------------------------


@pytest.mark.django_db
def test_a_board_moved_to_another_port_keeps_its_page_and_everything_on_it(c, commander, monkeypatch):
    """5 October 2026: Tim moved this board from switch 2 port 36 to port 13. Nothing was edited."""
    Board.objects.create(slug="fpga-4", usb_serial=FPGA4, kind="fpga", title="TT FPGA emulation board 4")
    m = verified_pi("pi-sw2-p36", ("tt", "tt-fpga", REAL_FPGA), serial="1000000062c15d4f")
    assert "/live/pi-sw2-p36.m3u8" in c.get("/board/fpga-4/").content.decode()
    # the Pi boots on the other port: it registers its new name and its check reports the same board
    m.hostname, m.last_boot_id = "pi-sw2-p13", "b2"
    m.save()
    BootEvent.objects.create(machine=m, boot_id="b2", stage="fpga-verified", ts=timezone.now(),
                             detail={"result": "pass", **verified_detail([("tt", "tt-fpga", REAL_FPGA)])})
    html = c.get("/board/fpga-4/").content.decode()
    assert "/live/pi-sw2-p13.m3u8" in html and "pi-sw2-p36" not in html
    assert 'data-port="13"' in html and 'id="tt-gallery"' in html
    asked = []
    monkeypatch.setattr(daemon, "health", lambda b, timeout=3.0: asked.append(b.ip) or {"reachable": True})
    monkeypatch.setattr(daemon, "enable", lambda b, name, body: asked.append(b.ip) or (200, {"enabled": name}))
    assert c.get("/board/fpga-4/status.json").json()["reachable"] is True
    assert c.post("/api/board/fpga-4/designs/tt_um_x/enable", data=b"{}", content_type="application/json").status_code == 200
    assert asked == ["10.21.2.13", "10.21.2.13"]
    r = c.get("/ws/board/fpga-4/serial")
    assert r.status_code == 200 and r["X-Accel-Redirect"] == "/_tt-serial/10.21.2.13"


def report(m, *boards_, boot_id=None, result="pass"):
    """One more fpga-verified event from a machine, in the boot it is running unless another is named."""
    return BootEvent.objects.create(machine=m, boot_id=boot_id or m.last_boot_id, stage="fpga-verified",
                                    ts=timezone.now(), detail={"result": result, **verified_detail(boards_)})


@pytest.mark.django_db
def test_a_board_moved_from_a_pi_that_keeps_running_is_on_the_pi_it_was_moved_to(c):
    """Both Pis are checking in and each one's report of the boot it is running names the board: the report
    that came last is where the board is, however the two Pis' status beats fall."""
    left = verified_pi("pi-sw2-p36", tt(FPGA4, "fpga"), serial="left-pi")
    verified_pi("pi-sw2-p13", tt(FPGA4, "fpga"), serial="new-pi")
    for beat_from in ("left-pi", "new-pi", "left-pi"):
        Machine.objects.filter(serial=beat_from).update(last_seen=timezone.now())
        board = boards.reported()[FPGA4]
        assert (board.pi.hostname, board.pi_serial, board.current) == ("pi-sw2-p13", "new-pi", True)
    # the Pi it was moved to restarts, runs its check, finds nothing: the board is still that Pi's, and that
    # Pi's port is the one Reset acts on. The Pi it left still names the board in a boot it is still running;
    # that older word does not win the board back.
    new = Machine.objects.get(serial="new-pi")
    new.last_boot_id = "b9"
    new.save()
    for event in (None, ("fpga-verifying", {}), ("fpga-verified", {"result": "missing"})):
        if event:
            BootEvent.objects.create(machine=new, boot_id="b9", stage=event[0], ts=timezone.now(), detail=event[1])
        board = boards.reported()[FPGA4]
        assert (board.pi.hostname, board.current) == ("pi-sw2-p13", False), event
        page = boards.page(f"tt-{FPGA4}")
        assert (page.switch, page.port) == (2, 13) and page.can_power_cycle and not page.has_commander
        assert boards.reported_port(2, 13)
    # ... and when it is power-cycled (the bridge says goodbye, or its beats just stop): Reset pressed once
    # must not move the board's page, and its next Reset, to the Pi it left
    for gone in ({"online": False}, {"online": True, "last_seen": timezone.now() - datetime.timedelta(minutes=10)}):
        Machine.objects.filter(serial="new-pi").update(**gone)
        board = boards.reported()[FPGA4]
        assert (board.pi.hostname, board.current, board.checked_in) == ("pi-sw2-p13", False, False), gone
        page = boards.page(f"tt-{FPGA4}")
        assert (page.switch, page.port) == (2, 13) and not page.has_commander and not page.has_gallery
    Machine.objects.filter(serial="new-pi").update(online=True, last_seen=timezone.now())
    # the Pi it left is still a Pi whose check named a Tiny Tapeout board: it can be reset too
    assert boards.reported_port(2, 36)
    # ... and the board goes back when the Pi it left reports it again
    report(left, tt(FPGA4, "fpga"))
    assert boards.reported()[FPGA4].pi.hostname == "pi-sw2-p36"


@pytest.mark.django_db
def test_the_newest_report_places_a_board_also_on_a_pi_that_has_stopped():
    """The check is the authority: the Pi that named the board last has it, and if that Pi has stopped it is
    the one to reset. An older report from a Pi that is still running does not take the board."""
    verified_pi("pi-sw2-p13", tt(FPGA4, "fpga"), serial="older-report")
    last = verified_pi("pi-sw2-p36", tt(FPGA4, "fpga"), serial="newest-report")
    Machine.objects.filter(pk=last.pk).update(online=False, last_seen=timezone.now() - datetime.timedelta(hours=2))
    board = boards.reported()[FPGA4]
    assert (board.pi.hostname, board.current, board.checked_in) == ("pi-sw2-p36", False, False)
    assert board.waiting == "its Raspberry Pi has stopped reporting"
    assert boards.reported_port(2, 36) and boards.reported_port(2, 13)


@pytest.mark.django_db
def test_the_serial_bridge_is_handed_to_the_pi_that_carries_the_board(c):
    verified_pi("pi-sw2-p13", ("tt", "tt-fpga", REAL_FPGA))
    r = c.get(f"/ws/board/tt-{FPGA4}/serial", HTTP_UPGRADE="websocket", HTTP_CONNECTION="Upgrade")
    assert r.status_code == 200 and r["X-Accel-Redirect"] == "/_tt-serial/10.21.2.13"
    assert c.get("/ws/board/nope/serial").status_code == 404
    Board.objects.create(slug="tt09", kind="asic", title="Tiny Tapeout 9")
    assert c.get("/ws/board/tt09/serial").status_code == 404  # a row no Pi reports


# --- a board the check could not read says so ---------------------------------------------------------


@pytest.mark.django_db
def test_a_board_the_check_could_not_read_has_a_page_that_says_why(c, commander, monkeypatch):
    Board.objects.create(slug="fpga-3", usb_serial=UNREAD, kind="fpga", title="TT FPGA emulation board 3")
    m = machine("pi-serial-3", "pi-sw2-p35")
    BootEvent.objects.create(machine=m, boot_id="b2", stage="fpga-verified", ts=timezone.now(), detail={
        "result": "error", **verified_detail([tt(UNREAD, tinytapeout_error=MAIN_PY)], result="error")})
    page = boards.page("fpga-3")
    assert (page.kind, page.live.reason, page.live.result) == ("unknown", MAIN_PY, "error")
    html = c.get("/board/fpga-3/").content.decode()
    assert 'id="tt-no-controls"' in html and "main.py is not known to be the SDK" in html
    assert 'id="tt-commander"' not in html and "tt-commander-embed.js" not in html and 'data-ws-path=""' in html
    assert 'id="tt-gallery"' not in html and 'id="tt-upload"' not in html
    # the camera, the status and Reset are the Pi's: still there
    assert "/live/pi-sw2-p35.m3u8" in html and 'id="tt-power"' in html and 'id="tt-status"' in html
    index = c.get("/").content.decode()
    assert '<section id="unknown">' in index and "main.py is not known to be the SDK" in index
    r = c.get("/api/board/fpga-3/designs")
    assert r.status_code == 404 and r.json() == {"error": "not an fpga board", "detail": MAIN_PY}


@pytest.mark.django_db
def test_the_variant_alone_does_not_make_a_page_an_fpga_page():
    """fpgas.online-test-designs issue #124: every Raspberry Pi USB device is reported as `tt-fpga`."""
    verified_pi("pi-sw2-p4", ("tt", "tt-fpga", {"usb_serial": CHIP6}))
    assert boards.reported()[CHIP6].kind == "unknown"


@pytest.mark.django_db
def test_no_unidentified_section_when_every_board_was_read(c):
    verified_pi("pi-sw2-p13", ("tt", "tt-fpga", REAL_FPGA))
    assert '<section id="unknown">' not in c.get("/").content.decode()


# --- what is and is not a Tiny Tapeout board here ----------------------------------------------------


@pytest.mark.django_db
def test_other_boards_and_pis_with_no_board_are_not_tiny_tapeout_pages(c):
    verified_pi("pi-sw2-p46", ("acorn", "cle-215+", {"serial": "0123456789abcdef"}))
    verified_pi("pi-sw2-p47")
    assert boards.reported() == {}
    assert "pi-sw2-p4" not in c.get("/").content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("serial", ["", "-", "../../etc", "A2961E5CAC65B25F", "abc", "a" * 33, "a2961e5c ac65b25f"])
def test_a_reported_serial_that_is_not_plainly_one_gets_no_page(c, serial, caplog):
    """The fleet broker takes reports from anything on the site LAN."""
    verified_pi("pi-sw2-p13", ("tt", "tt-fpga", {"usb_serial": serial, "chip": "fpga"}))
    assert boards.reported() == {}
    assert "no usable USB serial" in caplog.text
    assert c.get("/").status_code == 200


@pytest.mark.django_db
def test_a_pi_with_a_name_that_names_no_port_is_left_out():
    verified_pi("raspberrypi", tt(FPGA4, "fpga"))
    assert boards.reported() == {}


@pytest.mark.django_db
def test_a_board_keeps_its_page_and_its_reset_while_its_pi_restarts(c, commander):
    """A board is its Pi's until a check on that Pi names another: through a restart, while the check runs, and
    after a check that found nothing (a board that dropped off USB is the one that needs its Reset). The
    controls are only there when the boot the Pi is running has named the board."""
    Board.objects.create(slug="fpga-4", usb_serial=FPGA4, kind="fpga", title="TT FPGA emulation board 4")
    m = verified_pi("pi-sw2-p13", ("tt", "tt-fpga", REAL_FPGA), boot_id="b1")

    def page_now():
        page = boards.page("fpga-4")
        html = c.get("/board/fpga-4/").content.decode()
        assert 'id="tt-power"' in html and 'data-port="13"' in html and "/live/pi-sw2-p13.m3u8" in html
        assert boards.reported_port(2, 13)
        assert ('id="tt-commander"' in html) == ('id="tt-gallery"' in html) == page.live.current
        return page, html

    assert page_now()[0].live.current
    # the Pi restarts: its status beat names a new boot
    m.last_boot_id = "b2"
    m.save()
    page, html = page_now()
    assert not page.live.current and "has restarted and the boot check has not started yet" in html
    assert c.get("/api/board/fpga-4/designs").status_code == 503
    assert c.get("/ws/board/fpga-4/serial").status_code == 503
    # its check starts
    BootEvent.objects.create(machine=m, boot_id="b2", stage="fpga-verifying", ts=timezone.now(), detail={})
    assert "boot check is running" in page_now()[1]
    # ... and finds no board at all: the board is still this Pi's, and can be reset
    BootEvent.objects.create(machine=m, boot_id="b2", stage="fpga-verified", ts=timezone.now(),
                             detail={"result": "missing", "mode": "auto"})
    assert "did not name it this boot (the check&#x27;s result: missing)" in page_now()[1]
    # ... and then, run again, finds it
    report(m, ("tt", "tt-fpga", REAL_FPGA))
    assert page_now()[0].live.current


@pytest.mark.django_db
def test_a_pi_whose_check_names_another_board_no_longer_carries_the_first():
    m = verified_pi("pi-sw2-p13", tt(FPGA4, "fpga"), boot_id="b1")
    m.last_boot_id = "b2"
    m.save()
    report(m, tt(CHIP6, "asic", shuttle="tt06"))
    assert set(boards.reported()) == {CHIP6}
    assert boards.reported_port(2, 13)


@pytest.mark.django_db
def test_a_tiny_tapeout_board_is_not_another_boards_pi(c):
    """A Pi that carried a Tiny Tapeout board and now reports an Acorn is no Tiny Tapeout page and no port of
    this site's."""
    m = verified_pi("pi-sw2-p13", tt(FPGA4, "fpga"), boot_id="b1")
    m.last_boot_id = "b2"
    m.save()
    report(m, ("acorn", "cle-215+", {"serial": "0123456789abcdef"}))
    assert boards.reported() == {} and not boards.reported_port(2, 13)


# --- what a forged report can and cannot do (the fleet broker takes reports from anything on the site LAN) ---


@pytest.mark.django_db
@pytest.mark.parametrize("hostname", ["pi-sw300-p13", "pi-sw2-p99999", "pi-sw2-p256", "pi99999"])
def test_a_registered_name_that_gives_no_address_is_left_out(c, hostname):
    verified_pi(hostname, tt(FPGA4, "fpga"))
    assert boards.reported() == {}
    assert c.get(f"/ws/board/tt-{FPGA4}/serial").status_code == 404


@pytest.mark.django_db
def test_reported_text_is_cut_short_escaped_and_kept_out_of_links(c, commander):
    nasty = "<script>alert(1)</script>'\"" + "x" * 5000
    verified_pi("pi-sw2-p13", tt(CHIP6, "asic", shuttle="../../x?y=1#", sdk=nasty, demoboard=nasty, mcu=nasty))
    verified_pi("pi-sw2-p14", tt(UNREAD, tinytapeout_error=nasty))
    for path in ("/", f"/board/tt-{CHIP6}/", f"/board/tt-{UNREAD}/"):
        html = c.get(path).content.decode()
        assert "<script>alert(1)" not in html and "x" * 400 not in html, path
        assert "tinytapeout.com/chips/.." not in html
    assert boards.page(f"tt-{CHIP6}").shuttle == ""


@pytest.mark.django_db
def test_a_catalogue_slug_cannot_hide_a_board(c):
    """A row made by hand at a board's own address (the loader refuses one) does not get a second page."""
    Board.objects.create(slug=f"tt-{FPGA4}", kind="asic", title="made by hand")
    verified_pi("pi-sw2-p13", ("tt", "tt-fpga", REAL_FPGA))
    assert [p.slug for p in boards.pages()] == [f"tt-{FPGA4}"]


@pytest.mark.django_db
@pytest.mark.parametrize("sdk, commander_dir", [("1.2.2", "legacy-0.1.0"), ("v1.2.2", "legacy-0.1.0"), ("V1.2", "legacy-0.1.0"),
                                                ("0.9", "legacy-0.1.0"),
                                                ("2.0.4", "0.2.0"), ("10.0.0", "0.2.0"), ("-", "0.2.0")])
def test_which_commander_for_which_sdk(c, commander, sdk, commander_dir):
    verified_pi("pi-sw2-p6", tt(CHIP6, "asic", shuttle="tt06", sdk=sdk))
    assert f"tt-commander/{commander_dir}/tt-commander-embed.js" in c.get(f"/board/tt-{CHIP6}/").content.decode()


@pytest.mark.django_db
def test_the_serial_hand_off_is_a_get(c):
    verified_pi("pi-sw2-p13", ("tt", "tt-fpga", REAL_FPGA))
    assert c.post(f"/ws/board/tt-{FPGA4}/serial").status_code == 405


@pytest.mark.django_db
def test_the_polled_routes_answer_from_their_cache_without_asking_the_fleet(c, monkeypatch, django_assert_num_queries):
    verified_pi("pi-sw2-p13", ("tt", "tt-fpga", REAL_FPGA))
    monkeypatch.setattr(daemon, "health", lambda b, timeout=3.0: {"reachable": True})
    monkeypatch.setattr(daemon, "designs", lambda b: (200, {"enabled": None, "designs": []}))
    for path in (f"/board/tt-{FPGA4}/status.json", f"/api/board/tt-{FPGA4}/designs"):
        assert c.get(path).status_code == 200
        with django_assert_num_queries(0):
            assert c.get(path).status_code == 200
