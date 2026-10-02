from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

from . import hwid
from .models import Machine
from .services import fpga_states


def machine_list(request):
    machines = Machine.objects.select_related("latest_snapshot")
    states = fpga_states()
    rows = []
    for m in machines:
        doc = m.latest_snapshot.document if m.latest_snapshot else {}
        rows.append({
            "machine": m,
            "model": doc.get("machine", {}).get("model", ""),
            "fpga_kinds": sorted(b.get("kind", "?")
                                 for b in doc.get("fpga", {}).get("boards", [])),
            "fpga_check": states.get(m.serial, ""),
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
        "fpga_check": fpga_states().get(machine.serial, ""),
        "labels": labels_context(machine),
    })


def labels_context(machine):
    """What the detail page shows of this Pi's rpi-hwid labels: the
    document's summary, rpi-hwid's list of what each label still needs, and
    the site's own notes (events refused, boards from an earlier boot)."""
    built = hwid.build(machine)
    summary = built.document["summary"]
    boards = summary.get("fpga", []) + summary.get("tinytapeout", [])
    return {
        "fields": [(k, v) for k, v in sorted(summary.items()) if k not in ("fpga", "tinytapeout")],
        "boards": [sorted(b.items()) for b in boards],
        "missing": sorted(hwid.missing(built.document).items()),
        "notes": built.notes,
        "sources": sorted(built.document["sources"].items()),
    }


def label_input(request, serial):
    """rpi-hwid's label-input document for this Pi, as rpi-hwid writes it,
    named as rpi-hwid names a host's file. Offered complete or not: it is
    what the Pi sent, to compare with what rpi-hwid makes on the Pi."""
    machine = get_object_or_404(Machine, serial=serial)
    document = hwid.build(machine).document
    response = HttpResponse(hwid.dumps(document), content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="{document["host"]}.json"'
    return response
