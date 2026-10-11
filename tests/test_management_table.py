"""Table manners shared by the management tables (RULES 22): zebra stripes and sortable columns, in management.css
and one static script. The sort order itself is tested in node (tests/js/table.test.mjs)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TEST_FILE = Path(__file__).parent / "js" / "table.test.mjs"
STATIC = Path(__file__).resolve().parents[1] / "management" / "src" / "management" / "static" / "management"


def test_table_js_behaviour():
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node is not installed on this CI runner")
        pytest.skip("node is not installed")
    proc = subprocess.run([node, "--test", str(TEST_FILE)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_script_makes_buttons_in_headers_and_keeps_aria_sort_current():
    js = (STATIC / "table.js").read_text()
    for needle in ('"table.mgmt-sortable"', 'document.createElement("button")', '"aria-sort"', '"ascending"',
                   '"descending"', 'data-served'):
        assert needle in js


def test_the_stylesheet_stripes_rows_and_marks_the_sorted_column():
    css = (STATIC / "management.css").read_text()
    assert ".mgmt-table.mgmt-striped tbody tr:nth-child(even)" in css
    assert 'th[aria-sort="ascending"]' in css and 'th[aria-sort="descending"]' in css
