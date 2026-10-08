"""The management section (#66): one index on every host, in that host's own look, linked from its navigation.

Tim, 9 October 2026: "The 'management section' of fpgas.online site is not private and should be accessible
without any login/password -- everything being displaying is easily and publicly available through other means."
"""

import pytest
from django.test import Client

TT = "tinytapeout.fpgas.online"


@pytest.fixture
def welland():
    return Client(HTTP_HOST="welland.fpgas.online")


@pytest.fixture
def tt():
    return Client(HTTP_HOST=TT)


@pytest.mark.django_db
@pytest.mark.parametrize("host", ["welland.fpgas.online", "ps1.fpgas.online", TT])
def test_the_index_answers_without_a_login_on_every_host(host):
    r = Client(HTTP_HOST=host).get("/management/")
    assert r.status_code == 200
    html = r.content.decode()
    assert "<h1>Management</h1>" in html and "management/management.css" in html
    assert "<title>Management &mdash; fpgas.online</title>" in html
    assert "/accounts/login" not in html and "password" not in html.lower()


@pytest.mark.django_db
def test_the_index_lists_each_dashboard_with_what_it_shows_and_where_its_data_comes_from(welland):
    html = welland.get("/management/").content.decode()
    assert "Switch ports" in html and "coming: <a href=\"https://github.com/fpgas-online/fpgas.online-site/issues/67\"" not in html
    for title, issue in (("FPGA hosts", 68), ("Visitor and usage stats", 69)):
        assert title in html
        # not built yet: named, with its issue, and no link to a page that does not exist
        assert f"coming: <a href=\"https://github.com/fpgas-online/fpgas.online-site/issues/{issue}\">#{issue}</a>" in html
    assert 'href="/management/switches/"' in html and 'href="/management/fpgas/"' not in html
    assert "SNMP on the site&#x27;s switches" in html and "The fleet registry" in html


@pytest.mark.django_db
def test_a_dashboard_is_linked_once_its_page_exists(welland, monkeypatch):
    from management.views import index

    def url(request, name):
        return "/management/fpgas/" if name == "management-fpgas" else ""

    monkeypatch.setattr(index, "_url", url)
    html = welland.get("/management/").content.decode()
    assert '<a href="/management/fpgas/">FPGA hosts</a>' in html and "coming: <a href=\"https://github.com/fpgas-online/fpgas.online-site/issues/68\"" not in html


@pytest.mark.django_db
def test_each_host_has_its_own_look(welland, tt):
    w = welland.get("/management/").content.decode()
    assert '<a href="/fleet/">fleet</a>' in w and "tt-nav" not in w  # the fleet pages' look
    assert '<a href="/fleet/">Fleet registry</a>' in w
    t = tt.get("/management/").content.decode()
    assert 'class="tt-nav"' in t and "Tiny Tapeout" in t  # tinytapeout's own look
    # no fleet pages on this host: the registry is not linked from here
    assert "Fleet registry" not in t


@pytest.mark.django_db
def test_management_is_in_each_hosts_navigation(welland, tt, settings):
    settings.DOMAIN_NAME = "welland.fpgas.online"
    page = tt.get("/").content.decode()
    assert '<a href="/management/" >Management</a>' in page
    here = tt.get("/management/").content.decode()
    assert '<a href="/management/" class="is-active" aria-current="page">Management</a>' in here
    assert '<a href="/management/">management</a>' in welland.get("/fleet/").content.decode()
    assert '<a href="/management/">Management</a>' in welland.get("/fpgas/").content.decode()


def test_every_management_page_extends_one_base_that_owns_the_header():
    """The dashboards (#67, #68, #69) fill `title`, `extra_head` and `body`; the base keeps the header."""
    import pathlib

    from django.apps import apps

    base = (pathlib.Path(apps.get_app_config("management").path) / "templates/management/base.html").read_text()
    assert base.startswith("{% extends management_theme %}")
    for block in ("title", "extra_head", "crumb", "body"):
        assert "{% block " + block + " %}" in base
    assert '<nav class="mgmt-crumbs"' in base
