"""Upsert Board rows from the site-wide tt-boards.yaml (rendered by fpgas.online-infra).

The file is a mapping with a ``tt_boards`` list; each entry needs ``slug``,
``kind`` and ``title``. ``switch`` defaults to 1, ``port`` may be null.
"""

import yaml
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ttsite.models import Board

FIELDS = ("switch", "port", "kind", "shuttle", "title", "blurb", "description", "pcb", "pmods", "links", "enabled",
          "sort_order")
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
        seen = set()
        with transaction.atomic():
            for entry in entries:
                if not isinstance(entry, dict):
                    raise CommandError(f"{path}: entry is not a mapping: {entry!r}")
                slug = entry.get("slug")
                if not slug:
                    raise CommandError(f"{path}: entry without slug: {entry!r}")
                kind = entry.get("kind", "asic")
                if kind not in KINDS:
                    raise CommandError(f"{path}: board {slug!r} has unknown kind {kind!r}")
                defaults = {k: entry[k] for k in FIELDS if k in entry}
                defaults["kind"] = kind
                defaults.setdefault("title", slug)
                Board.objects.update_or_create(slug=slug, defaults=defaults)
                seen.add(slug)
            if prune:
                Board.objects.exclude(slug__in=seen).delete()
        self.stdout.write(f"loaded {len(seen)} boards from {path}")
