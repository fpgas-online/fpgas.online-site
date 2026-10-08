"""The management section, mounted at /management/ on every host (pib/urls.py and ttsite/urls.py).

One line per page: management-index here; the dashboards add theirs (#67 management-switches,
#68 management-fpgas, #69 management-stats)."""

from django.urls import path

from .views import fpgas, index

urlpatterns = [
    path("", index.index, name="management-index"),
    path("fpgas/", fpgas.fpgas, name="management-fpgas"),
]
