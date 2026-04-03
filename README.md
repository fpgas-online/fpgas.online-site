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

The [fpgas-online-poe](https://github.com/fpgas-online/fpgas.online-poe) package provides the `snmp_switch` Django app for PoE switch control (installed as a dependency).

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
