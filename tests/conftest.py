import importlib.util
import json

import pytest
from django.core.cache import cache

HAVE_LABEL_INPUT = importlib.util.find_spec("rpi_hwid.label_input") is not None


@pytest.fixture(autouse=True)
def _in_memory_channel_layer(settings):
    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    settings.ALLOWED_HOSTS = ["*"]
    settings.SECRET_KEY = "test-not-secret"


@pytest.fixture(autouse=True)
def _label_input_until_released(monkeypatch):
    """Until the rpi-hwid release with rpi_hwid.label_input is on PyPI (and
    pinned in pyproject.toml), stand in for its dumps and missing so the
    fleet pages can be tested; with that release this does nothing. Remove
    it once the pin is in."""
    if HAVE_LABEL_INPUT:
        return
    from fleet import hwid
    monkeypatch.setattr(hwid, "dumps", lambda doc: json.dumps(doc, sort_keys=True, indent=1) + "\n")
    monkeypatch.setattr(hwid, "missing", lambda doc: {} if "power_class" in doc["summary"]
                        else {"rpi": ["power_class"]})


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()
