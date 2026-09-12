"""The under-construction banner injected by UnderConstructionMiddleware.

Welland (tweed) is the in-development site; PS1 is the stable public one, so
the banner is gated on a per-deployment setting rather than on a hostname --
both welland.fpgas.online and tinytapeout.fpgas.online are served by the same
Django process, and ps1.fpgas.online is served by the same *code*.
"""

import pytest
from django.test import Client

BANNER_TEXT = "Under construction!"


@pytest.fixture
def site_settings(settings):
    settings.DOMAIN_NAME = "welland.fpgas.online"
    settings.PI_PW = "cGFzc3dvcmQ="
    settings.UNDER_CONSTRUCTION = True
    return settings


@pytest.fixture
def c(site_settings):
    return Client(HTTP_HOST="welland.fpgas.online")


@pytest.mark.django_db
def test_banner_shown_on_board_list(c):
    assert BANNER_TEXT in c.get("/fpgas/").content.decode()


@pytest.mark.django_db
def test_banner_offers_the_fallback_site_as_a_link(c):
    html = c.get("/fpgas/").content.decode()
    assert "Broken?" in html
    assert '<a href="https://ps1.fpgas.online/"' in html


@pytest.mark.django_db
def test_fallback_host_is_configurable(c, site_settings):
    site_settings.UNDER_CONSTRUCTION_FALLBACK = "spare.fpgas.online"
    html = c.get("/fpgas/").content.decode()
    assert '<a href="https://spare.fpgas.online/"' in html
    assert "ps1.fpgas.online" not in html


@pytest.mark.django_db
def test_banner_hidden_for_a_visitor_who_dismissed_it(c):
    c.cookies["fo_uc_dismissed"] = "1"

    assert BANNER_TEXT not in c.get("/fpgas/").content.decode()


@pytest.mark.django_db
def test_banner_carries_a_dismiss_control(c):
    assert 'id="fo-uc-dismiss"' in c.get("/fpgas/").content.decode()


@pytest.mark.django_db
def test_dismissal_lasts_one_week(c):
    # Set client-side with a max-age, so the browser drops it after a week and
    # the banner comes back on its own -- no dismissal state stored server side.
    assert "max-age=604800" in c.get("/fpgas/").content.decode()
