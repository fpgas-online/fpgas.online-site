import contextlib
import importlib
import io
import logging
import os
import subprocess
import sys
import types

import pytest
from django.conf import settings
from django.test import Client, override_settings
from django.urls import path
from django.utils.log import RequireDebugTrue


def test_ttsite_installed_and_defaults():
    assert "ttsite" in settings.INSTALLED_APPS
    assert settings.MIDDLEWARE[0] == "ttsite.middleware.TTSiteHostMiddleware"
    assert settings.TTSITE_HOST == "tinytapeout.fpgas.online"
    assert settings.TTSITE_COMMANDER_VERSION == ""


# ---------------------------------------------------------------------------
# Logging
#
# Regression cover for a real production failure: with DEBUG False and no
# ADMINS, Django's stock config routes django.request errors to mail_admins
# alone, so 298 HTTP 500s on tweed left no traceback anywhere in the journal.
# These tests assert on the handlers settings.LOGGING actually installs, not
# on the shape of the dict -- a dict-shape assertion would have passed against
# the broken configuration too.
# ---------------------------------------------------------------------------

APP_LOGGER_NAMES = ["pibfpgas", "pistat", "pibdemos", "pibup", "ttsite", "snmp_switch"]


def _boom(request):
    raise RuntimeError("kaboom-sentinel")


# ROOT_URLCONF for the 500 tests below (this module is importable as
# tests.test_settings_import). The default test host is "testserver", so
# TTSiteHostMiddleware leaves request.urlconf alone and these routes resolve.
urlpatterns = [path("boom/", _boom)]


def _reachable_handlers(logger_name):
    """Every handler a record logged on `logger_name` actually reaches."""
    logger = logging.getLogger(logger_name)
    handlers = []
    while logger:
        handlers.extend(logger.handlers)
        if not logger.propagate:
            break
        logger = logger.parent
    return handlers


@contextlib.contextmanager
def _capture(logger_name):
    """Redirect the configured handlers' streams into a buffer.

    Deliberately not assertLogs(): that installs a handler of its own and so
    passes even when the project configures no handler at all -- which is the
    exact bug being fixed here.
    """
    buffer = io.StringIO()
    # `type(h) is StreamHandler` on purpose: pytest's own caplog handler is a
    # StreamHandler *subclass* attached to the root logger, and capturing
    # through it would make these tests pass against a project that configures
    # no handlers at all.
    saved = [(h, h.stream) for h in _reachable_handlers(logger_name) if type(h) is logging.StreamHandler]
    for handler, _ in saved:
        handler.stream = buffer
    try:
        yield buffer
    finally:
        for handler, stream in saved:
            handler.stream = stream


@override_settings(DEBUG=False, ROOT_URLCONF=__name__)
def test_view_exception_is_logged_when_debug_is_false():
    with _capture("django.request") as buffer:
        response = Client(raise_request_exception=False).get("/boom/")
    assert response.status_code == 500
    output = buffer.getvalue()
    assert "Internal Server Error: /boom/" in output
    assert "Traceback (most recent call last)" in output
    assert "RuntimeError: kaboom-sentinel" in output


@override_settings(DEBUG=False, ROOT_URLCONF=__name__)
def test_error_log_does_not_leak_the_query_string():
    # PI_PW travels in query strings on /wssh/ URLs; a 500 must not echo one.
    with _capture("django.request") as buffer:
        Client(raise_request_exception=False).get("/boom/", {"pw": "sup3r-secret-board-pw"})
    output = buffer.getvalue()
    assert "Internal Server Error: /boom/" in output
    assert "sup3r-secret-board-pw" not in output


def test_error_handlers_are_not_gated_on_debug():
    # require_debug_true on the console handler is why production logged nothing.
    for handler in _reachable_handlers("django.request"):
        assert not any(isinstance(f, RequireDebugTrue) for f in handler.filters)


def test_no_handler_mails_admins():
    # ADMINS is unset, so AdminEmailHandler is a black hole; it also mails
    # scrubbed-but-still-sensitive request detail off the box.
    for handler in settings.LOGGING["handlers"].values():
        assert "AdminEmailHandler" not in handler["class"]


@pytest.mark.parametrize("app", APP_LOGGER_NAMES)
def test_project_app_loggers_reach_a_handler(app):
    # Mirrors logging.getLogger(__name__).exception(...) inside a view.
    name = f"{app}.views"
    with _capture(name) as buffer:
        try:
            raise ValueError("app-sentinel")
        except ValueError:
            logging.getLogger(name).exception("upload failed")
    output = buffer.getvalue()
    assert "upload failed" in output
    assert "ValueError: app-sentinel" in output


