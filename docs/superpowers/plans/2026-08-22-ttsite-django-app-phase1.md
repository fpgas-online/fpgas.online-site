# `ttsite` Django app (tinytapeout.fpgas.online, phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `ttsite` Django app to `fpgas.online-site`: a Tiny Tapeout-branded landing page, per-board pages (camera + embedded Commander + board info + status log + power-cycle), a docs page, a data-driven `Board` model seeded from the infra YAML, a host-routing middleware so `tinytapeout.fpgas.online` serves `ttsite.urls`, and a `/board/<slug>/status.json` proxy to the Pi daemon's `/health` — with pytest-django tests in CI.

**Architecture:** One new Django app in the existing `pib` project, following the repo's `src/` layout. `Board` rows are the only new data (demos/designs are read live from the Pi daemon in phase 2). A 20-line middleware switches `request.urlconf` by host — no `django-hosts`. The board page composes the existing HLS camera include, the existing `pistat` WebSocket status log, the existing `snmp_switch` PoE toggle, and the Commander embed bundle (served from `STATIC_ROOT/tt-commander/<ver>/`, deployed by infra). The WebSocket `/ws/board/<slug>/serial` is an **nginx** proxy (infra plan D), not Django. Phase-2/3 API proxies (`/api/board/<slug>/designs|bitstream|kianv/boot`) are not built here; a small daemon-client module is, so they slot in.

**Tech Stack:** Django 4.2 (existing), Channels/daphne (existing), `requests` (new runtime dep for the daemon client), pytest + pytest-django, ruff, `uv`, GitHub Actions.

**Spec:** `fpgas.online-infra/docs/superpowers/specs/2026-08-22-tinytapeout-fpgas-online-design.md` §6 (6.1–6.7), §3, §9, §10, §12. Daemon `/health` shape: `fpgas.online-tt` README (`{board:{present,device,vid_pid}, kind, slug, switch, port, hostname, clients, uptime_s, version, config_error}`). Embed API: `tt-commander-app` fork `README.fpgas-online.md` (`mountCommander(el, {transport:{kind:'websocket',url}, board:{slug,kind,shuttle}, apiBase})`).

## Global Constraints

- Repo `fpgas-online/fpgas.online-site`; app package `ttsite` at `ttsite/src/ttsite/` (same `src/` layout as `pibfpgas`); installed into the single `fpgas-online-site` package via `pyproject.toml`.
- Python ≥ 3.11; ruff line-length 120, rules E/F/W/I (repo `ruff.toml`); migrations excluded from lint (already).
- `Board` model fields exactly as spec §6.1: `slug` (SlugField unique), `switch` (PositiveSmallIntegerField default 1), `port` (PositiveSmallIntegerField null/blank), `kind` (choices `asic|kianv|fpga`), `shuttle` (CharField blank), `title`, `blurb`, `description` (TextField blank, markdown), `pcb` (blank), `pmods` (JSONField default list), `links` (JSONField default list), `enabled` (bool default True), `sort_order` (PositiveSmallIntegerField default 0). Properties: `hostname` = `f"pi-sw{switch}-p{port}"`, `ip` = `f"10.21.{switch}.{port}"`, `stream_url` = `f"/live/{hostname}.m3u8"`, `serial_ws_path` = `f"/ws/board/{slug}/serial"`, `api_base` = `f"/api/board/{slug}"`, `live` = `port is not None and enabled`. When `port is None` the hostname/ip properties raise `ValueError("board has no port")`.
- Seed command `manage.py ttsite_loadboards <yaml>`: YAML is a mapping with `tt_boards:` list (same file the Pi daemon reads); upsert by slug; `--prune` deletes rows whose slug is absent; idempotent.
- Host switch: `settings.TTSITE_HOST` (default `"tinytapeout.fpgas.online"`); middleware sets `request.urlconf = "ttsite.urls"` when `request.get_host()` (port stripped) equals it. `ttsite.urls` also includes `admin/`, `snmp/`, `pistat/`, `pibup/` so existing endpoints keep working on that host.
- URLs (on the TT host): `/` landing; `/board/<slug>/` board page; `/board/<slug>/status.json` (daemon `/health` proxy, 5 s cache, always HTTP 200 with `{reachable: bool, ...}`); `/docs/` curated docs. 404 for unknown slug; board pages for `enabled=False` or `port=None` render the "coming soon" variant (still 200).
- Daemon client: `ttsite/daemon.py` with `health(board, timeout=3.0) -> dict` using `requests.get(f"http://{board.ip}:8765/health")`; never raises to the view — returns `{"reachable": False, "error": str(exc)}` on any `requests.RequestException`/non-200/invalid JSON.
- Theme: TT logo at `static/ttsite/ttlogo_400.png` downloaded from `https://tinytapeout.com/ttlogo_400.png` (record source + date + "attribution; TT team to be notified" in `static/ttsite/README.md`); palette primary `#544ead`, secondary `#8afbfd`; Roboto via Google Fonts link in `base.html`; footer text exactly: "A community-run Tiny Tapeout hardware instance hosted by fpgas.online at Welland, South Australia — not operated by Tiny Tapeout Ltd." with a GitHub source link; nav: Boards · ASIC · KianV · FPGA · Docs · tinytapeout.com ↗.
- Commander embed is loaded from `{{ STATIC_URL }}tt-commander/{{ TTSITE_COMMANDER_VERSION }}/tt-commander-embed.js` (+ `.css`); `settings.TTSITE_COMMANDER_VERSION` default `""` → when empty the board page shows a "Commander bundle not deployed" notice instead of the mount script. The mount uses `transport.url = wss://<host>/ws/board/<slug>/serial` built client-side from `location.host`.
- The status log subscribes to BOTH `ws/pistat/<hostname>/` and `ws/pistat/pi<port>/` (the SNMP app still notifies the legacy `pi<N>` group). `pistat` routing/URL regexes are widened from `\w+` to `[\w-]+` so hyphenated hostnames route.
- Tests: pytest-django, sqlite, `DJANGO_SETTINGS_MODULE=pib.settings`; `pib/settings.py` must import cleanly without `pib/local_settings.py` (it is gitignored and Ansible-written) — wrap the import: `try: from pib.local_settings import *  # noqa  except ModuleNotFoundError as exc: if exc.name != "pib.local_settings": raise`. Tests use `InMemoryChannelLayer` via a conftest override. CI job `test` added to `.github/workflows/lint.yml`.
- Every change via PR with CI green; feature branches in `.worktrees/` (add to `.gitignore`); never force-push; commit trailer:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01UdRpsg6jY8PbcQxxX6txKE
  ```
- `uv` for all Python; never `/tmp`; ISO dates.

---

## File structure

```
fpgas.online-site/
├── pyproject.toml                         (modify: requests dep, ttsite package, pytest config, dev deps)
├── pib/settings.py                        (modify: ttsite app, middleware, TTSITE_* settings, tolerant local_settings import)
├── pib/urls.py                            (unchanged — main host)
├── ttsite/src/ttsite/__init__.py
├── ttsite/src/ttsite/apps.py
├── ttsite/src/ttsite/models.py            Board
├── ttsite/src/ttsite/migrations/0001_initial.py
├── ttsite/src/ttsite/admin.py
├── ttsite/src/ttsite/middleware.py        TTSiteHostMiddleware
├── ttsite/src/ttsite/urls.py
├── ttsite/src/ttsite/views.py             index, board, board_status, docs
├── ttsite/src/ttsite/daemon.py            health()
├── ttsite/src/ttsite/docs_links.py        the curated link list (data, not template logic)
├── ttsite/src/ttsite/management/commands/ttsite_loadboards.py
├── ttsite/src/ttsite/templates/ttsite/base.html
├── ttsite/src/ttsite/templates/ttsite/index.html
├── ttsite/src/ttsite/templates/ttsite/board.html
├── ttsite/src/ttsite/templates/ttsite/docs.html
├── ttsite/src/ttsite/static/ttsite/ttsite.css
├── ttsite/src/ttsite/static/ttsite/board.js       mount Commander, pistat log, power-cycle, status pill
├── ttsite/src/ttsite/static/ttsite/ttlogo_400.png + README.md
├── pistat/src/pistat/routing.py, urls.py  (modify: [\w-]+)
├── tests/conftest.py, tests/test_models.py, tests/test_loadboards.py, tests/test_middleware.py,
│   tests/test_views.py, tests/test_daemon.py, tests/test_pistat_routing.py, tests/data/tt-boards.yaml
├── .github/workflows/lint.yml             (modify: + test job)
└── README.md / CLAUDE.md                  (modify: ttsite app rows)
```

---

### Task 1: App scaffold, settings, pytest + CI

**Files:**
- Create: `ttsite/src/ttsite/__init__.py`, `apps.py`, `migrations/__init__.py`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_settings_import.py`
- Modify: `pyproject.toml`, `pib/settings.py`, `.gitignore`, `.github/workflows/lint.yml`

