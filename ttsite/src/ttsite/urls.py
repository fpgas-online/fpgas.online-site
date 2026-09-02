from django.contrib import admin
from django.urls import include, path

from . import views

urlpatterns = [
    path("", views.index, name="ttsite-index"),
    # single-site routes (legacy mode; they redirect when gateways are configured)
    path("board/<slug:slug>/", views.board, name="ttsite-board"),
    path("board/<slug:slug>/status.json", views.board_status, name="ttsite-board-status"),
    path("docs/", views.docs, name="ttsite-docs"),
    path("api/board/<slug:slug>/designs", views.api_designs, name="ttsite-api-designs"),
    path("api/board/<slug:slug>/designs/<str:name>/enable", views.api_enable, name="ttsite-api-enable"),
    path("api/board/<slug:slug>/bitstream", views.api_bitstream, name="ttsite-api-bitstream"),
    path("api/board/<slug:slug>/power", views.api_power, name="ttsite-api-power"),
    # site-scoped routes (gateway consumer mode, FPGAS_GATEWAYS non-empty)
    path("board/<slug:site>/<slug:slug>/", views.board, name="ttsite-board-site"),
    path("board/<slug:site>/<slug:slug>/status.json", views.board_status, name="ttsite-board-status-site"),
    path("api/board/<slug:site>/<slug:slug>/designs", views.api_designs, name="ttsite-api-designs-site"),
    path(
        "api/board/<slug:site>/<slug:slug>/designs/<str:name>/enable",
        views.api_enable,
        name="ttsite-api-enable-site",
    ),
    path("api/board/<slug:site>/<slug:slug>/bitstream", views.api_bitstream, name="ttsite-api-bitstream-site"),
    path("api/board/<slug:site>/<slug:slug>/power", views.api_power, name="ttsite-api-power-site"),
    # existing apps keep working on this host
    path("admin/", admin.site.urls),
    path("snmp/", include("snmp_switch.urls")),
    path("pistat/", include("pistat.urls")),
    path("pibup/", include("pibup.urls")),
]
