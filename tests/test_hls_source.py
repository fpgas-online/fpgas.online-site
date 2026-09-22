"""Every video.js player must get its HLS source from data-setup, never <source>.

Chrome (142+) plays HLS natively. Given ``<video class="video-js"><source
type="application/x-mpegURL">`` it sometimes selects the source itself before
video.js initialises; video.js's Html5 tech then sees ``currentSrc`` already
equal to its source, skips ``setSource()`` and leaves Chrome's native player in
charge instead of its own VHS engine. On our 1 s-fragment live playlists that
player never delivers a frame and holds the window ``load`` event for ever
(seen on welland in Chrome 145, 2026-09-21: 3 page loads in 12 stuck, 0 in 16
with the source moved into data-setup). With no <source> child the element has
nothing to select, so video.js always sets the source and always picks VHS.
"""

import json
from html.parser import HTMLParser

import pytest
from django.test import Client
from pibfpgas.models import Pi
from ttsite.models import Board


class Players(HTMLParser):
    def __init__(self):
        super().__init__()
        self.setups, self.source_tags = [], 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "source":
            self.source_tags += 1
        if tag == "video" and "video-js" in (a.get("class") or ""):
            self.setups.append(json.loads(a["data-setup"]))


def players(client, path):
    p = Players()
    p.feed(client.get(path).content.decode())
    assert p.source_tags == 0, f"{path}: <source> lets the browser's native HLS grab the stream"
    return p.setups


@pytest.fixture
def welland():
    return Client(HTTP_HOST="welland.fpgas.online")


@pytest.fixture
def tinytapeout():
    return Client(HTTP_HOST="tinytapeout.fpgas.online")


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/fpgas/pi21.html", "/fpgas/tt.html", "/fpgas/"])
def test_welland_players_take_their_source_from_data_setup(welland, settings, path):
    settings.DOMAIN_NAME = "welland.fpgas.online"
    settings.PI_PW = "cGFzc3dvcmQ="  # the board page renders it into the wssh iframe
    Pi.objects.create(port=21, switch=2, fpga_board="TT FPGA emulation (iCE40UP5K)")
    (setup,) = players(welland, path)
    assert setup["liveui"] is True
    assert setup["sources"] == [
        {"src": "https://welland.fpgas.online/live/pi-sw2-p21.m3u8", "type": "application/x-mpegURL"}]


@pytest.mark.django_db
def test_tinytapeout_board_player_takes_its_source_from_data_setup(tinytapeout):
    Board.objects.create(slug="tt07", switch=2, port=7, kind="asic", shuttle="tt07", title="Tiny Tapeout 7")
    (setup,) = players(tinytapeout, "/board/tt07/")
    assert setup["liveui"] is True
    assert setup["sources"] == [{"src": "/live/pi-sw2-p7.m3u8", "type": "application/x-mpegURL"}]


@pytest.mark.django_db
def test_tinytapeout_index_thumbnails_take_their_source_from_data_setup(tinytapeout):
    Board.objects.create(slug="tt07", switch=2, port=7, kind="asic", shuttle="tt07", title="Tiny Tapeout 7")
    Board.objects.create(slug="fpga-1", switch=2, port=12, kind="fpga", title="TT FPGA emulation board 1")
    setups = players(tinytapeout, "/")
    assert sorted(s["sources"][0]["src"] for s in setups) == ["/live/pi-sw2-p12.m3u8", "/live/pi-sw2-p7.m3u8"]
    assert all(s["sources"][0]["type"] == "application/x-mpegURL" and s["fluid"] is True for s in setups)
