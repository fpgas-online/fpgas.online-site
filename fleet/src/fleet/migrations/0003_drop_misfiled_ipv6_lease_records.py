"""Delete every IPv6 lease record stored so far (site #115).

Until fpgas.online-infra #293, the gateway's lease event script took a lease's port from dnsmasq's
DNSMASQ_INTERFACE. Every port interface of a switch carries the same IPv6 /64, so for an IPv6 lease dnsmasq names
the first interface with that prefix: every IPv6 lease record stored before #293 is filed under the wrong port (on
welland, 14 Oct 2026: all of them the hub Pi's, switch 2 port 30, filed as port 2). The converge that applies this
migration also installs #293's script, so the rows deleted here are the wrong ones. One or two right rows that arrive
between the script's install and this migration go too; the next renewal (about 150 s) brings them back.
"""

from django.db import migrations


def drop_ipv6_lease_records(apps, schema_editor):
    GatewayRecord = apps.get_model("fleet", "GatewayRecord")
    GatewayRecord.objects.filter(kind="lease", doc__family=6).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("fleet", "0002_gatewayrecord"),
    ]

    operations = [
        # nothing to undo: the deleted rows were wrong
        migrations.RunPython(drop_ipv6_lease_records, migrations.RunPython.noop),
    ]
