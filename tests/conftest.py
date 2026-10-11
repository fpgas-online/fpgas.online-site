import pytest
from django.core.cache import cache, caches


@pytest.fixture(autouse=True)
def _in_memory_channel_layer(settings):
    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    settings.ALLOWED_HOSTS = ["*"]
    settings.SECRET_KEY = "test-not-secret"


@pytest.fixture(autouse=True)
def _in_memory_poe_rate_limit(settings):
    """The PoE rate limit's cache is redis in production (pib/settings.py);
    the tests run in one process with no redis, so local memory holds it."""
    settings.CACHES = {**settings.CACHES, "poe": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                                                  "LOCATION": "poe"}}


@pytest.fixture(autouse=True)
def _clear_cache(_in_memory_poe_rate_limit):
    cache.clear()
    caches["poe"].clear()
    yield
    cache.clear()
    caches["poe"].clear()


@pytest.fixture(autouse=True)
def _no_github(monkeypatch):
    """The management index asks the public GitHub API for the newest browser test; no test reaches it. A test that
    wants an answer replaces `management.e2e.requests.get`."""
    import requests

    def blocked(*args, **kwargs):
        raise requests.ConnectionError("tests do not reach the network")

    monkeypatch.setattr("management.e2e.requests.get", blocked)
