# pib/pibup/urls.py

from django.urls import path

from pibfpgas.views import home, one, tt

urlpatterns = [
    path('', home),
    path('tt.html', tt),
    # a Pi's page is named by its registered hostname: pi-sw2-p46.html (pi9.html at a flat site)
    path('<str:hostname>.html', one),
]

