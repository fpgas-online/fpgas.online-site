import json

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


@pytest.mark.django_db(transaction=True)  # channels closes old DB connections as each event is handled
def test_switch_events_socket_forwards_a_record_and_ignores_the_browser():
    import asyncio

    from channels.layers import get_channel_layer
    from channels.testing import WebsocketCommunicator

    record = {"schema": 1, "switch": 2, "port": 9, "hostname": "pi-sw2-p9", "link": "down",
              "at": "2026-10-14T01:02:03+00:00"}

    async def run():
        socket = WebsocketCommunicator(URLRouter(websocket_urlpatterns), "/ws/switches/")
        connected, _ = await socket.connect()
        assert connected
        # what the browser sends is not relayed, to this socket or to the pistat groups
        await socket.send_to(text_data='{"message": "hello"}')
        assert await socket.receive_nothing(timeout=0.2)
        await get_channel_layer().group_send("switch_events", {"type": "switch.event", "record": record})
        assert json.loads(await socket.receive_from(timeout=1)) == record
        assert await socket.receive_nothing(timeout=0.2)
        await socket.disconnect()

    asyncio.run(run())
