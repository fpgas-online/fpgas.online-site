"""The consumer's per-port topic prefix: the broker's listener for a port
stamps `port/<port>/` on everything its Pi publishes, so the port in the
prefix is the one thing a Pi cannot choose. A machine is a row per (port,
serial), so what a Pi says about a serial writes only its own port's row.
Messages are driven through consumer.dispatch, and what the /fpgas/ pages
offer is read back through pibfpgas.pis.offered_pi."""

import json
import logging

import pytest
from django.test import Client
from fleet.models import BootEvent, Machine
from pibfpgas.pis import offered_pi

from fleet import consumer

SITE = "fpgas/welland/pi/"
A, B, C = "pi-sw2-p46", "pi-sw2-p47", "pi-sw2-p48"


@pytest.fixture(autouse=True)
def prefix_on(settings):
    settings.FLEET_MQTT = {**settings.FLEET_MQTT, "port_prefix": True}


@pytest.fixture
def c(settings):
    settings.DOMAIN_NAME = "welland.fpgas.online"
    return Client(HTTP_HOST="welland.fpgas.online")


def send(serial, kind, doc, port=None):
    """Publish one message as the broker would deliver it, stamped with the
    port the Pi is on when `port` is given. Returns what dispatch did."""
    topic = f"{SITE}{serial}/{kind}"
    if port is not None:
        topic = f"port/{port}/{topic}"
    return consumer.dispatch(topic, json.dumps(doc).encode())


def register(serial, hostname, port=None, **machine):
    return send(serial, "registration",
                {"schema": 1, "machine": {"serial": serial, **machine},
                 "connection": {"site": "welland", "hostname": hostname}}, port)


def beat(serial, port=None, boot_id="b1", online=True):
    return send(serial, "status", {"online": online, "boot_id": boot_id}, port)


def verified(serial, result, port=None, boot_id="b1"):
    return send(serial, "event", {
        "stage": "fpga-verified", "boot_id": boot_id,
        "detail": {"result": result, "mode": "auto"}}, port)


def found(serial, board, port=None, boot_id="b1"):
    return send(serial, "event", {
        "stage": "fpga-board-found", "boot_id": boot_id,
        "detail": {"board": board, "variant": "-", "where": "1-1.2"}}, port)


def honest_board(serial, port, board="acorn", boot_id="b1"):
    """A Pi on `port` (short hostname) that registers, beats, finds a board
    and passes its check, all through its own port's listener."""
    assert register(serial, port, port) == "registration"
    beat(serial, port, boot_id)
    found(serial, board, port, boot_id)
    verified(serial, "pass", port, boot_id)


def row(serial, port=""):
    return Machine.objects.get(serial=serial, verified_port=port)


def listed(port):
    pi = offered_pi(port)
    return pi.boards if pi else None


# --- the setting -----------------------------------------------------------

@pytest.mark.django_db
def test_with_the_setting_off_a_stamped_topic_is_foreign(settings):
    """Today's open broker lets any Pi publish a stamped topic itself."""
    settings.FLEET_MQTT = {**settings.FLEET_MQTT, "port_prefix": False}
    assert register("serial-a", A) == "registration"
    beat("serial-a")
    found("serial-a", "acorn")
    verified("serial-a", "pass")
    before = list(Machine.objects.values()), list(BootEvent.objects.values())
    assert register("serial-a", A, A) == "ignored"
    assert register("serial-x", B, B) == "ignored"
    assert beat("serial-a", A, online=False) == "ignored"
    assert verified("serial-a", "fail", A) == "ignored"
    assert consumer.dispatch("port/not a port/" + SITE + "s/status", b"{}") == "ignored"
    assert (list(Machine.objects.values()), list(BootEvent.objects.values())) == before
    assert listed(A) == "Acorn"


@pytest.mark.django_db
def test_the_setting_defaults_to_off():
    from pib import settings as module
    assert module.FLEET_MQTT["port_prefix"] is False


class FakeClient:
    """paho's client, recording what the command does with it."""
    instances = []

    def __init__(self, *args):
        self.subscribed = []
        FakeClient.instances.append(self)

    def subscribe(self, topics):
        self.subscribed.extend(topic for topic, _qos in topics)

    def connect(self, host, port):
        pass

    def loop_forever(self, retry_first_connection):
        self.on_connect(self, None, None, 0, None)


