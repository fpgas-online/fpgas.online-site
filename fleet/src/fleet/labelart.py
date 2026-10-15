"""rpi-hwid's labels of one Pi, made on the server from its label input (#41; Tim, 16 Oct 2026, site #141: "Why are
labels still not being rendered on the device page?").

The label input is fleet.hwid's document (what the Pi and its boot check sent). rpi-hwid makes each label it can from
it (labels.list_labels, labels.render_label: label inputs in, a PDF out; nothing read from hardware, nothing written)
and refuses, by name, each one a field is missing for. A label is made per kind, so a Pi label rpi-hwid refuses does
not keep its board's label off the page. The page shows each made label as an image (the PDF's page drawn by
pypdfium2) linking its PDF, the one rpi-hwid would print; a refused label is a row saying what it still needs.

Rendering is cached by the document's own bytes and the label's id: the same facts make the same label.
"""

import hashlib
import io
from dataclasses import dataclass

import pypdfium2
from django.core.cache import cache
from rpi_hwid import label_input, labels, placement

# Every kind of full-size label rpi-hwid makes (the micro labels are for small devices, not fleet hosts).
KINDS = tuple(labels.KINDS)
# What rpi-hwid raises when a label cannot be made from the facts given: each names the label and what it lacks; and
# ValueError, which its label code raises for a value it cannot print ("not a hex Device DNA: 'nan'": the broker
# takes events from anything on the site LAN, so a forged one must not take the page down). Each is shown on the
# page in rpi-hwid's own words; any other exception is a fault and is not caught.
REFUSALS = (ValueError, label_input.InputError, placement.PlacementError) + tuple(
    getattr(labels, name) for name in dir(labels)
    if isinstance(getattr(labels, name), type) and issubclass(getattr(labels, name), Exception)
    and getattr(labels, name).__module__ == labels.__name__)
SCALE = 4  # pixels per PDF point drawn: a 63.5 x 38.1 mm label is 720 x 432 px, shown at a quarter of that
CACHE_SECONDS = 24 * 3600


@dataclass(frozen=True)
class Made:
    id: str  # rpi-hwid's label id: "<host>/<kind>/<title>"
    kind: str
    title: str


def _text(document):
    return label_input.dumps(document)


def made(document):
    """([Made], [(kind, rpi-hwid's refusal)]): every label rpi-hwid makes from `document`, a kind at a time, and each
    kind it refuses, the exception in its own words."""
    out, refused = [], []
    for kind in KINDS:
        try:
            listed = labels.list_labels([document], only={kind})
        except REFUSALS as exc:
            # one board's refusal must not keep another board's label off the page: each board alone
            alone = list(_one_board_each(document))
            if not alone:
                refused.append((kind, exc))
                continue
            listed = []
            for single in alone:
                try:
                    listed.extend(labels.list_labels([single], only={kind}))
                except REFUSALS as one:
                    refused.append((kind, one))
        out.extend(Made(e["id"], e["kind"], e["title"]) for e in listed)
    return list(dict.fromkeys(out)), refused


def _one_board_each(document):
    """The document once per board it lists (FPGA and Tiny Tapeout boards), each copy with only that board; nothing
    for a document with fewer than two boards."""
    summary = document["summary"]
    boards = [("fpga", b) for b in summary.get("fpga") or []] + [("tinytapeout", b) for b in
                                                                 summary.get("tinytapeout") or []]
    if len(boards) < 2:
        return
    for key, board in boards:
        yield {**document, "summary": {**summary, "fpga": [], "tinytapeout": [], key: [board]}}


def _key(document, label_id, form):
    digest = hashlib.sha256((_text(document) + "\0" + label_id + "\0" + form).encode()).hexdigest()
    return f"fleet-label:{digest}"


def pdf(document, label_id):
    """One label as rpi-hwid prints it, a one-page PDF the label's size. KeyError for an id it does not make."""
    key = _key(document, label_id, "pdf")
    data = cache.get(key)
    if data is None:
        data = _render(document, label_id)
        cache.set(key, data, CACHE_SECONDS)
    return data


def _render(document, label_id):
    """render_label from the whole document, else from the one-board copy that makes it (as made() listed it)."""
    try:
        return labels.render_label([document], label_id)
    except REFUSALS + (KeyError,) as whole:
        for single in _one_board_each(document):
            try:
                return labels.render_label([single], label_id)
            except REFUSALS + (KeyError,):
                continue
        raise KeyError(label_id) from whole


def png(document, label_id):
    """One label as an image: its PDF's page drawn at SCALE."""
    key = _key(document, label_id, "png")
    data = cache.get(key)
    if data is None:
        page = pypdfium2.PdfDocument(pdf(document, label_id))[0]
        image = page.render(scale=SCALE).to_pil()
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        data = buf.getvalue()
        cache.set(key, data, CACHE_SECONDS)
    return data
