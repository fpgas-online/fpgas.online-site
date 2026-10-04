"""The consumer's per-port topic prefix: the broker's listener for a port
stamps `port/<port>/` on everything its Pi publishes, so the port in the
prefix is the one thing a Pi cannot choose. Messages are driven through
consumer.dispatch, and what the /fpgas/ pages offer is read back through
pibfpgas.pis.offered_pi."""

import json
import logging

import pytest
from fleet.models import BootEvent, Machine
from pibfpgas.pis import offered_pi

from fleet import consumer

SITE = "fpgas/welland/pi/"


def send(serial, kind, doc, port=None):
    """Publish one message as the broker would deliver it, stamped with the
    port the Pi is on when `port` is given. Returns what dispatch did."""
    topic = f"{SITE}{serial}/{kind}"
    if port is not None:
        topic = f"port/{port}/{topic}"
    return consumer.dispatch(topic, json.dumps(doc).encode())


def register(serial, hostname, port=None):
    return send(serial, "registration",
                {"schema": 1, "machine": {"serial": serial},
                 "connection": {"site": "welland", "hostname": hostname}}, port)


def beat(serial, port=None, boot_id="b1"):
    return send(serial, "status", {"online": True, "boot_id": boot_id}, port)


def verified(serial, result, port=None, boot_id="b1"):
    return send(serial, "event", {
        "stage": "fpga-verified", "boot_id": boot_id,
        "detail": {"result": result, "mode": "auto"}}, port)


def found(serial, board, port=None, boot_id="b1"):
    return send(serial, "event", {
        "stage": "fpga-board-found", "boot_id": boot_id,
        "detail": {"board": board, "variant": "-", "where": "1-1.2"}}, port)


def honest_board(serial, port, board="acorn"):
    """A Pi on `port` (short hostname) that registers, beats, finds a board
    and passes its check, all through its own port's listener."""
    assert register(serial, port, port) == "registration"
    beat(serial, port)
    found(serial, board, port)
    verified(serial, "pass", port)


def listed(port):
    pi = offered_pi(port)
    return pi.boards if pi else None


A, B = "pi-sw2-p46", "pi-sw2-p47"


@pytest.mark.django_db
def test_honest_prefixed_board_is_listed_and_remembers_its_port():
    honest_board("serial-a", A)
    assert listed(A) == "Acorn"
    assert Machine.objects.get(serial="serial-a").verified_port == A


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
    assert "serial-b" in caplog.text and A in caplog.text and B in caplog.text


@pytest.mark.django_db
def test_forged_registration_of_a_listed_serial_for_another_host_is_rejected():
    honest_board("serial-a", A)
    # a Pi on B names A's host with A's serial: refused, A's machine untouched
    assert register("serial-a", A, B) == "rejected"
    assert Machine.objects.get(serial="serial-a").verified_port == A
    assert listed(A) == "Acorn"


@pytest.mark.django_db
def test_a_pi_cannot_move_a_board_that_is_online_on_another_port(caplog):
    honest_board("serial-a", A)
    with caplog.at_level(logging.WARNING):
        assert register("serial-a", B, B) == "rejected"
    m = Machine.objects.get(serial="serial-a")
    assert (m.hostname, m.verified_port) == (A, A)
    assert listed(A) == "Acorn" and listed(B) is None
    assert "online on port " + A in caplog.text


@pytest.mark.django_db
def test_forged_fail_through_another_ports_prefix_does_not_hide_a_board(caplog):
    honest_board("serial-a", A)
    honest_board("serial-b", B)
    with caplog.at_level(logging.WARNING):
        verified("serial-a", "fail", B)          # B's Pi speaks for A's serial
        beat("serial-a", B)
        send("serial-a", "status", {"online": False, "boot_id": "b1"}, B)
    assert listed(A) == "Acorn" and listed(B) == "Acorn"
    assert BootEvent.objects.filter(machine__serial="serial-a",
                                    detail__result="fail").count() == 0
    assert "serial-a" in caplog.text