@pytest.mark.parametrize("app", APP_LOGGER_NAMES)
def test_project_app_loggers_emit_info(app):
    with _capture(app) as buffer:
        logging.getLogger(app).info("routine-sentinel")
    assert "routine-sentinel" in buffer.getvalue()


@pytest.mark.parametrize("name", ["daphne", "channels"])
def test_asgi_server_loggers_reach_a_handler(name):
    with _capture(name) as buffer:
        logging.getLogger(name).error("asgi-sentinel")
    assert "asgi-sentinel" in buffer.getvalue()


@pytest.mark.parametrize("name", ["django.server", "django.channels.server"])
def test_access_lines_are_not_duplicated(name):
    # gunicorn --access-logfile - already writes one line per request.
    with _capture(name) as buffer:
        logging.getLogger(name).info('"GET /boom/ HTTP/1.1" 500 0')
    assert buffer.getvalue() == ""


_SUBPROCESS = """
import logging, os, sys
os.environ["DJANGO_SETTINGS_MODULE"] = "pib.settings"
import django
django.setup()
from django.conf import settings
# Production runs DEBUG False (local_settings.py sets it). RequireDebugTrue
# reads settings.DEBUG per record, so flipping it here reproduces tweed.
settings.DEBUG = False
try:
    raise RuntimeError("kaboom-subprocess")
except RuntimeError:
    logging.getLogger("django.request").error("Internal Server Error: /boom/", exc_info=sys.exc_info())
"""


def test_traceback_goes_to_stderr_not_stdout():
    """A real process, as gunicorn/uvicorn and daphne are: stderr gets the traceback.

    pytest's own capture cannot show which stream the handler opened, so run it
    for real. stdout must stay clean -- gunicorn's access log owns stdout, and a
    multi-line traceback interleaved into it is unreadable.
    """
    proc = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS],
        cwd=str(settings.BASE_DIR),
        capture_output=True,
        text=True,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "pib.settings"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "Internal Server Error: /boom/" in proc.stderr
    assert "Traceback (most recent call last)" in proc.stderr
    assert "RuntimeError: kaboom-subprocess" in proc.stderr
    assert proc.stdout == ""


@pytest.fixture
def reload_settings(monkeypatch):
    """Re-import pib.settings with a synthetic pib/local_settings.py.

    django.conf.settings copied the module's attributes at setup, so reloading
    the module does not disturb the running configuration.
    """

    def _reload(**local_settings_attrs):
        fake = types.ModuleType("pib.local_settings")
        for key, value in local_settings_attrs.items():
            setattr(fake, key, value)
        monkeypatch.setitem(sys.modules, "pib.local_settings", fake)
        return importlib.reload(importlib.import_module("pib.settings"))

    yield _reload
    # monkeypatch has already removed the synthetic module; this restores the
    # real module state for every later test.
    importlib.reload(importlib.import_module("pib.settings"))


def test_log_level_defaults_to_info(reload_settings, monkeypatch):
    monkeypatch.delenv("DJANGO_LOG_LEVEL", raising=False)
    module = reload_settings()
    assert module.LOGGING["loggers"]["pibup"]["level"] == "INFO"
    assert module.LOGGING["loggers"]["django.request"]["level"] == "ERROR"


def test_local_settings_can_change_log_level(reload_settings):
    module = reload_settings(LOG_LEVEL="DEBUG", REQUEST_LOG_LEVEL="WARNING")
    assert module.LOGGING["loggers"]["pibup"]["level"] == "DEBUG"
    assert module.LOGGING["loggers"]["django"]["level"] == "DEBUG"
    assert module.LOGGING["loggers"]["django.request"]["level"] == "WARNING"


def test_env_var_can_change_log_level(reload_settings, monkeypatch):
    monkeypatch.setenv("DJANGO_LOG_LEVEL", "debug")
    module = reload_settings()
    assert module.LOGGING["loggers"]["pibup"]["level"] == "DEBUG"


def test_local_settings_beats_the_env_var(reload_settings, monkeypatch):
    monkeypatch.setenv("DJANGO_LOG_LEVEL", "WARNING")
    module = reload_settings(LOG_LEVEL="DEBUG")
    assert module.LOGGING["loggers"]["pibup"]["level"] == "DEBUG"


def test_local_settings_can_replace_logging_wholesale(reload_settings):
    custom = {"version": 1, "disable_existing_loggers": False, "handlers": {}, "root": {}}
    module = reload_settings(LOGGING=custom)
    assert module.LOGGING is custom


def test_existing_server_loggers_are_not_disabled():
    # gunicorn and daphne create their loggers before Django reads settings;
    # disable_existing_loggers True would silence the server itself.
    assert settings.LOGGING["disable_existing_loggers"] is False
