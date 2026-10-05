"""Upsert the catalogue rows from the site-wide tt-boards.yaml (rendered by fpgas.online-infra).

The file is a mapping with a ``tt_boards`` list; each entry needs ``slug``,
``kind`` and ``title``, and names its board by ``usb_serial`` (empty or absent
for a board that is not here yet).

A file from before the catalogue was by USB serial also says ``switch``,
``port``, ``enabled`` and ``commander``. Those are not read: where a board is,
whether it is there and which Commander drives it come from the fleet's boot
checks (ttsite/boards.py). Such a file loads, and its boards have no serial.
"""

import yaml
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from ttsite.boards import SERIAL, UNLISTED_PREFIX
from ttsite.models import Board

FIELDS = ("usb_serial", "kind", "shuttle", "title", "blurb", "description", "pcb", "pmods", "links", "sort_order")
# What a file from before the catalogue was by USB serial also carries: accepted, not read.
NOT_READ = ("switch", "port", "enabled", "commander")
KINDS = {k for k, _ in Board.KIND_CHOICES}


class Command(BaseCommand):
    help = "Upsert ttsite Board rows from tt-boards.yaml"

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--prune", action="store_true", help="delete boards whose slug is not in the file")
        parser.add_argument("--allow-empty", action="store_true",
                            help="permit --prune against an empty tt_boards list (deletes every board)")

    def handle(self, path, prune, allow_empty, **options):
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        if not isinstance(doc, dict) or not isinstance(doc.get("tt_boards"), list):
            raise CommandError(f"{path}: expected a mapping with a 'tt_boards' list")
        entries = doc["tt_boards"]
        if prune and not entries and not allow_empty:
            raise CommandError(f"{path}: refusing to --prune against an empty 'tt_boards' list; "
                               f"pass --allow-empty if deleting every board is really what you want")
        rows, serials = {}, {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise CommandError(f"{path}: entry is not a mapping: {entry!r}")
            slug = entry.get("slug")
            if not slug:
                raise CommandError(f"{path}: entry without slug: {entry!r}")
            if slug.startswith(UNLISTED_PREFIX) and SERIAL.fullmatch(slug[len(UNLISTED_PREFIX):]):
                raise CommandError(f"{path}: slug {slug!r} is the address of a board no row names: choose another")
            unknown = sorted(set(entry) - {"slug", *FIELDS, *NOT_READ})
            if unknown:
                raise CommandError(f"{path}: board {slug!r} has unknown keys {unknown}")
            kind = entry.get("kind", "asic")
            if kind not in KINDS:
                raise CommandError(f"{path}: board {slug!r} has unknown kind {kind!r}")
            serial = entry.get("usb_serial") or ""
            if not isinstance(serial, str):
                raise CommandError(f"{path}: board {slug!r}: usb_serial must be quoted text, not {serial!r}")
            if serial and not SERIAL.fullmatch(serial):
                raise CommandError(f"{path}: board {slug!r}: usb_serial {serial!r} is not a serial as a board "
                                   f"reports it (8 to 32 lower-case hex digits, nothing else)")
            if serial and serial in serials:
                raise CommandError(f"{path}: boards {serials[serial]!r} and {slug!r} name the same "
                                   f"usb_serial {serial!r}")
            if serial:
                serials[serial] = slug
            defaults = {k: entry[k] for k in FIELDS if k in entry}
            defaults["kind"] = kind
            defaults["usb_serial"] = serial
            defaults.setdefault("title", slug)
            rows[slug] = defaults
        try:
            with transaction.atomic():
                if prune:
                    Board.objects.exclude(slug__in=rows).delete()
                # a serial may move from one row to another: let go of every one that changes before any is set
                for slug, defaults in rows.items():
                    Board.objects.filter(slug=slug).exclude(usb_serial=defaults["usb_serial"]).update(usb_serial="")
                for slug, defaults in rows.items():
                    Board.objects.update_or_create(slug=slug, defaults=defaults)
        except IntegrityError as exc:
            raise CommandError(f"{path}: a usb_serial in it belongs to a row that is not in the file "
                               f"(load with --prune, or take the serial off that row): {exc}") from exc
        seen = set(rows)
        self.stdout.write(f"loaded {len(seen)} boards from {path}")
