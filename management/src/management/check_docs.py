"""Where each check's own documentation is (Tim, 16 Oct 2026, site #140: "Why doesn't the check link take you to the
docs for the specific check and just dumps you at a top level page?").

fpgas-verify's board modules generate one page with a section per check (fpgas.online-test-designs
docs/verify/checks.md, scripts/gen_checks_doc.py); the docs site imports it and gives each section an anchor from its
heading: the check's name as the dashboards show it, and "Boot check" and "Check ran" for the two columns beside the
checks. tests/test_management_check_docs.py fetches the served page and fails when a name the site shows has none."""

import re

PAGE = "https://docs.fpgas.online/en/latest/verify/checks.html"
BOOT_CHECK = f"{PAGE}#boot-check"
CHECK_RAN = f"{PAGE}#check-ran"


def anchor(name):
    """The section id the docs give a heading (MyST's heading anchors): lower case, runs of anything but letters,
    digits and hyphens as one hyphen."""
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")


def url(name):
    """The documentation of check `name`."""
    return f"{PAGE}#{anchor(name)}"
