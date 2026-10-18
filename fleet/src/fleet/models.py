"""Self-registered fleet machines (see the fleet self-registration design).

Machine = identity + presence (mutable; status/LWT churn). HardwareSnapshot
= append-only content-addressed history: a new row ONLY when the document
fingerprint changes. BootEvent = the boot-stage timeline, kept forever (D-6)."""

from django.db import models


class Machine(models.Model):
    serial = models.CharField(max_length=32, unique=True)
    site = models.CharField(max_length=32)
    hostname = models.CharField(max_length=64, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField()
    online = models.BooleanField(default=False)
    last_boot_id = models.CharField(max_length=40, blank=True)
    last_uptime_s = models.PositiveIntegerField(default=0)
    latest_snapshot = models.ForeignKey(
        "HardwareSnapshot", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["site", "hostname", "serial"]

    def __str__(self):
        return f"{self.hostname or self.serial} @ {self.site}"


class HardwareSnapshot(models.Model):
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE,
                                related_name="snapshots")
    fingerprint = models.CharField(max_length=64, db_index=True)
    document = models.JSONField()
    first_seen = models.DateTimeField(auto_now_add=True)
    last_confirmed = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["first_seen"]
        constraints = [models.UniqueConstraint(
            fields=["machine", "fingerprint"], name="uniq_machine_fingerprint")]


class BootEvent(models.Model):
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE,
                                related_name="events")
    boot_id = models.CharField(max_length=40)
    stage = models.CharField(max_length=64)
    detail = models.JSONField(default=dict, blank=True)
    ts = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ["ts"]


class GatewayRecord(models.Model):
    """One record the site's gateway published about a board port: a link change (kind "port", from the switch
    poller) or a DHCP lease event (kind "lease", from dnsmasq's script). `doc` is the record as published and
    validated (services.parse_gateway_record); the columns are the parts a query needs. `machine` is the Pi it was
    matched to (by MAC first, else by hostname), or None when none is registered yet."""

    KINDS = ("port", "lease")

    site = models.CharField(max_length=32)
    kind = models.CharField(max_length=8, choices=[(k, k) for k in KINDS])
    switch = models.PositiveIntegerField(null=True, blank=True)
    port = models.PositiveIntegerField(null=True, blank=True)
    hostname = models.CharField(max_length=64, blank=True)
    mac = models.CharField(max_length=17, null=True, blank=True)
    at = models.DateTimeField(db_index=True)
    doc = models.JSONField()
    machine = models.ForeignKey(Machine, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="gateway_records")

    class Meta:
        ordering = ["at", "id"]
        # (switch, port, at): whether another device was ACKed on a port since a machine was (management.fpga.seen_ports,
        # one device a port)
        indexes = [models.Index(fields=["machine", "kind", "at"]), models.Index(fields=["switch", "port", "at"])]
