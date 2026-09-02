"""Boards shown on tinytapeout.fpgas.online.

One row per Tiny Tapeout board at Welland. Network identity is DERIVED from
(switch, port) per the VLAN-per-port scheme (pi-sw<s>-p<p> / 10.21.<s>.<p>);
nothing here stores an IP. Demos/designs are not modelled — they are read
live from the Pi daemon.
"""

from django.db import models


class Board(models.Model):
    KIND_CHOICES = [("asic", "TT ASIC"), ("kianv", "KianV RISC-V"), ("fpga", "FPGA emulation")]

    slug = models.SlugField(unique=True)
    switch = models.PositiveSmallIntegerField(default=1)
    port = models.PositiveSmallIntegerField(null=True, blank=True, help_text="s3300 port; empty = not wired yet")
    kind = models.CharField(max_length=8, choices=KIND_CHOICES)
    shuttle = models.CharField(max_length=16, blank=True, help_text="e.g. tt06; blank for FPGA boards")
    title = models.CharField(max_length=80)
    blurb = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True, help_text="Plain text; line breaks are kept")
    pcb = models.CharField(max_length=80, blank=True)
    pmods = models.JSONField(default=list, blank=True)
    links = models.JSONField(default=list, blank=True)
    enabled = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "slug"]

    def __str__(self):
        return f"{self.slug} ({self.title})"

    # -- derived network identity --
    def _require_port(self):
        if self.port is None:
            raise ValueError(f"board {self.slug} has no port")

    @property
    def hostname(self):
        self._require_port()
        return f"pi-sw{self.switch}-p{self.port}"

    @property
    def ip(self):
        self._require_port()
        return f"10.21.{self.switch}.{self.port}"

    @property
    def stream_url(self):
        return f"/live/{self.hostname}.m3u8"

    @property
    def serial_ws_path(self):
        return f"/ws/board/{self.slug}/serial"

    @property
    def api_base(self):
        return f"/api/board/{self.slug}"

    @property
    def live(self):
        return self.port is not None and self.enabled

    # -- parity with ttsite.gateway.GatewayBoard, so templates work in both modes --
    @property
    def site_name(self):
        return ""  # local catalogue rows carry no site; templates fall back

    @property
    def page_url(self):
        return f"/board/{self.slug}/"

