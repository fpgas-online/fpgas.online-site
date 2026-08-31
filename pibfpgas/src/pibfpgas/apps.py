import os

from django.apps import AppConfig


class PibfpgasConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pibfpgas"
    verbose_name = "FPGA board pages"
    # Explicit path: the src/ layout leaves a bare top-level `pibfpgas/`
    # directory that Python's namespace-package resolution finds via cwd on
    # sys.path before the editable-install finder maps `pibfpgas` to
    # `pibfpgas/src/pibfpgas`. Without this, AppConfig.path resolves to the
    # top-level dir and template/fixture discovery silently finds nothing.
    path = os.path.dirname(os.path.abspath(__file__))
