import pytest
from django.test import Client


@pytest.mark.django_db
def test_tt_host_gets_ttsite_urlconf():
    c = Client(HTTP_HOST="tinytapeout.fpgas.online")
    r = c.get("/")
    assert r.status_code == 200
    assert "Tiny Tapeout" in r.content.decode()


@pytest.mark.django_db
def test_tt_host_with_port():
    c = Client(HTTP_HOST="tinytapeout.fpgas.online:8000")
    assert c.get("/docs/").status_code == 200


@pytest.mark.django_db
def test_other_host_keeps_main_urlconf():
    c = Client(HTTP_HOST="fpgas.online")
    r = c.get("/", follow=False)
    assert r.status_code in (301, 302) and r["Location"] == "/fpgas/"   # main site's RedirectView
    assert c.get("/docs/").status_code == 404
