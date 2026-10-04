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
