from django.urls import path

from . import views

urlpatterns = [
    path("<str:serial>/rpi-hwid.json", views.label_input, name="fleet-label-input"),
]
