"""Run the node behaviour tests for ttsite's library.js (tests/js/library.test.mjs): the board page's VGA library
panel (matching, shuttle order, paging, badges, renderPage on a fake page, and the load controller)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TEST_FILE = Path(__file__).parent / "js" / "library.test.mjs"


def test_library_js_behaviour():
    node = shutil.which("node")
    if node is None:
        # GitHub's ubuntu runners ship node, so CI must never skip this.
        if os.environ.get("CI"):
            pytest.fail("node is not installed on this CI runner")
        pytest.skip("node is not installed")
    proc = subprocess.run([node, "--test", str(TEST_FILE)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
