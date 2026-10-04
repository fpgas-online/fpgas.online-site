import json

import pytest
from fleet.models import Machine

from fleet import consumer

DOC = {"schema": 1, "machine": {"serial": "abc"},
       "connection": {"site": "welland", "hostname": "pi-sw2-p9"}}


@pytest.mark.django_db
def test_dispatch_routes_registration_status_event():
    t = "fpgas/welland/pi/abc/"
    assert consumer.dispatch(t + "registration", json.dumps(DOC).encode()) \
        == "registration"
    assert Machine.objects.get(serial="abc").hostname == "pi-sw2-p9"
    assert consumer.dispatch(t + "status",
                             b'{"online": true, "boot_id": "b", "uptime_s": 5}') \
        == "status"
    assert Machine.objects.get(serial="abc").online is True
    assert consumer.dispatch(t + "event",
                             b'{"stage": "ssh-up", "boot_id": "b"}') == "event"


@pytest.mark.django_db
def test_dispatch_ignores_foreign_topics_and_garbage():
    assert consumer.dispatch("sensors/tweed/cpu_temp", b"41.2") == "ignored"
    assert consumer.dispatch("fpgas/welland/pi/abc/registration", b"{nope") \
        == "ignored"


# transaction=True: dispatch runs on a sync_to_async worker thread whose own
# DB connection must see (and write) real committed rows, not the test
# transaction private to the main thread's connection
@pytest.mark.django_db(transaction=True)
def test_events_bridge_into_the_board_page_channel_group():
    # the board pages (dcws.js) subscribe to pistat_<hostname>; the bridge
    # keeps their status log working after the legacy curls are retired
    import asyncio

    from asgiref.sync import sync_to_async
    from channels.layers import get_channel_layer

    t = "fpgas/welland/pi/abc/"
    consumer.dispatch(t + "registration", json.dumps(DOC).encode())

    async def listen_and_fire():
        layer = get_channel_layer()
        ch = await layer.new_channel()
        await layer.group_add("pistat_pi-sw2-p9", ch)   # DOC's hostname
        # dispatch is sync (ORM); run it off-loop as any real caller would
        await sync_to_async(consumer.dispatch)(
            t + "event", b'{"stage": "ssh-up", "boot_id": "b"}')
        return await asyncio.wait_for(layer.receive(ch), timeout=1)

    msg = asyncio.run(listen_and_fire())
    assert msg["type"] == "stat.message" and msg["status"] == "ssh-up"
    assert msg["message"].startswith("piview: ")


@pytest.mark.parametrize("hostname, group", [
    ("pi-sw2-p9", "pistat_pi-sw2-p9"),
    ("pi-sw1-p9", "pistat_pi-sw1-p9"),  # not the same group as switch 2's port 9
    ("pi-sw2-p9.welland.fpgas.online", "pistat_pi-sw2-p9"),
    ("pi9", "pistat_pi9"),
    ("pi-sw2-p09", None),  # not the one spelling of that port
    ("tweed", None),
    ("", None),
    (None, None),
])
def test_widget_group_is_the_short_hostname(hostname, group):
    assert consumer._widget_group(hostname) == group
