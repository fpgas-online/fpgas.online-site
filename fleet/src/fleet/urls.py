from django.urls import path

from . import views

urlpatterns = [
    path("", views.machine_list, name="fleet-list"),
    path("<str:serial>/", views.machine_detail, name="fleet-detail"),
]
