# pib/pistat/urls.py

# from django.conf import settings
# from django.conf.urls.static import static
from django.urls import re_path

from pistat.views import ping, status

urlpatterns = [
    re_path(r'stat/(?P<pi_name>\w+)/(?P<status>\w+)', status),
    re_path(r'ping/(?P<pi_name>\w+)', ping),
]
# + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT, show_indexes=True)

