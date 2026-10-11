"""pib URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path
from django.views.generic.base import RedirectView
from management.views.fpgas import pis
from management.views.machine import machine

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', view=RedirectView.as_view(url="/fpgas/")),
    path('fpgas/', include('pibfpgas.urls')),
    path('snmp/', include('snmp_switch.urls')),
    path('pistat/', include('pistat.urls')),
    path('pibup/', include('pibup.urls')),
    # the Raspberry Pi dashboard is the fleet's front page (#94)
    path('fleet/', pis, name='fleet-list'),
    # each host's own page (#93), management's as the dashboard is; the fleet app keeps the label input download
    path('fleet/<str:serial>/', machine, name='fleet-detail'),
    path('fleet/', include('fleet.urls')),
    path('management/', include('management.urls')),
]
