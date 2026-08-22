# fpgas.online-site

Django web application for the [fpgas.online](https://fpgas.online) FPGA-as-a-Service platform.

## Overview

This is the web frontend that lets users interact with remote FPGA boards. It provides real-time board status, demo execution, file uploads, and PoE switch management through a browser interface.

## Django Apps

| App | Purpose |
|-----|---------|
| `pibfpgas` | FPGA board listing, demo management, board models |
| `pistat` | Real-time Pi status via WebSocket (board detection, camera, SSH) |
| `pibdemos` | Demo execution on FPGA boards via SSH |
| `pibup` | Bitstream and file upload to Pi boards |
| `ttsite` | TinyTapeout board catalogue, board pages, Commander fork embedding |

The [fpgas-online-poe](https://github.com/fpgas-online/fpgas.online-poe) package provides the `snmp_switch` Django app for PoE switch control (installed as a dependency).

## Hosts

The main application is served on `fpgas.online`. The Tiny Tapeout catalogue (`ttsite`) is served on the host named by the `TTSITE_HOST` Django setting -- it defaults to `tinytapeout.fpgas.online` in `pib/settings.py` and can be overridden in `pib/local_settings.py`. Routing is done by `ttsite.middleware.TTSiteHostMiddleware`, which compares the HTTP Host header against `TTSITE_HOST` and, on a match, points `request.urlconf` at `ttsite.urls`. Every other host keeps the project urlconf untouched.

Boards are seeded from `/etc/fpgas-online/tt-boards.yaml`, which is rendered on the host by the [fpgas.online-infra](https://github.com/fpgas-online/fpgas.online-infra) Ansible `site` role:

```bash
uv run python manage.py ttsite_loadboards /etc/fpgas-online/tt-boards.yaml --prune
# on the deploy host:
/srv/www/pib/venv/bin/python manage.py ttsite_loadboards /etc/fpgas-online/tt-boards.yaml --prune
```

`--prune` deletes rows whose slug is absent from the file; it refuses to run against an empty `tt_boards` list unless `--allow-empty` is also given.

The `TTSITE_COMMANDER_VERSION` setting (also in `pib/settings.py`, overridable in `local_settings.py`) pins the embedded [Commander fork](https://github.com/fpgas-online/tt-commander-app) bundle version under `STATIC_URL`; when it is empty the board pages show a "bundle not deployed" notice instead of the Commander embed.

Note: The `pistat` app's `ping` view still assumes the legacy `pi<N>` numbering scheme for resolving Pi IP addresses and does not yet support the new hyphenated hostname scheme.

## Tech Stack

- Django 4.2+
- Django Channels with Daphne (ASGI, WebSocket support)
- Redis (channel layer backend)
- nginx (reverse proxy)
- gunicorn + uvicorn (WSGI/ASGI workers)

## Installation

```bash
pip install git+https://github.com/fpgas-online/fpgas.online-site.git
```

Or for development:

```bash
git clone git@github.com:fpgas-online/fpgas.online-site.git
cd fpgas.online-site
pip install -e .
```

## Deployment

This package is deployed by the [fpgas.online-infra](https://github.com/fpgas-online/fpgas.online-infra) Ansible `site` role, which:

1. Installs `fpgas-online-site` and `fpgas-online-poe[cli]` into a virtualenv
2. Runs `manage.py migrate` and `collectstatic`
3. Configures nginx, gunicorn, daphne, and uvicorn systemd services
4. Sets up SSL via Let's Encrypt

The app runs at `/srv/www/pib/` on the server (tweed).

The wheel ships the `pib` project package (`settings.py`, `urls.py`, `asgi.py`, `asgi.base.py`) alongside the five Django apps; infra copies `settings.py`/`urls.py`/`asgi.py` into `/srv/www/pib/pib/` next to the Ansible-written `local_settings.py`. `pib/local_settings.py` is gitignored and is never packaged -- it only ever exists on the deploy host. `tests/` is excluded from the wheel.

## Directory Structure

```
pib/                    Django project (settings, urls, asgi)
pibfpgas/src/pibfpgas/  FPGA board management app
pistat/src/pistat/      Real-time status app (WebSocket consumers)
pibdemos/src/pibdemos/  Demo execution app
pibup/src/pibup/        File upload app
manage.py               Django management script
pyproject.toml          Package configuration
```

## Linting

- **ruff**: blocking

## Related Repos

- [fpgas.online-poe](https://github.com/fpgas-online/fpgas.online-poe) -- PoE switch management (dependency)
- [fpgas.online-infra](https://github.com/fpgas-online/fpgas.online-infra) -- Ansible deployment
- [fpgas.online-setup-pi](https://github.com/fpgas-online/fpgas.online-setup-pi) -- Pi-side status reporter scripts

## License

Apache 2.0
