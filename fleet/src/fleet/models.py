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