@pytest.mark.parametrize("on, topics", [
    (False, ["fpgas/+/pi/+/+"]),
    (True, ["fpgas/+/pi/+/+", "port/+/fpgas/+/pi/+/+"]),
])
def test_the_command_subscribes_to_the_stamped_form_only_when_on(
        monkeypatch, settings, on, topics):
    from django.core.management import call_command
    from fleet.management.commands import fleet_consumer
    settings.FLEET_MQTT = {**settings.FLEET_MQTT, "port_prefix": on}
    FakeClient.instances.clear()
    monkeypatch.setattr(fleet_consumer.mqtt, "Client", FakeClient)
    call_command("fleet_consumer")
    assert FakeClient.instances[0].subscribed == topics


# --- stamped messages ------------------------------------------------------

@pytest.mark.django_db
def test_honest_prefixed_board_is_listed_and_remembers_its_port():
    honest_board("serial-a", A)
    assert listed(A) == "Acorn"
    assert row("serial-a", A).hostname == A


@pytest.mark.django_db
def test_the_payload_may_name_the_full_host_name_of_its_port():
    assert register("serial-a", A + ".welland.fpgas.online", A) == "registration"


@pytest.mark.django_db
def test_registration_naming_another_host_is_rejected_and_logged(caplog):
    honest_board("serial-a", A)
    with caplog.at_level(logging.WARNING):
        # a Pi on port B claims A's hostname, with a serial of its own
        assert register("serial-b", A, B) == "rejected"
    assert not Machine.objects.filter(serial="serial-b").exists()
    assert listed(A) == "Acorn"
    assert "'serial-b'" in caplog.text and A in caplog.text and B in caplog.text


@pytest.mark.django_db
@pytest.mark.parametrize("hostname", [None, 7, ["pi-sw2-p46"], {"a": 1}])
def test_a_hostname_that_is_not_a_string_is_a_clean_rejection(hostname):
    assert register("serial-a", hostname, A) == "rejected"
    assert not Machine.objects.exists()


@pytest.mark.django_db
def test_forged_registration_of_a_listed_serial_for_another_host_is_rejected():
    honest_board("serial-a", A)
    assert register("serial-a", A, B) == "rejected"
    assert Machine.objects.count() == 1
    assert listed(A) == "Acorn"


@pytest.mark.django_db
def test_a_serial_claimed_from_another_port_is_a_row_of_its_own(caplog):
    """The reboot window: B registers A's serial right after A's will said
    offline, or before A's first beat. Nothing of A's changes."""
    assert register("serial-a", A, A) == "registration"       # A, before its first beat
    forged = register("serial-a", B, B, model="forged")
    beat("serial-a", B)                                        # the forger wins the race
    assert forged == "registration"
    honest_board("serial-a", A)                                # A's own messages, after
    beat("serial-a", A, online=False)                          # A's will: offline
    assert Machine.objects.filter(serial="serial-a").count() == 2
    assert row("serial-a", A).online is False and row("serial-a", B).online is True
    assert "model" not in row("serial-a", A).latest_snapshot.document["machine"]
    beat("serial-a", A)                                        # A beating again
    assert listed(A) == "Acorn"


@pytest.mark.django_db
def test_forged_results_for_a_serial_go_to_the_forgers_own_row():
    honest_board("serial-a", A)
    verified("serial-a", "fail", B)            # B has no row for it: dropped
    beat("serial-a", B, online=False)
    assert listed(A) == "Acorn"
    assert not BootEvent.objects.filter(detail__result="fail").exists()
    register("serial-a", B, B)                 # now B has its own row for it
    beat("serial-a", B, online=False)
    verified("serial-a", "fail", B)
    assert listed(A) == "Acorn" and listed(B) is None
    assert row("serial-a", A).online is True
    assert not BootEvent.objects.filter(machine=row("serial-a", A),
                                        detail__result="fail").exists()


@pytest.mark.django_db
def test_forged_fresher_registration_does_not_hide_the_board_on_another_port():
    honest_board("serial-a", A)
    assert register("serial-x", A, B) == "rejected"
    assert register("serial-x", B, B) == "registration"
    beat("serial-x", B)
    verified("serial-x", "fail", B)
    assert listed(A) == "Acorn"


@pytest.mark.django_db
def test_forged_board_found_through_the_wrong_prefix_does_not_relabel():
    honest_board("serial-a", A, board="acorn")
    honest_board("serial-b", B, board="arty")
    found("serial-a", "netv2", B)              # no (B, serial-a) row: dropped
    register("serial-a", B, B)
    beat("serial-a", B)
    found("serial-a", "netv2", B)              # B's own row for it: B only
    assert listed(A) == "Acorn"
    assert not BootEvent.objects.filter(machine=row("serial-a", A), detail__board="netv2").exists()
    assert not BootEvent.objects.filter(machine=row("serial-b", B), detail__board="netv2").exists()


