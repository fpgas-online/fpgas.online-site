"""A Pi the /fpgas/ pages offer: registered with its port's hostname, its
FPGA check passed in the boot it is running now, with the boards it found."""

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
