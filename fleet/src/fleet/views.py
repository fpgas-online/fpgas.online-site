import re

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
    try:
        missing = sorted(hwid.missing(built.document).items())
    except hwid.label_input.InputError as exc:
        # build() only makes documents rpi-hwid takes; this is the last
        # guard, so a page is never lost to what a Pi sent (contract §35)
        missing = [("document", exc.problems)]
    return {
        "fields": [(k, v) for k, v in sorted(summary.items()) if k not in ("fpga", "tinytapeout")],
        "boards": [sorted(b.items()) for b in boards],
        "missing": missing,
        "notes": built.notes,
        "sources": sorted(built.document["sources"].items()),
    }


# What a download's file name may hold, all of it (fullmatch: `$` would also
# match before a trailing newline). A host name that is anything else -- it
# comes from the registration, which anyone on the site LAN can send -- gives
# FALLBACK_NAME, never the serial, which comes from the same place.
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
FALLBACK_NAME = "rpi-hwid"


def label_input(request, serial):
    """rpi-hwid's label-input document for this Pi, as rpi-hwid writes it,
    named as rpi-hwid names a host's file. Offered complete or not: it is
    what the Pi sent, to compare with what rpi-hwid makes on the Pi."""
    machine = get_object_or_404(Machine, serial=serial)
    built = hwid.build(machine)
    try:
        text = hwid.dumps(built.document)
    except hwid.label_input.InputError as exc:
        # the last guard, as on the detail page (contract §35)
        return HttpResponse("rpi-hwid refuses this Pi's label input:\n"
                            + "\n".join(exc.problems + built.notes) + "\n",
                            status=409, content_type="text/plain; charset=utf-8")
    host = built.document["host"]
    name = host if SAFE_NAME.fullmatch(host) else FALLBACK_NAME
    response = HttpResponse(text, content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="{name}.json"'
    return response
