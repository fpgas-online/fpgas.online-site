"""Every check name the dashboards show links its own section of the docs' checks page (#140; Tim, 16 Oct 2026:
"Why doesn't the check link take you to the docs for the specific check and just dumps you at a top level page?").

The page is generated in fpgas.online-test-designs (docs/verify/checks.md) and served by docs.fpgas.online; this test
reads the SERVED page, so a name the site shows with no section there fails here, loudly, rather than a reader landing
at the top of the page."""

import re
import urllib.request

from management import check_docs, check_groups


def served_ids():
    with urllib.request.urlopen(check_docs.PAGE, timeout=30) as response:  # noqa: S310 (a fixed https URL)
        assert response.status == 200
        return set(re.findall(r'id="([^"]+)"', response.read().decode()))


def shown_names():
    """Every check name the site can put in a column: the grouped names, each board type's planned checks, the
    common ones."""
    names = {name for _, members in check_groups.GROUPS for name in members}
    names |= {name for checks in check_groups.PLANNED.values() for name in checks[0]}
    return names | set(check_groups.COMMON)


def test_every_check_name_the_site_shows_has_its_section_on_the_served_page():
    ids = served_ids()
    missing = sorted(n for n in shown_names() if check_docs.anchor(n) not in ids)
    assert not missing, f"no section on {check_docs.PAGE} for: {', '.join(missing)}"
    for url in (check_docs.BOOT_CHECK, check_docs.CHECK_RAN):
        assert url.split("#")[1] in ids


def test_an_anchor_is_the_heading_as_the_docs_make_it():
    assert check_docs.anchor("p2-uart") == "p2-uart"
    assert check_docs.anchor("Boot check") == "boot-check"
    assert check_docs.url("jtag") == "https://docs.fpgas.online/en/latest/verify/checks.html#jtag"
