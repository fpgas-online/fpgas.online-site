from django.conf import settings


def test_ttsite_installed_and_defaults():
    assert "ttsite" in settings.INSTALLED_APPS
    assert settings.MIDDLEWARE[0] == "ttsite.middleware.TTSiteHostMiddleware"
    assert settings.TTSITE_HOST == "tinytapeout.fpgas.online"
    assert settings.TTSITE_COMMANDER_VERSION == ""
