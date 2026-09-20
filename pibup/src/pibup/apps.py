import os

from django.apps import AppConfig


class PibupConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pibup"
    verbose_name = "pib upload"
    # Explicit path: same src/-layout namespace-package issue as ttsite and
    # pibfpgas -- without this, templates in pibup/src/pibup/templates are
    # invisible in the dev/test environment.
    path = os.path.dirname(os.path.abspath(__file__))