@pytest.mark.django_db
def test_a_forged_registration_never_becomes_the_real_boards_snapshot(c):
    honest_board("serial-a", A)
    register("serial-a", A, A, model="real")
    register("serial-a", B, B, model="forged")
    real, forged = row("serial-a", A), row("serial-a", B)
    assert real.latest_snapshot.document["machine"]["model"] == "real"
    assert forged.latest_snapshot.document["machine"]["model"] == "forged"
    assert real.snapshots.count() == 2 and forged.snapshots.count() == 1
    # each port's label document is its own, and none is picked for you
    assert c.get("/fleet/serial-a/rpi-hwid.json").status_code == 409
    for port, model in ((A, "real"), (B, "forged")):
        body = c.get(f"/fleet/serial-a/rpi-hwid.json?port={port}").content.decode()
        assert f'"model": "{model}"' in body
    assert c.get("/fleet/serial-a/rpi-hwid.json?port=" + C).status_code == 404


@pytest.mark.django_db
def test_the_whole_fleet_claimed_from_one_port_hides_nothing():
    honest_board("serial-a", A)
    honest_board("serial-b", B, board="arty")
    claimed = [f"serial-{n}" for n in range(30)] + ["serial-a", "serial-b"]
    for serial in claimed:
        assert register(serial, C, C) == "registration"
        beat(serial, C)
        verified(serial, "fail", C)
    assert Machine.objects.filter(verified_port=C).count() == len(claimed)
    assert set(Machine.objects.exclude(verified_port=C)
               .values_list("serial", "verified_port")) == {
        ("serial-a", A), ("serial-b", B)}
    assert listed(A) == "Acorn" and listed(B) == "Arty A7"


@pytest.mark.django_db
def test_replayed_forged_messages_before_the_real_ones_do_not_hide_the_board():
    """The broker retains and the consumer restarts: B's old forged
    registration and beats may arrive before A's own."""
    register("serial-a", B, B)
    beat("serial-a", B, online=False)
    verified("serial-a", "fail", B)
    honest_board("serial-a", A)
    assert listed(A) == "Acorn"


@pytest.mark.django_db
@pytest.mark.parametrize("prefix", [
    "pi-sw2-p09", "pi-sw2", "tweed", "PI-SW2-P4", "pi-sw2-p4%20", "", " ",
    "pi-sw2-p4.welland.fpgas.online", "#", "+"])
def test_malformed_prefix_is_rejected_and_logged(prefix, caplog):
    topic = f"port/{prefix}/{SITE}serial-a/registration"
    doc = {"schema": 1, "machine": {"serial": "serial-a"},
           "connection": {"site": "welland", "hostname": "pi-sw2-p4"}}
    with caplog.at_level(logging.WARNING):
        assert consumer.dispatch(topic, json.dumps(doc).encode()) == "rejected"
    assert not Machine.objects.exists()
    assert "rejected" in caplog.text


@pytest.mark.django_db
def test_prefixed_topics_of_another_shape_are_foreign():
    assert consumer.dispatch("port", b"1") == "ignored"
    assert consumer.dispatch("port/pi-sw2-p4/sensors/x", b"1") == "ignored"
    assert consumer.dispatch("port/not a port/sensors/x", b"1") == "ignored"
    assert consumer.dispatch(f"port/pi-sw2-p4/{SITE}s/other", b"{}") == "ignored"
    assert consumer.dispatch(f"port/pi-sw2-p4/extra/{SITE}s/event", b"{}") == "ignored"


@pytest.mark.django_db
def test_a_topic_segment_cannot_forge_a_log_line(caplog):
    with caplog.at_level(logging.WARNING):
        beat("serial\nWARNING forged line", A)
        register("serial-a\nforged", "x\nforged", A)
    assert caplog.text and "\nforged" not in caplog.text
    assert all("\n" not in r.getMessage() for r in caplog.records)


@pytest.mark.django_db
def test_status_before_registration_is_dropped_then_follows_it():
    assert beat("serial-a", A) == "status"
    assert not Machine.objects.exists()
    assert register("serial-a", A, A) == "registration"
    beat("serial-a", A)
    verified("serial-a", "pass", A)
    assert listed(A) == ""  # listed, no board found


