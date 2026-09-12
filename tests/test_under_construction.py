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