**Interfaces:**
- Produces: importable `ttsite` app (`INSTALLED_APPS` has `'ttsite'`), `settings.TTSITE_HOST`, `settings.TTSITE_COMMANDER_VERSION`, a working `uv run pytest` with pytest-django.

- [ ] **Step 1: Worktree**

```bash
cd /home/tim/github/fpgas-online/fpgas.online-site && git fetch origin && git worktree add .worktrees/ttsite-scaffold -b ttsite-scaffold origin/main && cd .worktrees/ttsite-scaffold
```

- [ ] **Step 2: pyproject**

Edit `pyproject.toml`:
```toml
[project]
# ... existing ...
dependencies = [
    "django>=4.2",
    "channels[daphne]>=4.0",
    "paramiko",
    "requests>=2.31",
    "PyYAML>=6",
    "fpgas-online-poe @ git+https://github.com/fpgas-online/fpgas.online-poe.git",
]

[dependency-groups]
dev = ["pytest>=8", "pytest-django>=4.8", "ruff>=0.6"]

[tool.setuptools.packages.find]
where = ["pibfpgas/src", "pistat/src", "pibdemos/src", "pibup/src", "ttsite/src"]
include = ["pib*", "pistat*", "ttsite*"]

[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "pib.settings"
testpaths = ["tests"]
pythonpath = ["."]
```
(Keep the existing `[tool.setuptools.package-data]`.)

- [ ] **Step 3: settings**

