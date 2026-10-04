import re

from django.http import Http404, HttpResponse
from django.shortcuts import render

from . import hwid
from .models import Machine
from .services import fpga_states, offered_hosts


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
            "fpga_check": states.get(m.pk, ""),
        })
    return render(request, "fleet/list.html", {"rows": rows})


def _sightings(serial):
    """Every machine row for this serial: one per port it registered from,
    and the one with no port if it registered without a port stamp."""
    rows = list(Machine.objects.filter(serial=serial).order_by("verified_port"))
    if not rows:
        raise Http404("no such machine")
    return rows


def _default_row(rows):
    """The row to show when none is asked for: the one the /fpgas/ pages
    offer, else the newest registered from a port, else the one with no
    port."""
    offered = set(offered_hosts().values())
    for pool in ([r for r in rows if r.pk in offered],
                 [r for r in rows if r.verified_port], rows):
        if pool:
            return max(pool, key=lambda r: r.last_seen)


def machine_detail(request, serial):
    rows = _sightings(serial)
    port = request.GET.get("port")  # "" names the row with no port stamp
    machine = _default_row(rows) if port is None else next(
        (r for r in rows if r.verified_port == port), None)
    if machine is None:
        raise Http404("this serial did not register from that port")
    snapshots = machine.snapshots.order_by("-first_seen")
    events = machine.events.filter(boot_id=machine.last_boot_id) \
        if machine.last_boot_id else machine.events.all()
    states = fpga_states()
    return render(request, "fleet/detail.html", {
        "machine": machine,
        "snapshots": snapshots,
        "events": events,
        "fpga_check": states.get(machine.pk, ""),
        "labels": labels_context(machine),
        "sightings": [{"machine": r, "check": states.get(r.pk, ""),
                       "shown": r.pk == machine.pk} for r in rows],
        "also_claimed_from": [r.verified_port or "no port stamp"
                              for r in rows if r.pk != machine.pk],
    })


def labels_context(machine):
    """What the detail page shows of this Pi's rpi-hwid labels: the
    document's summary, rpi-hwid's list of what each label still needs, and
    the site's own notes (events refused, boards from an earlier boot)."""
    built = hwid.build(machine)
    summary = built.document["summary"]
    boards = summary.get("fpga", []) + summary.get("tinytapeout", [])
    try:
        # rpi-hwid lists every label it can make, one with nothing missing
        # as [], so complete is no label with a field missing
        missing = sorted((label, fields) for label, fields
                         in hwid.missing(built.document).items() if fields)
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
    what the Pi sent, to compare with what rpi-hwid makes on the Pi.

    A serial seen from more than one port has a document per port, and none
    of them is the serial's: ?port=<port> picks one (?port= the one with no
    port stamp), and without it a serial with more than one is refused."""
    rows = _sightings(serial)
    port = request.GET.get("port")
    if port is None and len(rows) > 1:
        return HttpResponse(
            f"{serial} registered from more than one port; add ?port= one of: "
            + ", ".join(r.verified_port or "(empty: no port stamp)" for r in rows) + "\n",
            status=409, content_type="text/plain; charset=utf-8")
    machine = rows[0] if port is None else next(
        (r for r in rows if r.verified_port == port), None)
    if machine is None:
        raise Http404("this serial did not register from that port")
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
