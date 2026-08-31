"""FPGA board hosts shown on the /fpgas/ pages.

One row per Raspberry Pi with an FPGA board attached. Network identity is
DERIVED from (switch, port); nothing here stores an IP. Rows with ``switch``
set follow the VLAN-per-port scheme (Welland: pi-sw<s>-p<p> / 10.21.<s>.<p>,
gateway ssh forward <s><pp>22). Rows with ``switch`` NULL keep the legacy
flat scheme (PS1: pi<p> / 10.21.0.<100+p>, forward <100+p>22).
"""

from django.db import models


class Pi(models.Model):
    port = models.IntegerField()
    switch = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="VLAN-per-port switch number; empty = legacy flat scheme")
    mac = models.CharField(max_length=17, blank=True)
    serial_no = models.CharField(max_length=32, blank=True)
    location = models.CharField(max_length=30, blank=True)
    model = models.CharField(max_length=30, blank=True)
    cable_color = models.CharField(max_length=10, blank=True)
    fpga_board = models.CharField(
        max_length=80, blank=True,
        help_text='Attached FPGA board, e.g. "Digilent Arty A7-35T"')

    class Meta:
        ordering = ["switch", "port"]

    def __str__(self):
        return f"{self.hostname} ({self.fpga_board})" if self.fpga_board else self.hostname

    # -- derived network identity --
    @property
    def hostname(self):
        if self.switch is None:
            return f"pi{self.port}"
        return f"pi-sw{self.switch}-p{self.port}"

    @property
    def ip(self):
        if self.switch is None:
            return f"10.21.0.{100 + self.port}"
        return f"10.21.{self.switch}.{self.port}"

    @property
    def ssh_port(self):
        """The gateway's per-Pi ssh dnat port (e.g. 23422 -> 10.21.2.34:22)."""
        if self.switch is None:
            return (100 + self.port) * 100 + 22
        return self.switch * 10000 + self.port * 100 + 22

    @property
    def stream_url(self):
        return f"/live/{self.hostname}.m3u8"