In `pib/settings.py`:
- add `'ttsite',` to `INSTALLED_APPS` (after `'pistat'`);
- add `'ttsite.middleware.TTSiteHostMiddleware',` as the **first** entry of `MIDDLEWARE` (it must run before `CommonMiddleware`'s APPEND_SLASH redirects resolve URLs);
- add, above the local_settings import:
  ```python
  # tinytapeout.fpgas.online (ttsite app). Overridable in local_settings.py.
  TTSITE_HOST = "tinytapeout.fpgas.online"
  TTSITE_COMMANDER_VERSION = ""  # e.g. "0.1.0"; empty => bundle not deployed
  ```
- replace the last line with:
  ```python
  try:
      from pib.local_settings import *  # noqa: E402, F403
  except ModuleNotFoundError as exc:  # pragma: no cover - only in dev/test without local_settings
      if exc.name != "pib.local_settings":
          raise
  ```

- [ ] **Step 4: App + test scaffolding**

`ttsite/src/ttsite/__init__.py`: empty. `ttsite/src/ttsite/apps.py`:
```python
from django.apps import AppConfig


class TtsiteConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ttsite"
    verbose_name = "tinytapeout.fpgas.online"
```
`ttsite/src/ttsite/migrations/__init__.py`: empty.

`tests/__init__.py`: empty. `tests/conftest.py`:
```python
import pytest


@pytest.fixture(autouse=True)
def _in_memory_channel_layer(settings):
    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    settings.ALLOWED_HOSTS = ["*"]
    settings.SECRET_KEY = "test-not-secret"
```
`tests/test_settings_import.py`:
```python
from django.conf import settings


def test_ttsite_installed_and_defaults():
    assert "ttsite" in settings.INSTALLED_APPS
    assert settings.MIDDLEWARE[0] == "ttsite.middleware.TTSiteHostMiddleware"
    assert settings.TTSITE_HOST == "tinytapeout.fpgas.online"
    assert settings.TTSITE_COMMANDER_VERSION == ""
```
Create a placeholder `ttsite/src/ttsite/middleware.py` (Task 3 replaces it) so the settings import works:
```python
class TTSiteHostMiddleware:
    """Replaced by Task 3; placeholder so settings import cleanly."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)
```

- [ ] **Step 5: CI + gitignore + run**

`.gitignore` append `.worktrees/` and `tmp/`. In `.github/workflows/lint.yml` add:
```yaml
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.11"
      - run: uv sync --group dev
      - run: uv run pytest -v
```
Run: `uv sync --group dev && uv run pytest -v && uv run ruff check .`
Expected: 1 passed (a `uv.lock` is created — commit it); ruff clean. `uv sync` installs `fpgas-online-poe` from git (network needed).

- [ ] **Step 6: Commit, PR, CI**

```bash
git add pyproject.toml uv.lock pib/settings.py .gitignore .github/workflows/lint.yml ttsite tests
git commit -F - <<'EOF'
feat(ttsite): app scaffold, settings, pytest-django + CI test job

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UdRpsg6jY8PbcQxxX6txKE
EOF
git push -u origin ttsite-scaffold && gh pr create --base main --fill && gh pr checks --watch
```

---

### Task 2: `Board` model, migration, admin, `ttsite_loadboards`

**Files:**
- Create: `ttsite/src/ttsite/models.py`, `ttsite/src/ttsite/migrations/0001_initial.py` (via `makemigrations`), `ttsite/src/ttsite/admin.py`, `ttsite/src/ttsite/management/__init__.py`, `ttsite/src/ttsite/management/commands/__init__.py`, `ttsite/src/ttsite/management/commands/ttsite_loadboards.py`, `tests/data/tt-boards.yaml`, `tests/test_models.py`, `tests/test_loadboards.py`

**Interfaces:**
- Produces: `ttsite.models.Board` (fields/properties per Global Constraints; `KIND_CHOICES`; `Meta.ordering = ["sort_order", "slug"]`); `Board.asic_slots()` classmethod returning a list of 10 `(n, board_or_None)` for `tt01`..`tt10`; management command `ttsite_loadboards PATH [--prune]`.

- [ ] **Step 1: Tests first** — `tests/data/tt-boards.yaml`:
```yaml
tt_boards:
  - {slug: tt06, port: 6, kind: asic, shuttle: tt06, title: "Tiny Tapeout 6", blurb: "TT06 on a demo board",
     pcb: "TT demo board v3 (RP2040)", links: [{label: "TT06 chip page", url: "https://tinytapeout.com/chips/tt06/"}]}
  - {slug: tt03, port: 3, kind: asic, shuttle: tt03, title: "Tiny Tapeout 3", enabled: false}
  - {slug: kianv-1, port: null, kind: kianv, shuttle: tt06, title: "KianV uLinux SoC (TT06)",
     pmods: [{name: "QSPI Pmod", url: "https://github.com/mole99/qspi-pmod"}]}
  - {slug: fpga-1, port: 12, kind: fpga, title: "TT FPGA emulation board 1", sort_order: 5}
```
`tests/test_models.py`:
```python
import pytest
from ttsite.models import Board


@pytest.mark.django_db
def test_properties_derive_from_switch_and_port():
    b = Board.objects.create(slug="tt06", port=6, kind="asic", shuttle="tt06", title="TT06")
    assert b.hostname == "pi-sw1-p6"
    assert b.ip == "10.21.1.6"
    assert b.stream_url == "/live/pi-sw1-p6.m3u8"
    assert b.serial_ws_path == "/ws/board/tt06/serial"
    assert b.api_base == "/api/board/tt06"
    assert b.live is True


@pytest.mark.django_db
def test_switch_two():
    b = Board.objects.create(slug="x", switch=2, port=7, kind="asic", title="x")
    assert (b.hostname, b.ip) == ("pi-sw2-p7", "10.21.2.7")


@pytest.mark.django_db
def test_unwired_board_is_not_live_and_has_no_address():
    b = Board.objects.create(slug="kianv-1", port=None, kind="kianv", title="KianV")
    assert b.live is False
    with pytest.raises(ValueError, match="no port"):
        _ = b.hostname
    with pytest.raises(ValueError, match="no port"):
        _ = b.ip


@pytest.mark.django_db
def test_disabled_board_is_not_live():
    b = Board.objects.create(slug="tt03", port=3, kind="asic", title="TT03", enabled=False)
    assert b.live is False


@pytest.mark.django_db
def test_asic_slots_are_ten_in_order():
    Board.objects.create(slug="tt06", port=6, kind="asic", shuttle="tt06", title="TT06")
    Board.objects.create(slug="fpga-1", port=12, kind="fpga", title="F")
    slots = Board.asic_slots()
    assert [n for n, _ in slots] == list(range(1, 11))
    assert slots[5][1].slug == "tt06"
    assert all(b is None for n, b in slots if n != 6)


@pytest.mark.django_db
def test_kind_choices_and_str():
    assert dict(Board.KIND_CHOICES).keys() == {"asic", "kianv", "fpga"}
    b = Board.objects.create(slug="tt06", port=6, kind="asic", title="Tiny Tapeout 6")
    assert str(b) == "tt06 (Tiny Tapeout 6)"
```
`tests/test_loadboards.py`:
```python
from pathlib import Path

import pytest
from django.core.management import call_command
from ttsite.models import Board

DATA = Path(__file__).parent / "data" / "tt-boards.yaml"


@pytest.mark.django_db
def test_load_creates_rows():
    call_command("ttsite_loadboards", str(DATA))
    assert set(Board.objects.values_list("slug", flat=True)) == {"tt06", "tt03", "kianv-1", "fpga-1"}
    tt06 = Board.objects.get(slug="tt06")
    assert (tt06.port, tt06.kind, tt06.shuttle, tt06.pcb) == (6, "asic", "tt06", "TT demo board v3 (RP2040)")
    assert tt06.links == [{"label": "TT06 chip page", "url": "https://tinytapeout.com/chips/tt06/"}]
    assert Board.objects.get(slug="tt03").enabled is False
    assert Board.objects.get(slug="kianv-1").port is None
    assert Board.objects.get(slug="fpga-1").sort_order == 5


@pytest.mark.django_db
def test_load_is_idempotent_and_updates():
    call_command("ttsite_loadboards", str(DATA))
    Board.objects.filter(slug="tt06").update(title="stale")
    call_command("ttsite_loadboards", str(DATA))
    assert Board.objects.count() == 4
    assert Board.objects.get(slug="tt06").title == "Tiny Tapeout 6"


@pytest.mark.django_db
def test_prune_removes_missing_only_with_flag(tmp_path):
    call_command("ttsite_loadboards", str(DATA))
    Board.objects.create(slug="gone", port=40, kind="asic", title="gone")
    call_command("ttsite_loadboards", str(DATA))
    assert Board.objects.filter(slug="gone").exists()
    call_command("ttsite_loadboards", str(DATA), "--prune")
    assert not Board.objects.filter(slug="gone").exists()


@pytest.mark.django_db
def test_bad_shape_raises(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- just: list\n")
    with pytest.raises(Exception, match="tt_boards"):
        call_command("ttsite_loadboards", str(p))


@pytest.mark.django_db
def test_unknown_kind_raises(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("tt_boards:\n  - {slug: x, port: 1, kind: banana, title: x}\n")
    with pytest.raises(Exception, match="kind"):
        call_command("ttsite_loadboards", str(p))
```
Run: `uv run pytest tests/test_models.py tests/test_loadboards.py -v` → FAIL (no module `ttsite.models`).

- [ ] **Step 2: Model**

`ttsite/src/ttsite/models.py`:
```python
"""Boards shown on tinytapeout.fpgas.online.

One row per Tiny Tapeout board at Welland. Network identity is DERIVED from
(switch, port) per the VLAN-per-port scheme (pi-sw<s>-p<p> / 10.21.<s>.<p>);
nothing here stores an IP. Demos/designs are not modelled — they are read
live from the Pi daemon.
"""

from django.db import models


class Board(models.Model):
    KIND_CHOICES = [("asic", "TT ASIC"), ("kianv", "KianV RISC-V"), ("fpga", "FPGA emulation")]

    slug = models.SlugField(unique=True)
    switch = models.PositiveSmallIntegerField(default=1)
    port = models.PositiveSmallIntegerField(null=True, blank=True, help_text="s3300 port; empty = not wired yet")
    kind = models.CharField(max_length=8, choices=KIND_CHOICES)
    shuttle = models.CharField(max_length=16, blank=True, help_text="e.g. tt06; blank for FPGA boards")
    title = models.CharField(max_length=80)
    blurb = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True, help_text="Markdown")
    pcb = models.CharField(max_length=80, blank=True)
    pmods = models.JSONField(default=list, blank=True)
    links = models.JSONField(default=list, blank=True)
    enabled = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "slug"]

    def __str__(self):
        return f"{self.slug} ({self.title})"

    # -- derived network identity --
    def _require_port(self):
        if self.port is None:
            raise ValueError(f"board {self.slug} has no port")

    @property
    def hostname(self):
        self._require_port()
        return f"pi-sw{self.switch}-p{self.port}"

    @property
    def ip(self):
        self._require_port()
        return f"10.21.{self.switch}.{self.port}"

    @property
    def stream_url(self):
        return f"/live/{self.hostname}.m3u8"

    @property
    def serial_ws_path(self):
        return f"/ws/board/{self.slug}/serial"

    @property
    def api_base(self):
        return f"/api/board/{self.slug}"

    @property
    def live(self):
        return self.port is not None and self.enabled

    @classmethod
    def asic_slots(cls):
        """Ten fixed slots TT01..TT10, keyed by slug ``tt01``..``tt10``."""
        by_slug = {b.slug: b for b in cls.objects.filter(kind="asic")}
        return [(n, by_slug.get(f"tt{n:02d}")) for n in range(1, 11)]
```
Then `uv run python manage.py makemigrations ttsite` (creates `0001_initial.py`).

`admin.py`:
```python
from django.contrib import admin

from .models import Board


@admin.register(Board)
class BoardAdmin(admin.ModelAdmin):
    list_display = ("slug", "kind", "switch", "port", "shuttle", "title", "enabled", "sort_order")
    list_filter = ("kind", "enabled")
    search_fields = ("slug", "title", "shuttle")
```

- [ ] **Step 3: Loader command**

`ttsite/src/ttsite/management/commands/ttsite_loadboards.py`:
```python
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

    def handle(self, path, prune, **options):
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        if not isinstance(doc, dict) or not isinstance(doc.get("tt_boards"), list):
            raise CommandError(f"{path}: expected a mapping with a 'tt_boards' list")
        seen = set()
        with transaction.atomic():
            for entry in doc["tt_boards"]:
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -v && uv run ruff check .` → all green (incl. migration file existing and `makemigrations --check` clean: `uv run python manage.py makemigrations --check --dry-run`).

- [ ] **Step 5: Commit, PR, CI**

```bash
git worktree add .worktrees/ttsite-model -b ttsite-model main   # (from origin/main after Task 1 merged)
git add ttsite tests
git commit -F - <<'EOF'
feat(ttsite): Board model, admin, ttsite_loadboards command

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UdRpsg6jY8PbcQxxX6txKE
EOF
git push -u origin ttsite-model && gh pr create --base main --fill && gh pr checks --watch
```

---

### Task 3: Host middleware, URLs, views, daemon client, docs data

**Files:**
- Create/replace: `ttsite/src/ttsite/middleware.py`, `ttsite/src/ttsite/urls.py`, `ttsite/src/ttsite/views.py`, `ttsite/src/ttsite/daemon.py`, `ttsite/src/ttsite/docs_links.py`, `tests/test_middleware.py`, `tests/test_daemon.py`, `tests/test_views.py`
- Templates are Task 4; this task's views render minimal placeholder templates created here (`ttsite/templates/ttsite/{index,board,docs}.html` with just the text the tests assert; Task 4 fleshes them out).

**Interfaces:**
- Produces: `ttsite.urls` (`index`, `board`, `board_status`, `docs`, plus includes); views `index(request)`, `board(request, slug)`, `board_status(request, slug)`, `docs(request)`; `daemon.health(board, timeout=3.0)`; `docs_links.SECTIONS` (list of `{"title": str, "links": [{"label","url","note"}]}`).

- [ ] **Step 1: Tests**

`tests/test_middleware.py`:
```python
import pytest
from django.test import Client


@pytest.mark.django_db
def test_tt_host_gets_ttsite_urlconf():
    c = Client(HTTP_HOST="tinytapeout.fpgas.online")
    r = c.get("/")
    assert r.status_code == 200
    assert "Tiny Tapeout" in r.content.decode()


@pytest.mark.django_db
def test_tt_host_with_port():
    c = Client(HTTP_HOST="tinytapeout.fpgas.online:8000")
    assert c.get("/docs/").status_code == 200


@pytest.mark.django_db
def test_other_host_keeps_main_urlconf():
    c = Client(HTTP_HOST="fpgas.online")
    r = c.get("/", follow=False)
    assert r.status_code == 301 and r["Location"] == "/fpgas/"   # main site's RedirectView
    assert c.get("/docs/").status_code == 404
```
`tests/test_daemon.py`:
```python
import pytest
import requests
from ttsite import daemon
from ttsite.models import Board


class FakeResp:
    def __init__(self, status=200, body=None, bad_json=False):
        self.status_code = status
        self._body = body
        self._bad = bad_json

    def json(self):
        if self._bad:
            raise ValueError("bad json")
        return self._body


@pytest.mark.django_db
def test_health_ok(monkeypatch):
    b = Board.objects.create(slug="tt06", port=6, kind="asic", title="t")
    calls = {}

    def fake_get(url, timeout):
        calls["url"], calls["timeout"] = url, timeout
        return FakeResp(200, {"board": {"present": True, "device": "/dev/ttboard", "vid_pid": "2e8a:0005"}, "clients": 1})

    monkeypatch.setattr(daemon.requests, "get", fake_get)
    h = daemon.health(b)
    assert calls["url"] == "http://10.21.1.6:8765/health" and calls["timeout"] == 3.0
    assert h["reachable"] is True and h["board"]["present"] is True


@pytest.mark.django_db
@pytest.mark.parametrize("resp", [FakeResp(500, {}), FakeResp(200, bad_json=True)])
def test_health_bad_response(monkeypatch, resp):
    b = Board.objects.create(slug="tt06", port=6, kind="asic", title="t")
    monkeypatch.setattr(daemon.requests, "get", lambda url, timeout: resp)
    h = daemon.health(b)
    assert h["reachable"] is False and "error" in h


@pytest.mark.django_db
def test_health_connection_error(monkeypatch):
    b = Board.objects.create(slug="tt06", port=6, kind="asic", title="t")

    def boom(url, timeout):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(daemon.requests, "get", boom)
    h = daemon.health(b)
    assert h == {"reachable": False, "error": "refused"}


@pytest.mark.django_db
def test_health_unwired_board():
    b = Board.objects.create(slug="k", port=None, kind="kianv", title="t")
    assert daemon.health(b)["reachable"] is False
```
`tests/test_views.py`:
```python
import pytest
from django.test import Client
from ttsite import daemon
from ttsite.models import Board


@pytest.fixture
def c():
    return Client(HTTP_HOST="tinytapeout.fpgas.online")


@pytest.fixture
def boards(db):
    Board.objects.create(slug="tt06", port=6, kind="asic", shuttle="tt06", title="Tiny Tapeout 6")
    Board.objects.create(slug="tt03", port=3, kind="asic", shuttle="tt03", title="Tiny Tapeout 3", enabled=False)
    Board.objects.create(slug="kianv-1", port=None, kind="kianv", shuttle="tt06", title="KianV uLinux SoC")
    Board.objects.create(slug="fpga-1", port=12, kind="fpga", title="TT FPGA emulation board 1")


def test_index_lists_sections(c, boards):
    html = c.get("/").content.decode()
    assert "Tiny Tapeout 6" in html and "KianV uLinux SoC" in html and "TT FPGA emulation board 1" in html
    assert html.count("coming soon") >= 8          # 10 ASIC slots, tt06 live, tt03 disabled => 9 non-live incl. tt03
    assert "/board/tt06/" in html


def test_board_page_live(c, boards, settings):
    settings.TTSITE_COMMANDER_VERSION = "0.1.0"
    html = c.get("/board/tt06/").content.decode()
    assert "/live/pi-sw1-p6.m3u8" in html
    assert "tt-commander/0.1.0/tt-commander-embed.js" in html
    assert "/ws/board/tt06/serial" in html
    assert "ws/pistat/pi-sw1-p6/" in html and "ws/pistat/pi6/" in html
    assert "tinytapeout.com/chips/tt06/" in html


def test_board_page_without_bundle_shows_notice(c, boards):
    html = c.get("/board/tt06/").content.decode()
    assert "Commander bundle not deployed" in html
    assert "tt-commander-embed.js" not in html


@pytest.mark.parametrize("slug", ["tt03", "kianv-1"])
def test_board_page_coming_soon(c, boards, slug):
    r = c.get(f"/board/{slug}/")
    assert r.status_code == 200
    html = r.content.decode()
    assert "coming soon" in html.lower()
    assert "tt-commander-embed.js" not in html and ".m3u8" not in html


def test_board_404(c, boards):
    assert c.get("/board/nope/").status_code == 404


def test_status_json_proxies_health(c, boards, monkeypatch):
    monkeypatch.setattr(daemon, "health", lambda b, timeout=3.0: {"reachable": True, "board": {"present": True}})
    r = c.get("/board/tt06/status.json")
    assert r.status_code == 200 and r.json()["reachable"] is True


def test_status_json_unreachable_is_200(c, boards, monkeypatch):
    monkeypatch.setattr(daemon, "health", lambda b, timeout=3.0: {"reachable": False, "error": "refused"})
    r = c.get("/board/tt06/status.json")
    assert r.status_code == 200 and r.json() == {"reachable": False, "error": "refused"}


def test_status_json_cached(c, boards, monkeypatch):
    calls = []
    monkeypatch.setattr(daemon, "health", lambda b, timeout=3.0: calls.append(1) or {"reachable": True})
    c.get("/board/tt06/status.json")
    c.get("/board/tt06/status.json")
    assert len(calls) == 1


def test_docs_page(c):
    html = c.get("/docs/").content.decode()
    assert "tinytapeout.com/guides/get-started-demoboard" in html
    assert "tt-micropython-firmware" in html
```
Run → fail (no urls/views).

- [ ] **Step 2: Middleware**

`ttsite/src/ttsite/middleware.py`:
```python
"""Serve ttsite.urls on the tinytapeout.fpgas.online host; leave every other host alone."""

from django.conf import settings


class TTSiteHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":", 1)[0].lower()
        if host == settings.TTSITE_HOST.lower():
            request.urlconf = "ttsite.urls"
        return self.get_response(request)
```

- [ ] **Step 3: daemon client + docs data**

`ttsite/src/ttsite/daemon.py`:
```python
"""Tiny HTTP client for the Pi-side fpgas-tt daemon (port 8765). Never raises into views."""

import requests

DAEMON_PORT = 8765


def health(board, timeout=3.0):
    if board.port is None:
        return {"reachable": False, "error": "board is not wired to a port"}
    url = f"http://{board.ip}:{DAEMON_PORT}/health"
    try:
        resp = requests.get(url, timeout=timeout)
    except requests.RequestException as exc:
        return {"reachable": False, "error": str(exc)}
    if resp.status_code != 200:
        return {"reachable": False, "error": f"HTTP {resp.status_code} from {url}"}
    try:
        data = resp.json()
    except ValueError as exc:
        return {"reachable": False, "error": f"invalid JSON from {url}: {exc}"}
    data["reachable"] = True
    return data
```
`ttsite/src/ttsite/docs_links.py`:
```python
"""Curated Tiny Tapeout documentation index shown at /docs/ and linked from board pages."""

SECTIONS = [
    {"title": "Getting started", "links": [
        {"label": "Tiny Tapeout", "url": "https://tinytapeout.com/", "note": "the project"},
        {"label": "Demo board guide", "url": "https://tinytapeout.com/guides/get-started-demoboard/", "note": "the board you are driving"},
        {"label": "Commander app (upstream)", "url": "https://github.com/TinyTapeout/tt-commander-app", "note": "what this page embeds"},
        {"label": "Commander app (fpgas.online fork)", "url": "https://github.com/fpgas-online/tt-commander-app", "note": "web-transport fork"},
    ]},
    {"title": "Specs", "links": [
        {"label": "Tech specs", "url": "https://tinytapeout.com/specs/", "note": ""},
        {"label": "Pinouts & PMODs", "url": "https://tinytapeout.com/specs/pinouts/", "note": ""},
        {"label": "MicroPython SDK / firmware", "url": "https://github.com/TinyTapeout/tt-micropython-firmware", "note": "the REPL you see in the terminal tab"},
        {"label": "Demo board PCB", "url": "https://github.com/TinyTapeout/tt-demo-pcb", "note": ""},
    ]},
    {"title": "FPGA emulation", "links": [
        {"label": "FPGA breakout guide", "url": "https://tinytapeout.com/guides/fpga-breakout/", "note": "iCE40UP5K ASIC simulator"},
        {"label": "tt-support-tools", "url": "https://github.com/TinyTapeout/tt-support-tools", "note": "tt_fpga.py harden / configure"},
        {"label": "Breakout PCB", "url": "https://github.com/TinyTapeout/breakout-pcb", "note": ""},
    ]},
    {"title": "Chips", "links": [
        {"label": f"TT{n:02d}", "url": f"https://tinytapeout.com/chips/tt{n:02d}/", "note": ""} for n in range(1, 11)
    ]},
    {"title": "KianV", "links": [
        {"label": "KianV uLinux SoC (TT06)", "url": "https://tinytapeout.com/chips/tt06/tt_um_kianV_rv32ima_uLinux_SoC/", "note": ""},
        {"label": "kianRiscV", "url": "https://github.com/splinedrive/kianRiscV", "note": "source + Linux images"},
        {"label": "QSPI Pmod", "url": "https://github.com/mole99/qspi-pmod", "note": "flash + PSRAM the SoC needs"},
    ]},
    {"title": "This instance", "links": [
        {"label": "fpgas.online", "url": "https://fpgas.online/", "note": "the general FPGA boards"},
        {"label": "Design spec", "url": "https://github.com/fpgas-online/fpgas.online-infra/blob/main/docs/superpowers/specs/2026-08-22-tinytapeout-fpgas-online-design.md", "note": ""},
        {"label": "Source: fpgas.online-site", "url": "https://github.com/fpgas-online/fpgas.online-site", "note": ""},
    ]},
]
```

- [ ] **Step 4: urls + views + placeholder templates**

`ttsite/src/ttsite/urls.py`:
```python
from django.contrib import admin
from django.urls import include, path

from . import views

urlpatterns = [
    path("", views.index, name="ttsite-index"),
    path("board/<slug:slug>/", views.board, name="ttsite-board"),
    path("board/<slug:slug>/status.json", views.board_status, name="ttsite-board-status"),
    path("docs/", views.docs, name="ttsite-docs"),
    # existing apps keep working on this host
    path("admin/", admin.site.urls),
    path("snmp/", include("snmp_switch.urls")),
    path("pistat/", include("pistat.urls")),
    path("pibup/", include("pibup.urls")),
]
```
`ttsite/src/ttsite/views.py`:
```python
from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render

from . import daemon
from .docs_links import SECTIONS
from .models import Board

STATUS_CACHE_SECONDS = 5


def _common(request):
    return {
        "TTSITE_HOST": settings.TTSITE_HOST,
        "COMMANDER_VERSION": settings.TTSITE_COMMANDER_VERSION,
        "STATIC_URL": settings.STATIC_URL,
    }


def index(request):
    ctx = _common(request)
    ctx.update(
        asic_slots=Board.asic_slots(),
        kianv_boards=Board.objects.filter(kind="kianv"),
        fpga_boards=Board.objects.filter(kind="fpga"),
    )
    return render(request, "ttsite/index.html", ctx)


def board(request, slug):
    b = get_object_or_404(Board, slug=slug)
    ctx = _common(request)
    ctx.update(
        board=b,
        live=b.live,
        shuttle_url=f"https://tinytapeout.com/chips/{b.shuttle}/" if b.shuttle else "",
        pistat_groups=[b.hostname, f"pi{b.port}"] if b.live else [],
    )
    return render(request, "ttsite/board.html", ctx)


def board_status(request, slug):
    b = get_object_or_404(Board, slug=slug)
    key = f"ttsite:health:{b.slug}"
    data = cache.get(key)
    if data is None:
        data = daemon.health(b)
        cache.set(key, data, STATUS_CACHE_SECONDS)
    return JsonResponse(data)


def docs(request):
    ctx = _common(request)
    ctx.update(sections=SECTIONS)
    return render(request, "ttsite/docs.html", ctx)
```
Placeholder templates (Task 4 replaces them; keep the assertions true):
- `index.html`: `<h1>Tiny Tapeout boards at fpgas.online</h1>` then loop `asic_slots` printing `{{ b.title }}` + link `/board/{{ b.slug }}/` when live else `coming soon`; loop kianv/fpga boards similarly.
- `board.html`: title; if not `live` print `coming soon`; if live print `{{ board.stream_url }}`, `{{ board.serial_ws_path }}`, `ws/pistat/{{ g }}/` for each group, the shuttle link, and either the `<script type="module" src="{{ STATIC_URL }}tt-commander/{{ COMMANDER_VERSION }}/tt-commander-embed.js">` line or the text `Commander bundle not deployed`.
- `docs.html`: sections with links.

- [ ] **Step 5: Run tests, lint** → green. Note Django's test cache is locmem by default; `test_status_json_cached` relies on it — if a `CACHES` setting exists in `local_settings` it is not loaded in tests.

- [ ] **Step 6: Commit, PR, CI**

```bash
git worktree add .worktrees/ttsite-views -b ttsite-views main
git add ttsite tests
git commit -F - <<'EOF'
feat(ttsite): host middleware, urls, views, daemon health client, docs index

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UdRpsg6jY8PbcQxxX6txKE
EOF
git push -u origin ttsite-views && gh pr create --base main --fill && gh pr checks --watch
```

---

### Task 4: Templates, theme, board.js

**Files:**
- Replace: `ttsite/src/ttsite/templates/ttsite/{base,index,board,docs}.html`
- Create: `ttsite/src/ttsite/static/ttsite/ttsite.css`, `ttsite/src/ttsite/static/ttsite/board.js`, `ttsite/src/ttsite/static/ttsite/ttlogo_400.png`, `ttsite/src/ttsite/static/ttsite/README.md`
- Modify: `tests/test_views.py` (add assertions for nav/footer/status pill/power-cycle), `tests/test_static.py` (new)

**Interfaces:**
- Consumes: view contexts from Task 3. Produces: the visual site; `board.js` exposes `window.ttsiteBoard = { mount(cfg) }` and reads config from `<div id="ttsite-board" data-*>` attributes: `data-slug`, `data-kind`, `data-shuttle`, `data-ws-path`, `data-api-base`, `data-status-url`, `data-port`, `data-pistat-groups` (space-separated), `data-commander-js`/`data-commander-css` (may be empty).

- [ ] **Step 1: Assets**

```bash
curl -fsSL -o ttsite/src/ttsite/static/ttsite/ttlogo_400.png https://tinytapeout.com/ttlogo_400.png
file ttsite/src/ttsite/static/ttsite/ttlogo_400.png   # must say PNG image data
```
`static/ttsite/README.md`:
```markdown
# ttsite static assets

- `ttlogo_400.png` — Tiny Tapeout logo, downloaded 2026-08-22 from https://tinytapeout.com/ttlogo_400.png.
  Used to identify the Tiny Tapeout project on a community-run hardware instance, with attribution and a
  "not operated by Tiny Tapeout Ltd" disclaimer in the footer. The Tiny Tapeout team is being notified
  (spec §13 open item 3). Replace with an official asset/permission note when received.
- `ttsite.css`, `board.js` — Apache-2.0, this repo.
```
If the download 404s, try `https://tinytapeout.com/images/ttlogo_400.png` or fetch the logo URL from the homepage HTML (`curl -s https://tinytapeout.com/ | grep -o 'src="[^"]*ttlogo[^"]*"'`); record the actual URL in the README.

- [ ] **Step 2: Tests to add**

Append to `tests/test_views.py`:
```python
def test_chrome_nav_and_footer(c, boards):
    html = c.get("/").content.decode()
    for item in ["Boards", "ASIC", "KianV", "FPGA", "Docs", "tinytapeout.com"]:
        assert item in html
    assert "not operated by Tiny Tapeout Ltd" in html
    assert "ttsite/ttlogo_400.png" in html
    assert "#544ead" in (c.get("/static/ttsite/ttsite.css").content.decode() if False else open(
        "ttsite/src/ttsite/static/ttsite/ttsite.css").read())


def test_board_page_data_attributes(c, boards, settings):
    settings.TTSITE_COMMANDER_VERSION = "0.1.0"
    html = c.get("/board/tt06/").content.decode()
    assert 'id="ttsite-board"' in html
    assert 'data-slug="tt06"' in html and 'data-kind="asic"' in html and 'data-port="6"' in html
    assert 'data-ws-path="/ws/board/tt06/serial"' in html
    assert 'data-status-url="/board/tt06/status.json"' in html
    assert 'data-pistat-groups="pi-sw1-p6 pi6"' in html
    assert "Power-cycle board" in html
    assert "ttsite/board.js" in html
```
`tests/test_static.py`:
```python
from pathlib import Path

STATIC = Path("ttsite/src/ttsite/static/ttsite")


def test_assets_present():
    assert (STATIC / "ttlogo_400.png").stat().st_size > 1000
    assert (STATIC / "ttlogo_400.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (STATIC / "README.md").exists()


def test_board_js_reads_data_attributes_and_mounts():
    js = (STATIC / "board.js").read_text()
    for attr in ["data-slug", "data-kind", "data-ws-path", "data-status-url", "data-pistat-groups", "data-commander-js"]:
        assert attr.replace("data-", "").replace("-", "") in js.replace("-", "").replace("_", "")  # dataset access
    assert "mountCommander" in js
    assert "/snmp/toggle" in js
    assert "ws/pistat/" in js
```
(Run → fail.)

- [ ] **Step 3: `base.html`**

```html
{% load static %}<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Tiny Tapeout boards at fpgas.online{% endblock %}</title>
  <link rel="icon" href="{% static 'ttsite/ttlogo_400.png' %}">
  <link href="https://fonts.googleapis.com/css?family=Roboto:300,400,500,700" rel="stylesheet">
  <link rel="stylesheet" href="{% static 'ttsite/ttsite.css' %}">
  {% block head %}{% endblock %}
</head>
<body>
<header class="tt-header">
  <a class="tt-brand" href="/"><img src="{% static 'ttsite/ttlogo_400.png' %}" alt="Tiny Tapeout logo"><span>Tiny Tapeout <em>at fpgas.online</em></span></a>
  <nav class="tt-nav">
    <a href="/">Boards</a>
    <a href="/#asic">ASIC</a>
    <a href="/#kianv">KianV</a>
    <a href="/#fpga">FPGA</a>
    <a href="/docs/">Docs</a>
    <a href="https://tinytapeout.com/" target="_blank" rel="noopener">tinytapeout.com ↗</a>
  </nav>
</header>
<main class="tt-main">{% block content %}{% endblock %}</main>
<footer class="tt-footer">
  <p>A community-run Tiny Tapeout hardware instance hosted by fpgas.online at Welland, South Australia — not operated by Tiny Tapeout Ltd.
     <a href="https://github.com/fpgas-online/fpgas.online-site">Source on GitHub</a> ·
     <a href="https://fpgas.online/">fpgas.online</a></p>
</footer>
{% block scripts %}{% endblock %}
</body>
</html>
```

- [ ] **Step 4: `index.html`**

Hero (title, one paragraph: what this is, "anyone can drive these boards — no login, be nice, others may be driving too"), then:
```html
<section id="asic"><h2>Tiny Tapeout ASIC boards (TT01–TT10)</h2>
<div class="tt-grid">
{% for n, b in asic_slots %}
  <article class="tt-card {% if not b or not b.live %}tt-card--soon{% endif %}">
    <h3>TT{{ n|stringformat:"02d" }}{% if b %} · {{ b.title }}{% endif %}</h3>
    {% if b and b.live %}
      <video class="video-js tt-thumb" muted autoplay playsinline preload="none" data-setup='{"fluid":true}'>
        <source src="{{ b.stream_url }}" type="application/x-mpegURL"></video>
      <p>{{ b.blurb }}</p>
      <a class="tt-btn" href="/board/{{ b.slug }}/">Use this board</a>
    {% else %}
      <p class="tt-soon">coming soon</p>
      <a href="https://tinytapeout.com/chips/tt{{ n|stringformat:'02d' }}/" target="_blank" rel="noopener">TT{{ n|stringformat:"02d" }} chip page ↗</a>
    {% endif %}
  </article>
{% endfor %}
</div></section>
<section id="kianv"><h2>KianV RISC-V boards</h2> …same card loop over kianv_boards…</section>
<section id="fpga"><h2>FPGA emulation boards</h2> …same card loop over fpga_boards…</section>
<section id="how"><h2>How this works</h2><p>Each board sits on a Raspberry Pi with a camera. The Commander you see is the
<a href="https://github.com/fpgas-online/tt-commander-app">fpgas.online fork</a> of Tiny Tapeout's Commander app, talking
to the board's RP2040 over a WebSocket bridge. <a href="/docs/">Docs →</a></p></section>
```
Include video.js in `{% block head %}` and lazy-init in `{% block scripts %}`. **All external `<script>`/`<link>` tags (video.js CDN, Google Fonts stylesheet) carry Subresource Integrity**: compute once with `curl -fsSL <url> | openssl dgst -sha384 -binary | openssl base64 -A` and write `integrity="sha384-…" crossorigin="anonymous"` on the tag (pin the exact version URL `https://vjs.zencdn.net/8.4.0/video.min.js` / `video-js.min.css`; Google Fonts CSS is dynamic per UA so it gets `crossorigin="anonymous"` only, no integrity). Put the tags in `base.html`'s head block once (a `{% block head %}` override in `board.html`/`index.html` must keep the same attributes) and add a test in `tests/test_views.py` asserting every `vjs.zencdn.net` tag in the rendered board page contains `integrity="sha384-`.

- [ ] **Step 5: `board.html`**

```html
{% extends "ttsite/base.html" %}{% load static %}
{% block title %}{{ board.title }} · Tiny Tapeout at fpgas.online{% endblock %}
{% block head %}
  <link href="https://vjs.zencdn.net/8.4.0/video-js.min.css" rel="stylesheet">
  <script src="https://vjs.zencdn.net/8.4.0/video.min.js"></script>
  {% if live and COMMANDER_VERSION %}<link rel="stylesheet" href="{{ STATIC_URL }}tt-commander/{{ COMMANDER_VERSION }}/tt-commander-embed.css">{% endif %}
{% endblock %}
{% block content %}
<div id="ttsite-board"
     data-slug="{{ board.slug }}" data-kind="{{ board.kind }}" data-shuttle="{{ board.shuttle }}"
     data-port="{{ board.port|default_if_none:'' }}"
     data-ws-path="{% if live %}{{ board.serial_ws_path }}{% endif %}" data-api-base="{{ board.api_base }}"
     data-status-url="/board/{{ board.slug }}/status.json"
     data-pistat-groups="{{ pistat_groups|join:' ' }}"
     data-commander-js="{% if live and COMMANDER_VERSION %}{{ STATIC_URL }}tt-commander/{{ COMMANDER_VERSION }}/tt-commander-embed.js{% endif %}">
  <header class="tt-board-head">
    <h1>{{ board.title }} <span class="tt-kind tt-kind--{{ board.kind }}">{{ board.get_kind_display }}</span></h1>
    {% if shuttle_url %}<a href="{{ shuttle_url }}" target="_blank" rel="noopener">{{ board.shuttle }} chip page ↗</a>{% endif %}
    <span id="tt-status" class="tt-pill tt-pill--unknown">status…</span>
    {% if live %}<button id="tt-power" class="tt-btn tt-btn--warn" title="Toggle PoE on s3300 port {{ board.port }}">Power-cycle board</button>
    <button id="tt-video-reset" class="tt-btn">Reset video</button>{% endif %}
  </header>
  {% if not live %}
    <p class="tt-soon-banner">This board is <strong>coming soon</strong> — not wired up yet. Meanwhile, read about it below.</p>
  {% else %}
  <div class="tt-board-grid">
    <section class="tt-camera"><video id="tt-video" class="video-js" controls autoplay muted playsinline preload="auto" data-setup="{}">
      <source src="{{ board.stream_url }}" type="application/x-mpegURL"></video></section>
    <section class="tt-commander">
      {% if COMMANDER_VERSION %}<div id="tt-commander"></div>
      {% else %}<p class="tt-notice">Commander bundle not deployed on this server yet (TTSITE_COMMANDER_VERSION is empty).</p>{% endif %}
    </section>
  </div>
  {% endif %}
  <section class="tt-info">
    <h2>About this board</h2>
    {% if board.description %}<div class="tt-md">{{ board.description|linebreaks }}</div>{% endif %}
    <dl>{% if board.pcb %}<dt>PCB</dt><dd>{{ board.pcb }}</dd>{% endif %}
        {% if board.pmods %}<dt>PMODs fitted</dt><dd>{% for p in board.pmods %}<a href="{{ p.url }}">{{ p.name }}</a>{% if p.note %} — {{ p.note }}{% endif %}{% if not forloop.last %}, {% endif %}{% endfor %}</dd>{% endif %}
        {% if live %}<dt>Where</dt><dd>Welland, switch {{ board.switch }} port {{ board.port }} ({{ board.hostname }})</dd>{% endif %}</dl>
    <ul class="tt-links">
      {% for l in board.links %}<li><a href="{{ l.url }}" target="_blank" rel="noopener">{{ l.label }} ↗</a></li>{% endfor %}
      <li><a href="https://tinytapeout.com/guides/get-started-demoboard/" target="_blank" rel="noopener">Demo board guide ↗</a></li>
      <li><a href="https://github.com/TinyTapeout/tt-micropython-firmware" target="_blank" rel="noopener">MicroPython SDK docs ↗</a></li>
      <li><a href="https://tinytapeout.com/specs/pinouts/" target="_blank" rel="noopener">Pinouts ↗</a></li>
      <li><a href="/docs/">All docs →</a></li>
    </ul>
    {% if board.kind == "kianv" %}<p class="tt-notice">KianV boards carry a QSPI flash/PSRAM PMOD and boot Linux on the chip; the one-click boot demo arrives in a later phase.</p>{% endif %}
    {% if board.kind == "fpga" %}<p class="tt-notice">FPGA emulation boards run Tiny Tapeout designs on an iCE40UP5K; the demo gallery and bitstream upload arrive in a later phase.</p>{% endif %}
  </section>
  {% if live %}<section class="tt-log"><h2>Status log</h2><textarea id="tt-log" rows="6" readonly></textarea></section>{% endif %}
</div>
{% endblock %}
{% block scripts %}<script type="module" src="{% static 'ttsite/board.js' %}"></script>{% endblock %}
```
(`get_kind_display` comes from `choices`.) The `Power-cycle board` text and the `tt-commander-embed.js` path must appear exactly as the tests assert.

- [ ] **Step 6: `board.js`**

```js
// SPDX-License-Identifier: Apache-2.0
// board page glue: mount Commander, status pill, pistat log, power-cycle.
const root = document.getElementById('ttsite-board');
if (root) {
  const d = root.dataset;
  const log = document.getElementById('tt-log');
  const appendLog = (t) => { if (!log) return; log.value += new Date().toLocaleTimeString() + ': ' + t + '\n'; log.scrollTop = log.scrollHeight; };

  // status pill from /board/<slug>/status.json (5 s server cache; poll every 10 s)
  const pill = document.getElementById('tt-status');
  async function refreshStatus() {
    try {
      const r = await fetch(d.statusUrl, { cache: 'no-store' });
      const s = await r.json();
      let cls = 'tt-pill--offline', text = 'Pi offline';
      if (s.reachable && s.board && s.board.present) { cls = 'tt-pill--ok'; text = 'board present' + (s.clients ? ` · ${s.clients} viewer${s.clients === 1 ? '' : 's'}` : ''); }
      else if (s.reachable) { cls = 'tt-pill--noboard'; text = 'board not detected'; }
      if (s.config_error) text += ' · config error';
      pill.className = 'tt-pill ' + cls; pill.textContent = text; pill.title = s.error || '';
    } catch (e) { pill.className = 'tt-pill tt-pill--unknown'; pill.textContent = 'status unavailable'; }
  }
  if (pill && d.statusUrl) { refreshStatus(); setInterval(refreshStatus, 10000); }

  // pistat status log: subscribe to every group the board is known by
  (d.pistatGroups || '').split(' ').filter(Boolean).forEach((g) => {
    const url = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/pistat/' + g + '/';
    const connect = () => {
      const ws = new WebSocket(url);
      ws.onmessage = (e) => { try { appendLog(JSON.parse(e.data).message); } catch { appendLog(e.data); } };
      ws.onclose = () => setTimeout(connect, 5000);
    };
    connect();
  });

  // power-cycle via the existing snmp_switch toggle endpoint
  const power = document.getElementById('tt-power');
  if (power && d.port) power.onclick = async () => {
    if (!confirm('Power-cycle this board? Anyone else using it will be interrupted.')) return;
    appendLog('power-cycle requested');
    await fetch('/snmp/toggle', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ port: Number(d.port) }) });
  };
  const vreset = document.getElementById('tt-video-reset');
  if (vreset) vreset.onclick = () => { const v = document.getElementById('tt-video'); if (v && v.player) v.player.load(); };

  // Commander embed
  const mountEl = document.getElementById('tt-commander');
  if (mountEl && d.commanderJs && d.wsPath) {
    import(d.commanderJs).then(({ mountCommander }) => {
      mountCommander(mountEl, {
        transport: { kind: 'websocket', url: (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + d.wsPath },
        board: { slug: d.slug, kind: d.kind, shuttle: d.shuttle || undefined },
        apiBase: d.apiBase,
      });
      appendLog('Commander mounted');
    }).catch((e) => { mountEl.textContent = 'Could not load the Commander bundle: ' + e; });
  }
}
```

- [ ] **Step 7: `ttsite.css`** — CSS variables `--tt-primary: #544ead; --tt-secondary: #8afbfd; --tt-ink: #1c1b2e; --tt-paper: #fff;` header bar in primary with white text, nav links, cards grid (`grid-template-columns: repeat(auto-fill, minmax(220px, 1fr))`), `.tt-card--soon` muted, `.tt-board-grid` two columns (camera 2fr / commander 3fr) collapsing under 900 px, pills (`--ok` green, `--noboard` red, `--offline` grey, `--unknown` light), buttons in primary, warn button amber, footer light grey with the disclaimer; Roboto everywhere. Keep it ~120 lines; no framework.

- [ ] **Step 8: `docs.html`** — sections from `sections` with label/url/note; intro paragraph.

- [ ] **Step 9: Run, lint, commit, PR**

Run: `uv run pytest -v && uv run ruff check .` → green. Also `uv run python manage.py check` and render a quick local sanity (`uv run python manage.py runserver` + `curl -H 'Host: tinytapeout.fpgas.online' localhost:8000/` is optional).
```bash
git worktree add .worktrees/ttsite-theme -b ttsite-theme main
git add ttsite tests
git commit -F - <<'EOF'
feat(ttsite): TT-branded templates, theme CSS, board page glue (commander mount, status, log, power-cycle)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UdRpsg6jY8PbcQxxX6txKE
EOF
git push -u origin ttsite-theme && gh pr create --base main --fill && gh pr checks --watch
```

---

### Task 5: pistat hostname routing fix, docs

**Files:**
- Modify: `pistat/src/pistat/routing.py` (`(?P<pi_name>[\w-]+)`), `pistat/src/pistat/urls.py` (both regexes → `[\w-]+`), `README.md` (Django Apps table row + host note), `CLAUDE.md` (app row)
- Create: `tests/test_pistat_routing.py`

- [ ] **Step 1: Test**
```python
import pytest
from channels.routing import URLRouter
from django.test import Client
from django.urls import Resolver404, resolve

from pistat.routing import websocket_urlpatterns


def test_ws_route_accepts_hyphenated_hostnames():
    router = URLRouter(websocket_urlpatterns)
    assert router.routes[0].pattern.match("ws/pistat/pi-sw1-p6/") is not None
    assert router.routes[0].pattern.match("ws/pistat/pi6/") is not None


@pytest.mark.django_db
def test_http_stat_accepts_hyphenated_hostnames():
    c = Client(HTTP_HOST="fpgas.online")
    r = c.post("/pistat/stat/pi-sw1-p6/cam/")
    assert r.status_code == 200
```
(Run → the first fails before the fix; the second 404s.)

- [ ] **Step 2: Fix** — `routing.py`: `re_path(r"ws/pistat/(?P<pi_name>[\w-]+)/$", …)`; `urls.py`: `re_path(r'stat/(?P<pi_name>[\w-]+)/(?P<status>\w+)', status)`, `re_path(r'ping/(?P<pi_name>[\w-]+)', ping)`. (`ping` still assumes the legacy `pi<N>` numbering for the IP — leave it; note in README.)

- [ ] **Step 3: Docs** — README "Django Apps" table: `| ttsite | tinytapeout.fpgas.online: TT board catalogue, board pages embedding the Commander fork, docs |`; add a "Hosts" paragraph: main site on `fpgas.online`, `TTSITE_HOST` served by `ttsite.urls` via middleware; `ttsite_loadboards` usage; `TTSITE_COMMANDER_VERSION`. CLAUDE.md app list + "ttsite" conventions (tests via `uv run pytest`).

- [ ] **Step 4: Run, commit, PR**
```bash
git worktree add .worktrees/pistat-hostnames -b pistat-hostnames main
uv run pytest -v && uv run ruff check .
git add pistat tests README.md CLAUDE.md
git commit -F - <<'EOF'
fix(pistat): accept hyphenated Pi hostnames; document ttsite

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UdRpsg6jY8PbcQxxX6txKE
EOF
git push -u origin pistat-hostnames && gh pr create --base main --fill && gh pr checks --watch
```

---

## Self-review against the spec

- §6.1 model + properties ✔ (Task 2); §6.2 seeding ✔ (Task 2); §6.3 host routing ✔ (Task 3); §6.4 URLs: `/`, `/board/<slug>/`, `status.json`, `/docs/` ✔ — `/api/...` proxies deferred to phases 2/3 by design (daemon client lands now); §6.5 composition ✔ (Task 4: header strip, camera, Commander mount, info card with auto-links, kind extras as notices, status log); §6.6 theme ✔ (Task 4); §6.7 tests ✔ (Tasks 1–5, CI job in Task 1). §9 errors: daemon failures → `reachable:false` + pill text; missing bundle → notice; coming-soon variant ✔. pistat hostname regex fix is required for the VLAN hostnames (discovered during planning) ✔ (Task 5).
- Placeholders: none. Types consistent: `Board` fields/properties used identically in views/templates/tests; `daemon.health` signature `(board, timeout=3.0)` matches the monkeypatch lambdas; data attributes in `board.html` match `board.js` dataset names (`statusUrl`, `pistatGroups`, `commanderJs`, `wsPath`, `apiBase`) and the tests' attribute strings.