# --- unstamped messages, during the transition -----------------------------

@pytest.mark.django_db
def test_a_stamped_beat_for_an_unstamped_machine_goes_to_no_row():
    register("serial-a", A)
    assert beat("serial-a", A, online=True) == "status"
    assert verified("serial-a", "pass", A) == "event"
    assert row("serial-a").online is False and not BootEvent.objects.exists()


@pytest.mark.django_db
def test_a_stamped_registration_never_adopts_the_unstamped_row():
    register("serial-a", A)
    beat("serial-a")
    register("serial-a", A, A)
    assert Machine.objects.filter(serial="serial-a").count() == 2
    assert row("serial-a").online is True and row("serial-a", A).online is False
    beat("serial-a", online=False)         # the old form touches only its own row
    assert row("serial-a", A).online is False


@pytest.mark.django_db
def test_unprefixed_forgery_cannot_displace_a_prefix_established_board():
    honest_board("serial-a", A)
    # the old form, from any Pi: a newer machine on A's hostname that beats
    # and passes, and attempts on A's own serial
    register("serial-x", A)
    beat("serial-x")
    verified("serial-x", "pass")
    found("serial-x", "tt")
    register("serial-a", B)
    beat("serial-a", online=False)
    verified("serial-a", "fail")
    found("serial-a", "netv2")
    assert listed(A) == "Acorn"
    assert listed(B) is None
    assert row("serial-a", A).online is True


@pytest.mark.django_db
def test_unprefixed_claim_on_a_host_nobody_stamped_still_works():
    honest_board("serial-a", A)
    assert register("serial-y", B) == "registration"
    beat("serial-y")
    verified("serial-y", "pass")
    found("serial-y", "arty")
    assert listed(B) == "Arty A7" and listed(A) == "Acorn"


@pytest.mark.django_db
@pytest.mark.parametrize("on", [False, True])
def test_unprefixed_only_operation_is_unchanged(settings, on):
    settings.FLEET_MQTT = {**settings.FLEET_MQTT, "port_prefix": on}
    assert register("serial-a", A) == "registration"
    assert beat("serial-a") == "status"
    verified("serial-a", "pass")
    found("serial-a", "acorn")
    assert listed(A) == "Acorn"
    # as today, the newest machine on a hostname speaks for it
    register("serial-z", A)
    beat("serial-z")
    verified("serial-z", "fail")
    assert listed(A) is None
    assert set(Machine.objects.values_list("verified_port", flat=True)) == {""}


# --- moves -----------------------------------------------------------------

@pytest.mark.django_db
def test_a_board_that_moved_ports_is_listed_at_once_and_leaves_history():
    honest_board("serial-a", A)
    # it boots on B while A's last will has not been heard yet
    honest_board("serial-a", B, boot_id="b2")
    assert listed(B) == "Acorn"
    assert Machine.objects.filter(serial="serial-a").count() == 2
    assert row("serial-a", A).events.count() > 0         # kept as history
    # A's row stops being listed when its beats stop, here by its last will
    beat("serial-a", A, online=False)
    assert listed(A) is None and listed(B) == "Acorn"


@pytest.mark.django_db
def test_a_newer_board_on_the_same_port_replaces_the_older_as_today():
    honest_board("serial-a", A)
    honest_board("serial-n", A, board="arty")
    assert listed(A) == "Arty A7"


# --- pages -----------------------------------------------------------------

@pytest.mark.django_db
def test_detail_page_names_the_port_and_notes_other_claims(c):
    honest_board("serial-a", A)
    register("serial-b", B)
    page = c.get("/fleet/serial-b/").content.decode()
    assert "Registered from port" in page and "none (no port stamp)" in page
    assert "also claimed" not in page
    assert "Registered from port" in c.get("/fleet/serial-a/").content.decode()
    register("serial-a", B, B)
    page = c.get("/fleet/serial-a/").content.decode()
    assert f"This serial is also claimed from {B}" in page
    assert f"This serial is also claimed from {A}" in c.get(
        f"/fleet/serial-a/?port={B}").content.decode()
    assert c.get("/fleet/serial-a/?port=").status_code == 404   # no unstamped row


@pytest.mark.django_db
def test_list_page_has_a_row_and_port_per_sighting(c):
    honest_board("serial-a", A)
    register("serial-a", B, B)
    register("serial-a", A)
    page = c.get("/fleet/").content.decode()
    assert page.count("<td>serial-a</td>") == 3
    assert "<th>Port</th>" in page and "no stamp" in page
