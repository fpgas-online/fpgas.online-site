from django.contrib import admin
from django.urls import include, path

from . import views

urlpatterns = [
    path("", views.index, name="ttsite-index"),
    path("board/<slug:slug>/", views.board, name="ttsite-board"),
    path("board/<slug:slug>/status.json", views.board_status, name="ttsite-board-status"),
    path("docs/", views.docs, name="ttsite-docs"),
    path("api/board/<slug:slug>/designs", views.api_designs, name="ttsite-api-designs"),
    path("api/board/<slug:slug>/designs/<str:name>/enable", views.api_enable, name="ttsite-api-enable"),
    path("api/board/<slug:slug>/bitstream", views.api_bitstream, name="ttsite-api-bitstream"),
    # existing apps keep working on this host
    path("admin/", admin.site.urls),
    path("snmp/", include("snmp_switch.urls")),
    path("pistat/", include("pistat.urls")),
    path("pibup/", include("pibup.urls")),
]