@pytest.mark.django_db
def test_forged_fresher_registration_does_not_hide_the_board_on_another_port():
    honest_board("serial-a", A)
    # B's Pi registers a new machine claiming A's hostname, the old attack
    assert register("serial-x", A, B) == "rejected"
    beat("serial-x", B)
    verified("serial-x", "fail", B)
    assert listed(A) == "Acorn"


@pytest.mark.django_db
def test_forged_board_found_through_the_wrong_prefix_does_not_relabel():
    honest_board("serial-a", A, board="acorn")
    honest_board("serial-b", B, board="arty")
    found("serial-a", "netv2", B)
    assert listed(A) == "Acorn" and listed(B) == "Arty A7"


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
    assert consumer.dispatch("port/pi-sw2-p4/sensors/x", b"1") == "ignored"
    assert consumer.dispatch(f"port/pi-sw2-p4/{SITE}s/other", b"{}") == "ignored"
    assert consumer.dispatch(f"port/pi-sw2-p4/extra/{SITE}s/event", b"{}") == "ignored"


@pytest.mark.django_db
def test_status_before_registration_is_dropped_then_follows_it():
    assert beat("serial-a", A) == "status"
    assert not Machine.objects.exists()
    assert register("serial-a", A, A) == "registration"
    beat("serial-a", A)
    verified("serial-a", "pass", A)
    assert listed(A) == ""  # listed, no board found


@pytest.mark.django_db
def test_unprefixed_forgery_cannot_displace_a_prefix_established_board(caplog):
    honest_board("serial-a", A)
    with caplog.at_level(logging.WARNING):
        # the old form, from any Pi: a newer machine on A's hostname that
        # beats and passes ...
        register("serial-x", A)
        beat("serial-x")
        verified("serial-x", "pass")
        found("serial-x", "tt")
        # ... and attempts on A's own machine
        assert register("serial-a", B) == "rejected"
        send("serial-a", "status", {"online": False, "boot_id": "b1"})
        verified("serial-a", "fail")
        found("serial-a", "netv2")
    assert listed(A) == "Acorn"
    assert listed(B) is None
    m = Machine.objects.get(serial="serial-a")
    assert (m.hostname, m.online) == (A, True)


@pytest.mark.django_db
def test_unprefixed_claim_on_a_host_nobody_stamped_still_works():
    honest_board("serial-a", A)
    assert register("serial-y", B) == "registration"
    beat("serial-y")
    verified("serial-y", "pass")
    found("serial-y", "arty")
    assert listed(B) == "Arty A7" and listed(A) == "Acorn"


@pytest.mark.django_db
def test_unprefixed_only_operation_is_unchanged():
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


@pytest.mark.django_db
def test_a_prefixed_registration_upgrades_a_machine_seen_unprefixed():
    register("serial-a", A)
    beat("serial-a")
    assert register("serial-a", A, A) == "registration"
    assert Machine.objects.get(serial="serial-a").verified_port == A


@pytest.mark.django_db
def test_a_board_that_moved_ports_is_handled_as_moves_are_today():
    honest_board("serial-a", A)
    # it goes quiet on A (its last will says offline), then boots on B
    send("serial-a", "status", {"online": False, "boot_id": "b1"}, A)
    assert listed(A) is None
    assert register("serial-a", B, B) == "registration"
    beat("serial-a", B, boot_id="b2")
    found("serial-a", "acorn", B, boot_id="b2")
    verified("serial-a", "pass", B, boot_id="b2")
    assert listed(B) == "Acorn" and listed(A) is None
    assert Machine.objects.get(serial="serial-a").verified_port == B
    # a late message from its old port no longer reaches it
    verified("serial-a", "fail", A, boot_id="b2")
    assert listed(B) == "Acorn"


@pytest.mark.django_db
def test_a_newer_board_on_the_same_port_replaces_the_older_as_today():
    honest_board("serial-a", A)
    honest_board("serial-n", A, board="arty")
    assert listed(A) == "Arty A7"
