"""Run the node behaviour tests for management's switch-events.js (tests/js/switch-events.test.mjs), and check the
dashboard page carries what the script needs."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from django.test import Client

from management import panel
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
def test_the_dashboard_loads_the_script_and_gives_it_the_panel_states(reader):  # noqa: F811
    html = Client(HTTP_HOST=HOSTS[0]).get("/management/switches/").content.decode()
    assert '<script src="/static/management/switch-events.js" defer></script>' in html
    states = re.search(r'<script id="mgmt-panel-states" type="application/json">(.*?)</script>', html).group(1)
    assert json.loads(states) == [list(s) for s in panel.STATES]
    # the polling fallback is still there
    assert "setInterval(tick, 15 * 1000)" in html
