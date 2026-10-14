import json

import pytest
from fleet.models import GatewayRecord, Machine

from fleet import consumer, services

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
    assert consumer.dispatch("sensors/gateway/cpu_temp", b"41.2") == "ignored"
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
    ("gateway", None),
    ("", None),
    (None, None),
])
def test_widget_group_is_the_short_hostname(hostname, group):
    assert consumer._widget_group(hostname) == group


# --- the gateway's records: fpgas/<site>/gw/port and fpgas/<site>/gw/lease ---

PORT = {"schema": 1, "switch": 2, "port": 9, "hostname": "pi-sw2-p9", "link": "down", "at": "2026-10-14T01:02:03+00:00"}
LEASE = {"schema": 1, "switch": 2, "port": 9, "hostname": "pi-sw2-p9", "at": "2026-10-14T01:02:04+00:00",
         "event": "old", "family": 4, "address": "10.2.9.2", "mac": "88:A2:9E:45:85:77", "server": "10.2.9.1",
         "lifetime_s": 600, "acked_at": 1791939724, "expires": 1791940324, "interface": "v2109"}
GW = "fpgas/welland/gw/"


def send(kind, doc):
    return consumer.dispatch(GW + kind, json.dumps(doc).encode())


@pytest.mark.django_db
def test_gateway_records_are_stored_and_matched_by_mac_then_hostname():
    by_mac = {**DOC, "machine": {"serial": "abc", "macs": {"eth0": "88:a2:9e:45:85:77"}},
              "connection": {"site": "welland", "hostname": "pi-sw3-p3"}}
    consumer.dispatch("fpgas/welland/pi/abc/registration", json.dumps(by_mac).encode())
    consumer.dispatch("fpgas/welland/pi/def/registration", json.dumps(
        {**DOC, "machine": {"serial": "def"}}).encode())
    assert send("lease", LEASE) == "gw/lease"
    lease = GatewayRecord.objects.get(kind="lease")
    # the MAC wins over the hostname (pi-sw2-p9 is "def"'s)
    assert lease.machine.serial == "abc" and lease.mac == "88:A2:9E:45:85:77"
    assert (lease.site, lease.switch, lease.port, lease.hostname) == ("welland", 2, 9, "pi-sw2-p9")
    assert lease.doc == LEASE and lease.at.isoformat() == "2026-10-14T01:02:04+00:00"
    assert send("port", PORT) == "gw/port"
    port = GatewayRecord.objects.get(kind="port")
    assert port.machine.serial == "def" and port.mac is None and port.doc == PORT


@pytest.mark.django_db
def test_gateway_record_of_an_unknown_machine_is_kept_unmatched():
    assert send("port", PORT) == "gw/port"
    assert GatewayRecord.objects.get().machine is None


@pytest.mark.django_db
def test_latest_gateway_lease_is_the_newest():
    consumer.dispatch("fpgas/welland/pi/abc/registration", json.dumps(DOC).encode())
    machine = Machine.objects.get(serial="abc")
    assert services.latest_gateway_lease(machine) is None
    send("lease", {**LEASE, "at": "2026-10-14T01:00:00+00:00", "event": "add"})
    send("lease", {**LEASE, "at": "2026-10-14T01:05:00+00:00", "event": "old"})
    send("lease", {**LEASE, "at": "2026-10-14T01:03:00+00:00", "event": "old"})
    send("port", PORT)  # not a lease
    assert services.latest_gateway_lease(machine).doc["at"] == "2026-10-14T01:05:00+00:00"


@pytest.mark.django_db
@pytest.mark.parametrize("kind, doc", [
    ("port", {**PORT, "schema": 2}),
    ("port", {**PORT, "schema": True}),
    ("port", {**PORT, "link": "sideways"}),
    ("port", {**PORT, "port": "9"}),
    ("port", {**PORT, "switch": None}),
    ("port", {**PORT, "hostname": None}),
    ("port", {**PORT, "hostname": "pi9"}),
    ("port", {**PORT, "hostname": "pi-sw2-p9; drop"}),
    ("port", {**PORT, "at": "yesterday"}),
    ("port", {**PORT, "at": "2026-10-14T01:02:03"}),
    ("port", {k: v for k, v in PORT.items() if k != "at"}),
    ("lease", {**LEASE, "event": "renewed"}),
    ("lease", {**LEASE, "family": 5}),
    ("lease", {**LEASE, "family": "4"}),
    ("lease", {**LEASE, "address": 4}),
    ("lease", {**LEASE, "mac": 4}),
    ("lease", {**LEASE, "lifetime_s": "600"}),
    ("lease", {**LEASE, "cause": "boredom"}),
    ("lease", {**LEASE, "hostname": "gateway"}),
])
def test_invalid_gateway_records_are_ignored(kind, doc):
    assert send(kind, doc) == "ignored"
    assert GatewayRecord.objects.count() == 0


@pytest.mark.django_db
def test_gateway_lease_may_have_no_port_and_a_cause():
    doc = {**LEASE, "switch": None, "port": None, "hostname": None, "cause": "power-cycle"}
    assert send("lease", doc) == "gw/lease"
    assert GatewayRecord.objects.get().hostname == ""


@pytest.mark.django_db
@pytest.mark.parametrize("topic, payload", [
    (GW + "port", b"{nope"),
    (GW + "port", b"[1]"),
    (GW + "other", json.dumps(PORT).encode()),
    ("fpgas/welland/gw/port/extra", json.dumps(PORT).encode()),
    ("fpgas/welland/gwx/port", json.dumps(PORT).encode()),
    ("other/welland/gw/port", json.dumps(PORT).encode()),
])
def test_gateway_garbage_and_foreign_topics_are_ignored(topic, payload):
    assert consumer.dispatch(topic, payload) == "ignored"
    assert GatewayRecord.objects.count() == 0
