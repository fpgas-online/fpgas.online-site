"""The catalogue of tinytapeout.fpgas.online: words a person wrote about a board.

A row names a board by its USB serial (the value on its label) and gives it a
page address, a title and a description. It says nothing about where the
board is plugged in and decides no feature of its page: which board is on
which Pi, and what it is, comes from the fleet's boot checks at the moment of
the request (boards.py). A board no row names is shown all the same.

A row with no USB serial is a page about a board that is not here yet (or a
chip, such as the KianV boxes); `kind` and `shuttle` file such a row on the
index and are not asked for a board a boot check reported.
"""

from django.db import models


class Board(models.Model):
    KIND_CHOICES = [("asic", "TT ASIC"), ("kianv", "KianV RISC-V"), ("fpga", "FPGA emulation")]

    slug = models.SlugField(unique=True)
    usb_serial = models.CharField(max_length=32, blank=True,
                                  help_text="the board's USB serial, as on its label; empty = not here yet")
    kind = models.CharField(max_length=8, choices=KIND_CHOICES,
                            help_text="where the index files this row while no boot check reports the board")
    shuttle = models.CharField(max_length=16, blank=True,
                               help_text="e.g. tt06, for a row no boot check reports; a reported board says its own")
    title = models.CharField(max_length=80)
    blurb = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True, help_text="Plain text; line breaks are kept")
    pcb = models.CharField(max_length=80, blank=True)
    pmods = models.JSONField(default=list, blank=True)
    links = models.JSONField(default=list, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "slug"]
        constraints = [
            models.UniqueConstraint(fields=["usb_serial"], condition=~models.Q(usb_serial=""),
                                    name="ttsite_one_row_per_usb_serial"),
        ]

    def __str__(self):
        return f"{self.slug} ({self.title})"
