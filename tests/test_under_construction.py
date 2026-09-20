"""The under-construction banner injected by UnderConstructionMiddleware.

Welland (tweed) is the in-development site; PS1 is the stable public one, so
the banner is gated on a per-deployment setting rather than on a hostname --
both welland.fpgas.online and tinytapeout.fpgas.online are served by the same
Django process, and ps1.fpgas.online is served by the same *code*.
"""

import pytest
from django.http import HttpResponse, StreamingHttpResponse
from django.test import Client
from pibfpgas.middleware import UnderConstructionMiddleware

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


@pytest.mark.django_db
def test_admin_pages_are_left_alone(c):
    # Staff tooling, not a visitor-facing page.
    r = c.get("/admin/login/")

    assert r.status_code == 200
    assert BANNER_TEXT not in r.content.decode()


@pytest.mark.django_db
def test_iframed_pages_are_left_alone(c):
    # Browsers send Sec-Fetch-Dest: iframe for a frame load and document for a
    # top-level navigation. Only the top page should carry the banner.
    r = c.get("/fpgas/", headers={"sec-fetch-dest": "iframe"})

    assert BANNER_TEXT not in r.content.decode()


@pytest.mark.django_db
def test_top_level_navigations_get_the_banner(c):
    r = c.get("/fpgas/", headers={"sec-fetch-dest": "document"})

    assert BANNER_TEXT in r.content.decode()


@pytest.mark.django_db
def test_json_responses_are_left_alone(c):
    r = c.get("/pistat/stat/pi9/booted")

    assert r["Content-Type"] == "application/json"
    assert BANNER_TEXT not in r.content.decode()


def test_streaming_responses_are_left_alone(rf, settings):
    settings.UNDER_CONSTRUCTION = True

    def view(request):
        return StreamingHttpResponse(iter([b"<html><body>hi</body></html>"]), content_type="text/html")

    r = UnderConstructionMiddleware(view)(rf.get("/"))

    assert b"".join(r.streaming_content).decode() == "<html><body>hi</body></html>"


def test_content_length_is_corrected_when_the_response_sets_it(rf, settings):
    settings.UNDER_CONSTRUCTION = True

    def view(request):
        r = HttpResponse("<html><body>hi</body></html>", content_type="text/html")
        r["Content-Length"] = str(len(r.content))
        return r

    r = UnderConstructionMiddleware(view)(rf.get("/"))

    assert BANNER_TEXT in r.content.decode()
    assert int(r["Content-Length"]) == len(r.content)


@pytest.mark.django_db
def test_banner_removes_itself_if_it_ends_up_framed(c):
    # Belt and braces for clients that do not send Sec-Fetch-Dest at all
    # (Safari before 16.4), which the middleware has to treat as top level.
    assert "window.top !== window.self" in c.get("/fpgas/").content.decode()


@pytest.mark.django_db
def test_template_comment_does_not_reach_the_page(c):
    assert "{#" not in c.get("/fpgas/").content.decode()


# --- invariant guards (these pass against the code as written; they exist to
# --- stop a later change from quietly breaking a decision made deliberately).


@pytest.mark.django_db
def test_no_banner_when_the_setting_is_off(site_settings):
    # ps1.fpgas.online runs this same code and must never show the banner.
    site_settings.UNDER_CONSTRUCTION = False

    r = Client(HTTP_HOST="ps1.fpgas.online").get("/fpgas/")

    assert BANNER_TEXT not in r.content.decode()


@pytest.mark.django_db
def test_banner_also_covers_the_tinytapeout_host(site_settings):
    # tinytapeout.fpgas.online is a CNAME for welland.fpgas.online served by
    # this same process, so the banner is gated per deployment, not per host.
    r = Client(HTTP_HOST="tinytapeout.fpgas.online").get("/")

    assert r.status_code == 200
    assert BANNER_TEXT in r.content.decode()


@pytest.mark.django_db
def test_bare_fragments_are_left_alone(c):
    # pibup's templates are bare forms with no <html>/<body>, so there is no
    # sane place to put a banner. Accepted: they are transient upload pages.
    r = c.get("/pibup/upload?pino=9")

    assert r.status_code == 200
    assert BANNER_TEXT not in r.content.decode()
