import os

from django.apps import AppConfig


class TtsiteConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ttsite"
    verbose_name = "tinytapeout.fpgas.online"
    # Explicit path: the src/ layout leaves a bare top-level `ttsite/` directory
    # (containing only `src/`) that Python's namespace-package resolution finds
    # via cwd on sys.path before the editable-install finder maps `ttsite` to
    # `ttsite/src/ttsite`. Without this, AppConfig.path resolves to the empty
    # top-level dir and management command discovery silently finds nothing.
    path = os.path.dirname(os.path.abspath(__file__))
