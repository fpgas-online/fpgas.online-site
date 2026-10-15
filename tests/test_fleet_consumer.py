import json

import pytest
from django.utils import timezone
from django.utils.dateparse import parse_datetime
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

# Golden records: copied by hand from what the real producers print. PORT is fpgas.online-poe's Change.record()
# (poller.py); LEASE is fpgas.online-infra's fpgas-lease-event record() for an "old" renewal on v2209 (switch 2,
# port 9); DEL_LEASE the same script's "del"; RELEASED_V6 and RELEASED_V4 are fpgas-release-port-lease's
# lease_record() (a v6 lease has no MAC; the release carries its cause).
PORT = {"schema": 1, "switch": 2, "port": 9, "hostname": "pi-sw2-p9", "link": "down", "at": "2026-10-14T01:02:03+00:00"}
LEASE = {"schema": 1, "switch": 2, "port": 9, "hostname": "pi-sw2-p9", "at": "2026-10-14T01:02:04+00:00",
         "event": "old", "family": 4, "address": "10.2.9.2", "mac": "88:a2:9e:45:85:77", "server": None,
         "lifetime_s": 300, "acked_at": "2026-10-14T01:02:04+00:00", "expires": 1791940024, "interface": "v2209"}
DEL_LEASE = {"schema": 1, "switch": 2, "port": 9, "hostname": "pi-sw2-p9", "at": "2026-10-14T01:02:04+00:00",
             "event": "del", "family": 4, "address": "10.2.9.2", "mac": "88:a2:9e:45:85:77", "server": None,
             "lifetime_s": None, "acked_at": None, "expires": 1791940024, "interface": "v2209"}
RELEASED_V6 = {"schema": 1, "switch": 2, "port": 9, "hostname": "pi-sw2-p9", "at": "2026-10-14T01:02:05+00:00",
               "event": "released", "family": 6, "address": "fd00::9", "mac": None, "server": None,
               "lifetime_s": None, "acked_at": None, "expires": 1791940024, "interface": "v2209",
               "cause": "link-down"}
RELEASED_V4 = {**RELEASED_V6, "family": 4, "address": "10.2.9.2", "mac": "88:a2:9e:45:85:77",
               "cause": "power-cycle"}
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
    assert lease.machine.serial == "abc" and lease.mac == "88:a2:9e:45:85:77"
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
def test_the_producers_golden_records_are_all_accepted():
    consumer.dispatch("fpgas/welland/pi/abc/registration", json.dumps(
        {**DOC, "machine": {"serial": "abc", "macs": {"eth0": "88:A2:9E:45:85:77"}}}).encode())
    for kind, doc in (("port", PORT), ("lease", LEASE), ("lease", DEL_LEASE), ("lease", RELEASED_V6),
                      ("lease", RELEASED_V4)):
        assert send(kind, doc) == f"gw/{kind}"
    stored = {(r.kind, r.doc.get("event"), r.doc["family"] if r.kind == "lease" else None): r
              for r in GatewayRecord.objects.all()}
    assert len(stored) == 5 and all(r.doc in (PORT, LEASE, DEL_LEASE, RELEASED_V6, RELEASED_V4) for r in stored.values())
    # a v4 record matches by its MAC; the v6 release has none and falls back to the port's hostname (abc's)
    assert stored[("lease", "old", 4)].machine.serial == "abc"
    assert stored[("lease", "released", 6)].machine.serial == "abc" and stored[("lease", "released", 6)].mac is None


@pytest.mark.django_db
def test_a_stranger_mac_is_not_credited_to_the_pi_on_the_port():
    """A visitor's laptop answered by the gateway on a board's port: the record has a MAC no machine has, so it is
    stored unmatched, not given to the Pi registered on that port by hostname."""
    consumer.dispatch("fpgas/welland/pi/def/registration", json.dumps(
        {**DOC, "machine": {"serial": "def", "macs": {"eth0": "88:a2:9e:00:00:01"}}}).encode())
    send("lease", {**LEASE, "mac": "aa:bb:cc:dd:ee:ff"})
    assert GatewayRecord.objects.get().machine is None
    assert services.latest_gateway_ack(Machine.objects.get(serial="def")) is None


@pytest.mark.django_db
def test_a_record_without_a_mac_falls_back_to_the_hostname():
    consumer.dispatch("fpgas/welland/pi/def/registration", json.dumps(
        {**DOC, "machine": {"serial": "def"}}).encode())
    send("lease", RELEASED_V6)
    assert GatewayRecord.objects.get().machine.serial == "def"


