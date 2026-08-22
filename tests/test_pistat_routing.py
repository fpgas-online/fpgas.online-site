import pytest
from channels.routing import URLRouter
from django.test import Client
from pistat.routing import websocket_urlpatterns


def test_ws_route_accepts_hyphenated_hostnames():
    router = URLRouter(websocket_urlpatterns)
    assert router.routes[0].pattern.match("ws/pistat/pi-sw1-p6/") is not None
    assert router.routes[0].pattern.match("ws/pistat/pi6/") is not None


@pytest.mark.django_db
def test_http_stat_accepts_hyphenated_hostnames():
    c = Client(HTTP_HOST="fpgas.online")
    r = c.post("/pistat/stat/pi-sw1-p6/cam/")
    assert r.status_code == 200
