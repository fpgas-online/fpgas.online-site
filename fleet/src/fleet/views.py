from django.shortcuts import get_object_or_404, render

from .models import Machine


def machine_list(request):
    machines = Machine.objects.select_related("latest_snapshot")
    rows = []
    for m in machines:
        doc = m.latest_snapshot.document if m.latest_snapshot else {}
        rows.append({
            "machine": m,
            "model": doc.get("machine", {}).get("model", ""),
            "fpga_kinds": sorted(b.get("kind", "?")
                                 for b in doc.get("fpga", {}).get("boards", [])),
        })
    return render(request, "fleet/list.html", {"rows": rows})


def machine_detail(request, serial):
    machine = get_object_or_404(Machine, serial=serial)
    snapshots = machine.snapshots.order_by("-first_seen")
    events = machine.events.filter(boot_id=machine.last_boot_id) \
        if machine.last_boot_id else machine.events.all()
    return render(request, "fleet/detail.html", {
        "machine": machine,
        "snapshots": snapshots,
        "events": events,
    })
