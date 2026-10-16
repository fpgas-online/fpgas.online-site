"""Every check name the dashboards show links its own section of the docs' checks page (#140; Tim, 16 Oct 2026:
"Why doesn't the check link take you to the docs for the specific check and just dumps you at a top level page?").

The page is generated in fpgas.online-test-designs (docs/verify/checks.md) and served by docs.fpgas.online; this test
reads the SERVED page, so a name the site shows with no section there fails here, loudly, rather than a reader landing
at the top of the page."""

import functools
import re
import urllib.request

import pytest
from django.test import Client

from management import check_docs, check_groups


@functools.cache
def served_ids():
    # Read the Docs refuses Python's default user agent (403): name this test
    request = urllib.request.Request(check_docs.PAGE, headers={"User-Agent": "fpgas.online-site tests (#140)"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 (a fixed https URL)
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



@pytest.mark.django_db
def test_every_docs_link_the_pages_emit_lands_on_a_section_of_the_served_page():
    """The links the pages actually carry, not a list: /fleet/ and the device page of every Pi in welland's 4 Oct
    reports (the review of #150: the Boot checks table's marks had no link and a name-list test could not see it)."""
    from tests.test_management_fpgas import REAL, pi

    for hostname, detail in REAL.items():
        pi(hostname, detail, serial=hostname)
    client = Client(HTTP_HOST="welland.fpgas.online")
    pages = ["/fleet/", *(f"/fleet/{hostname}/" for hostname in REAL)]
    links = set()
    for path in pages:
        html = client.get(path).content.decode()
        links |= set(re.findall(r'href="(' + re.escape(check_docs.PAGE) + r'[^"]*)"', html))
    assert links, "no page links a check's documentation"
    ids = served_ids()
    bad = sorted(link for link in links if "#" not in link or link.split("#", 1)[1] not in ids)
    assert not bad, f"links that land on no section: {bad}"


def test_a_name_the_site_does_not_know_links_nothing():
    assert check_docs.url("not-a-check") == ""
