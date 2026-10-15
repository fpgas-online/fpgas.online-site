"""Run the node behaviour tests for management's switch-events.js (tests/js/switch-events.test.mjs), and check the
dashboard page carries what the script needs."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from django.test import Client

from tests.test_management_switches import HOSTS, reader  # noqa: F401  (the fixture)

TEST_FILE = Path(__file__).parent / "js" / "switch-events.test.mjs"


def test_switch_events_js_behaviour():
    node = shutil.which("node")
    if node is None:
        # GitHub's ubuntu runners ship node, so CI must never skip this.
        if os.environ.get("CI"):
            pytest.fail("node is not installed on this CI runner")
        pytest.skip("node is not installed")
    proc = subprocess.run([node, "--test", str(TEST_FILE)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.django_db
def test_the_dashboard_loads_the_script_and_keeps_what_it_draws_on(reader):  # noqa: F811
    """switch-events.js finds a port's Link mark and Speed by their classes and the box by its id (#130): the served
    row and box carry them; there is no list of panel states to hand it any more (the drawing needs no key)."""
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    assert '<script src="/static/management/switch-events.js" defer></script>' in html
    assert 'id="mgmt-panel-states"' not in html and "SwitchEvents.createKeeper()" in html
    row = html.split('<tr id="switch-1-port-1">')[1].split("</tr>")[0]
    assert 'class="mgmt-mark c-link mgmt-up"' in row and '<span class="mgmt-glyph">↑</span>' in row
    assert '<td class="c-speed"' in row
    assert '<g id="switch-1-panel-1" class="mgmt-port mgmt-box-' in html and "mgmt-edge-up" in html
    # the polling fallback is still there
    assert "setInterval(tick, 15 * 1000)" in html
