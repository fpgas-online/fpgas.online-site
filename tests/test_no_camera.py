"""A board whose Pi publishes no camera stream shows a static "No camera on
this board" box where the player would be (issue #57).

The pages load /js/no-camera.js, which asks for each player's playlist and
replaces the player with the box when the playlist is 404. Its behaviour is
tested in node (tests/js/no-camera.test.mjs); here: that every welland page
with a player loads it, and that the players are unchanged otherwise (no
<source>, see test_hls_source.py)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from django.test import Client

from tests.fleet_pis import verified_pi

TEST_FILE = Path(__file__).parent / "js" / "no-camera.test.mjs"
SCRIPT = Path(__file__).resolve().parents[1] / "pibfpgas" / "src" / "pibfpgas" / "static" / "js" / "no-camera.js"


def test_no_camera_js_behaviour():
    node = shutil.which("node")
    if node is None:
        # GitHub's ubuntu runners ship node, so CI must never skip this.
        if os.environ.get("CI"):
            pytest.fail("node is not installed on this CI runner")
        pytest.skip("node is not installed")
    proc = subprocess.run([node, "--test", str(TEST_FILE)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/fpgas/", "/fpgas/pi-sw9-p21.html", "/fpgas/tt.html"])
def test_every_page_with_a_player_loads_the_check(settings, path):
    settings.DOMAIN_NAME = "site.example"
    settings.PI_PW = "cGFzc3dvcmQ="  # the board page renders it into the wssh iframe
    verified_pi("pi-sw9-p21", ("tt", "tt-fpga"))  # the TT page needs a TT board on port 21
    html = Client(HTTP_HOST="site.example").get(path).content.decode()
    assert 'class="video-js"' in html
    assert '<script defer src="/js/no-camera.js"></script>' in html


def test_the_script_is_a_static_file_beside_whep_live():
    # collected with the other pibfpgas static files and served at /js/no-camera.js
    assert SCRIPT.is_file()
    assert SCRIPT.with_name("whep-live.js").is_file()
