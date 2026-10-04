"""Registered Pis for tests. verified_pi() is one the /fpgas/ pages offer:
registered with its port's hostname, its FPGA check passed in the boot it is
running now, with the boards it found. machine(), verifying(), verified() and
registered() build one a step at a time, with whatever result its check gave."""

import datetime

from django.utils import timezone
from fleet.models import BootEvent, Machine


def verified_pi(hostname, *boards, serial=None, boot_id="b1"):
    """`boards` are (board, variant) pairs, as fpga-board-found sends them."""
    now = timezone.now()
    m = Machine.objects.create(serial=serial or hostname, site="welland", hostname=hostname,
                               last_seen=now, online=True, last_boot_id=boot_id)
    for i, (board, variant) in enumerate(boards):
        BootEvent.objects.create(machine=m, boot_id=boot_id, stage="fpga-board-found", ts=now,
                                 detail={"board": board, "variant": variant, "where": f"1-1.{i + 2}"})
    BootEvent.objects.create(machine=m, boot_id=boot_id, stage="fpga-verified", ts=now,
                             detail={"result": "pass", "mode": "auto"})
    return m


T0 = timezone.now()


def machine(serial, hostname="", boot_id="b2"):
    # online, its status beat just in: last_seen is stamped now, not at T0,
    # which a long test run leaves minutes behind
    return Machine.objects.create(serial=serial, site="welland", hostname=hostname,
                                  last_seen=timezone.now(), online=True, last_boot_id=boot_id)


def verifying(m, boot_id="b2", minutes=0):
    BootEvent.objects.create(machine=m, boot_id=boot_id, stage="fpga-verifying",
                             detail={"started_at": "2026-09-27T06:00:00+00:00"},
                             ts=T0 + datetime.timedelta(minutes=minutes))


def verified(m, result, boot_id="b2", minutes=0):
    BootEvent.objects.create(machine=m, boot_id=boot_id, stage="fpga-verified",
                             detail={"result": result, "mode": "all-boards"},
                             ts=T0 + datetime.timedelta(minutes=minutes))


def registered(serial, hostname, result, minutes=0):
    m = machine(serial, hostname)
    m.last_seen = timezone.now() - datetime.timedelta(minutes=2) + datetime.timedelta(minutes=minutes)
    m.save()
    verified(m, result)
    return m
