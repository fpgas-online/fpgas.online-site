from django.urls import path

from . import views

urlpatterns = [
    path("<str:serial>/rpi-hwid.json", views.label_input, name="fleet-label-input"),
    # one label rpi-hwid makes for this Pi (?id=<its label id>), as an image and as the PDF it prints (#141)
    path("<str:serial>/label.png", views.label_png, name="fleet-label-png"),
    path("<str:serial>/label.pdf", views.label_pdf, name="fleet-label-pdf"),
]