@pytest.mark.django_db
def test_latest_gateway_lease_is_the_newest():
    consumer.dispatch("fpgas/welland/pi/abc/registration", json.dumps(
        {**DOC, "machine": {"serial": "abc", "macs": {"eth0": LEASE["mac"]}}}).encode())
    machine = Machine.objects.get(serial="abc")
    assert services.latest_gateway_lease(machine) is None
    send("lease", {**LEASE, "at": "2026-10-14T01:00:00+00:00", "event": "add"})
    send("lease", {**LEASE, "at": "2026-10-14T01:05:00+00:00", "event": "old"})
    send("lease", {**LEASE, "at": "2026-10-14T01:03:00+00:00", "event": "old"})
    send("port", PORT)  # not a lease
    assert services.latest_gateway_lease(machine).doc["at"] == "2026-10-14T01:05:00+00:00"


@pytest.mark.django_db
def test_latest_gateway_ack_skips_records_that_are_not_an_ack():
    consumer.dispatch("fpgas/welland/pi/abc/registration", json.dumps(
        {**DOC, "machine": {"serial": "abc", "macs": {"eth0": LEASE["mac"]}}}).encode())
    machine = Machine.objects.get(serial="abc")
    assert services.latest_gateway_ack(machine) is None
    send("lease", {**LEASE, "at": "2026-10-14T01:00:00+00:00", "event": "old", "acked_at": "2026-10-14T01:00:00+00:00"})
    send("lease", {**LEASE, "at": "2026-10-14T01:02:00+00:00", "event": "del", "acked_at": None})
    assert services.latest_gateway_ack(machine).doc["acked_at"] == "2026-10-14T01:00:00+00:00"
    send("lease", {**LEASE, "at": "2026-10-14T01:04:00+00:00", "event": "add", "acked_at": "2026-10-14T01:04:00+00:00"})
    assert services.latest_gateway_ack(machine).doc["acked_at"] == "2026-10-14T01:04:00+00:00"


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
    ("port", {**PORT, "hostname": "pi-sw2-p8"}),      # the hostname of another port than the record's own
    ("port", {**PORT, "hostname": "pi-sw3-p9"}),
    ("lease", {**LEASE, "hostname": "pi-sw1-p9"}),
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
    ("lease", {**LEASE, "acked_at": 1791939724}),  # an epoch: the registry reads ISO 8601 only
    ("lease", {**LEASE, "acked_at": 1791939724.5}),
    ("lease", {**LEASE, "acked_at": "2026-10-14T01:02:04"}),  # no offset
    ("lease", {**LEASE, "acked_at": "soon"}),
    ("lease", {**LEASE, "acked_at": True}),
    ("lease", {**LEASE, "expires": "1791940024"}),
    ("lease", {**LEASE, "expires": -1}),
    ("lease", {**LEASE, "expires": True}),
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


@pytest.mark.django_db(transaction=True)
def test_a_port_record_reaches_its_board_page_and_the_switch_dashboard_only():
    import asyncio

    from asgiref.sync import sync_to_async
    from channels.layers import get_channel_layer

    async def listen_and_fire():
        layer = get_channel_layer()
        page, other, dashboard = [await layer.new_channel() for _ in range(3)]
        await layer.group_add("pistat_pi-sw2-p9", page)
        await layer.group_add("pistat_pi-sw1-p9", other)  # another switch's port 9
        await layer.group_add("switch_events", dashboard)
        await sync_to_async(send)("port", PORT)
        await sync_to_async(send)("lease", LEASE)
        got = {"page": [await asyncio.wait_for(layer.receive(page), timeout=1) for _ in range(2)],
               "dashboard": await asyncio.wait_for(layer.receive(dashboard), timeout=1)}
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(layer.receive(other), timeout=0.2)
        with pytest.raises(asyncio.TimeoutError):  # only port records go to the dashboard
            await asyncio.wait_for(layer.receive(dashboard), timeout=0.2)
        return got

    got = asyncio.run(listen_and_fire())
    assert [m["message"] for m in got["page"]] == [
        "switch: link down", "dhcp: old 10.2.9.2 88:a2:9e:45:85:77"]
    assert all(m["type"] == "stat.message" for m in got["page"])
    assert got["dashboard"] == {"type": "switch.event", "record": PORT}


