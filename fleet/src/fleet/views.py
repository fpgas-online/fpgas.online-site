import re

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404

from . import hwid, labelart
from .models import Machine

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


def _label(request, serial):
    """(the Pi's label input, the id asked for) when rpi-hwid makes a label of that id from it; 404 otherwise."""
    machine = get_object_or_404(Machine, serial=serial)
    document = hwid.build(machine).document
    label_id = request.GET.get("id", "")
    try:
        ids = labelart.made_ids(document)
    except hwid.label_input.InputError:  # the last guard (contract §35): the page says why
        raise Http404("rpi-hwid refuses this Pi's label input") from None
    if label_id not in ids:
        raise Http404("rpi-hwid makes no label of that id for this Pi")
    return document, label_id


def label_png(request, serial):
    """One of the Pi's labels as an image, for its page."""
    document, label_id = _label(request, serial)
    return HttpResponse(labelart.png(document, label_id), content_type="image/png")


def label_pdf(request, serial):
    """One of the Pi's labels as rpi-hwid prints it: a one-page PDF the label's size."""
    document, label_id = _label(request, serial)
    response = HttpResponse(labelart.pdf(document, label_id), content_type="application/pdf")
    name = label_id.rsplit("/", 1)[-1].replace(" ", "-")  # "acorn-linden 0x0044..." -> acorn-linden-0x0044...
    response["Content-Disposition"] = f'inline; filename="{name if SAFE_NAME.fullmatch(name) else FALLBACK_NAME}.pdf"'
    return response
