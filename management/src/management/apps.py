import os

from django.apps import AppConfig


class ManagementConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "management"
    verbose_name = "management section"
    # Explicit path: the src/ layout leaves a bare top-level `management/` directory that namespace-package
    # resolution finds via cwd on sys.path before the editable-install finder maps it (see fleet, ttsite).
    path = os.path.dirname(os.path.abspath(__file__))