@pytest.mark.parametrize("doc, text", [
    ({"kind": "port", "link": "bounced"}, "switch: link bounced"),
    ({"kind": "lease", "event": "released", "address": "10.2.9.2", "mac": "aa:bb", "cause": "link-down"},
     "dhcp: released 10.2.9.2 aa:bb (link-down)"),
    ({"kind": "lease", "event": "del", "address": "fd00::9", "mac": None}, "dhcp: del fd00::9"),
])
def test_gateway_text(doc, text):
    kind = doc.pop("kind")
    assert consumer._gateway_text(GatewayRecord(kind=kind, doc=doc)) == text


@pytest.mark.django_db
def test_old_gateway_records_are_pruned_but_each_ports_newest_is_kept(settings, monkeypatch):
    from datetime import timedelta

    from django.utils import timezone

    monkeypatch.setattr(services, "_last_prune", None)
    settings.GATEWAY_RECORD_DAYS = 7
    now = timezone.now()

    def at(days):
        return (now - timedelta(days=days)).isoformat()

    send("lease", {**LEASE, "at": at(20)})                      # port 9, old, and not the newest: goes
    send("lease", {**LEASE, "at": at(10)})                      # port 9, old, but the newest... until one arrives
    send("lease", {**LEASE, "port": 8, "hostname": "pi-sw2-p8", "at": at(30)})   # port 8's only record: kept
    send("port", {**PORT, "at": at(10)})                        # another kind of port 9: its own newest, kept
    assert GatewayRecord.objects.count() == 4                   # the prune ran on the first store only
    monkeypatch.setattr(services, "_last_prune", None)
    send("lease", {**LEASE, "at": at(1)})                       # inside the window; runs the prune
    kept = sorted((r.kind, r.port, r.at.date()) for r in GatewayRecord.objects.all())
    assert kept == sorted([("lease", 8, (now - timedelta(days=30)).date()), ("port", 9, (now - timedelta(days=10)).date()),
                           ("lease", 9, (now - timedelta(days=1)).date())])


@pytest.mark.django_db
def test_the_prune_runs_at_most_once_per_interval(settings, monkeypatch):
    from datetime import timedelta

    from django.utils import timezone

    monkeypatch.setattr(services, "_last_prune", None)
    old = (timezone.now() - timedelta(days=30)).isoformat()
    send("lease", {**LEASE, "at": old})
    send("lease", {**LEASE, "at": old})   # within the interval: not pruned
    assert GatewayRecord.objects.count() == 2
    monkeypatch.setattr(services, "_last_prune", services._last_prune - services.PRUNE_EVERY_SECONDS)
    send("lease", {**LEASE, "at": timezone.now().isoformat()})
    assert GatewayRecord.objects.count() == 1


@pytest.mark.django_db
def test_a_failed_prune_does_not_stop_the_record_being_pushed(monkeypatch, caplog):
    def broken():
        raise RuntimeError("database is locked")

    sent = []
    monkeypatch.setattr(services, "_last_prune", None)
    monkeypatch.setattr(services, "prune_gateway_records", broken)
    monkeypatch.setattr(consumer, "_send", lambda group, message: sent.append(group))
    with caplog.at_level("ERROR"):
        assert send("port", PORT) == "gw/port"
    assert GatewayRecord.objects.count() == 1          # stored
    assert consumer.SWITCH_EVENTS_GROUP in sent        # and bridged
    assert "could not be pruned" in caplog.text


@pytest.mark.django_db
def test_every_stored_acked_at_parses_as_an_iso_time_with_an_offset():
    for acked in ("2026-10-14T01:02:04+00:00", "2026-10-14T11:32:04+10:30", None):
        assert send("lease", {**LEASE, "acked_at": acked}) == "gw/lease"
    for record in GatewayRecord.objects.filter(kind="lease"):
        acked = record.doc["acked_at"]
        assert acked is None or not timezone.is_naive(parse_datetime(acked))


@pytest.mark.django_db
def test_the_migration_drops_every_ipv6_lease_record_and_nothing_else():
    """fleet 0003 (site #115): IPv6 lease records stored before infra #293 were filed under the wrong port."""
    import importlib

    from django.apps import apps

    send("lease", {**LEASE, "family": 6, "address": "2404:e80:a137:2102::30", "mac": None,
                   "acked_at": "2026-10-14T05:45:02+00:00"})
    send("lease", LEASE)
    send("port", PORT)
    assert GatewayRecord.objects.count() == 3
    migration = importlib.import_module("fleet.migrations.0003_drop_misfiled_ipv6_lease_records")
    migration.drop_ipv6_lease_records(apps, None)
    assert sorted((r.kind, r.doc["family"] if r.kind == "lease" else None) for r in GatewayRecord.objects.all()) == [
        ("lease", 4), ("port", None)]


