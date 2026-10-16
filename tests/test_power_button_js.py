"""Run the node behaviour tests for management's power-button.js (tests/js/power-button.test.mjs): the switch
dashboard's PoE button (round 5: toasts, the turning ⏻, the countdown)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TEST_FILE = Path(__file__).parent / "js" / "power-button.test.mjs"


def test_power_button_js_behaviour():
    node = shutil.which("node")
    if node is None:
        # GitHub's ubuntu runners ship node, so CI must never skip this.
        if os.environ.get("CI"):
            pytest.fail("node is not installed on this CI runner")
        pytest.skip("node is not installed")
    proc = subprocess.run([node, "--test", str(TEST_FILE)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


