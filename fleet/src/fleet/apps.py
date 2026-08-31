import os

from django.apps import AppConfig


class FleetConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fleet"
    verbose_name = "fleet self-registration"
    # Explicit path: the src/ layout leaves a bare top-level `fleet/` directory
    # that namespace-package resolution finds via cwd on sys.path before the
    # editable-install finder maps `fleet` to `fleet/src/fleet` (see ttsite).
    path = os.path.dirname(os.path.abspath(__file__))