# --- retained messages (site #119) --------------------------------------------------------------------------------
# The Pi publishes its registration, status beat and will retained, so the broker replays each machine's last copy to
# the consumer whenever it subscribes. Such a copy updates state but is no sign the Pi spoke now.

DAY_AGO = timezone.now() - timezone.timedelta(days=1)
PI = "fpgas/welland/pi/abc/"


def _known_machine_seen_a_day_ago():
    consumer.dispatch(PI + "registration", json.dumps(DOC).encode())
    Machine.objects.filter(serial="abc").update(last_seen=DAY_AGO)


@pytest.mark.django_db
def test_a_retained_status_applies_the_state_but_leaves_last_seen():
    _known_machine_seen_a_day_ago()
    will = json.dumps({"online": False, "reason": "connection-lost"}).encode()
    assert consumer.dispatch(PI + "status", will, retained=True) == "status"
    m = Machine.objects.get(serial="abc")
    assert m.last_seen == DAY_AGO and m.online is False


@pytest.mark.django_db
def test_a_live_status_sets_last_seen():
    _known_machine_seen_a_day_ago()
    beat = json.dumps({"online": True, "boot_id": "b", "uptime_s": 5}).encode()
    assert consumer.dispatch(PI + "status", beat, retained=False) == "status"
    m = Machine.objects.get(serial="abc")
    assert m.last_seen > DAY_AGO and m.online is True and m.last_boot_id == "b"


@pytest.mark.django_db
def test_a_retained_registration_of_a_known_machine_leaves_last_seen():
    _known_machine_seen_a_day_ago()
    moved = {**DOC, "connection": {"site": "welland", "hostname": "pi-sw2-p10"}}
    assert consumer.dispatch(PI + "registration", json.dumps(moved).encode(), retained=True) == "registration"
    m = Machine.objects.get(serial="abc")
    assert m.last_seen == DAY_AGO and m.hostname == "pi-sw2-p10"


@pytest.mark.django_db
def test_a_retained_registration_of_a_new_machine_creates_it():
    before = timezone.now()
    assert consumer.dispatch(PI + "registration", json.dumps(DOC).encode(), retained=True) == "registration"
    # last_seen is required: a machine new to the site gets the time the site first heard of it
    assert before <= Machine.objects.get(serial="abc").last_seen <= timezone.now()


@pytest.mark.django_db
def test_a_retained_status_is_not_told_to_the_board_page(monkeypatch):
    _known_machine_seen_a_day_ago()
    told = []
    monkeypatch.setattr(consumer, "_bridge", lambda hostname, stage: told.append((hostname, stage)))
    consumer.dispatch(PI + "status", b'{"online": false, "reason": "connection-lost"}', retained=True)
    assert told == []
    consumer.dispatch(PI + "status", b'{"online": true, "boot_id": "b", "uptime_s": 5}', retained=False)
    assert told == [("pi-sw2-p9", "online")]


@pytest.mark.parametrize("retain", [0, 1])
def test_the_mqtt_callback_passes_the_retain_flag(monkeypatch, retain):
    from types import SimpleNamespace

    from fleet.management.commands import fleet_consumer

    seen = []
    monkeypatch.setattr(fleet_consumer.consumer, "dispatch",
                        lambda topic, payload, retained=False: seen.append((topic, payload, retained)))
    fleet_consumer.on_message(None, None, SimpleNamespace(topic=PI + "status", payload=b"{}", retain=retain))
    assert seen == [(PI + "status", b"{}", bool(retain))]


def test_the_command_wires_the_callback_and_a_real_paho_message_carries_retain(monkeypatch, settings):
    """handle() hands the module's on_message to paho, and paho's own MQTTMessage carries the flag it passes."""
    import paho.mqtt.client as mqtt
    from fleet.management.commands import fleet_consumer

    made = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            made.append(self)

        def connect(self, host, port):
            pass

        def loop_forever(self, **kwargs):
            pass

    settings.FLEET_MQTT = {"host": "127.0.0.1", "port": 1883}
    monkeypatch.setattr(fleet_consumer.mqtt, "Client", FakeClient)
    fleet_consumer.Command().handle()
    assert made[0].on_message is fleet_consumer.on_message

    seen = []
    monkeypatch.setattr(fleet_consumer.consumer, "dispatch",
                        lambda topic, payload, retained=False: seen.append(retained))
    msg = mqtt.MQTTMessage(topic=(PI + "status").encode())
    msg.payload = b"{}"
    msg.retain = True
    fleet_consumer.on_message(None, None, msg)
    assert seen == [True]
