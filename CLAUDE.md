## Background

This repo is part of the [fpgas.online](https://fpgas.online) FPGA-as-a-Service platform.
The platform provides remote access to real FPGA boards (Arty A7, NeTV2, Fomu, TinyTapeout)
via PoE-powered Raspberry Pis that are network-booted. There are two deployment sites:
Welland (private test lab in South Australia) and PS1 hackerspace (public service in Chicago).

This codebase was extracted from the original monorepo [`carlfk/pici`](https://github.com/CarlFK/pici)
in April 2026 using `git filter-repo` to preserve commit history. The monorepo was split into
purpose-specific repos under the `fpgas-online` GitHub organization, where each repo produces
installable artifacts (pip packages or deb packages) consumed by the infrastructure repo.

## Repository Overview

Django web application serving the fpgas.online platform. Provides real-time FPGA board
status, demo execution, file uploads, and PoE switch management through a browser.

### Django Apps

- `pibfpgas` -- FPGA board listing, demo management, database models
- `pistat` -- Real-time Pi status via WebSocket (Django Channels/Daphne)
- `pibdemos` -- Demo execution on FPGA boards via SSH to Pis
- `pibup` -- Bitstream and file upload to Pi boards

The `snmp_switch` app (PoE switch control) is in the separate `fpgas-online-poe` package.

### App Layout

Each Django app uses a `src/` layout: e.g., `pibfpgas/src/pibfpgas/`. The top-level
`pyproject.toml` installs all apps as a single `fpgas-online-site` package.

### Deployment

Deployed at `/srv/www/pib/` on tweed by the infra repo's `site` ansible role.
Runs behind nginx with gunicorn (WSGI), daphne (ASGI/WebSocket), and uvicorn workers.
Redis provides the Django Channels layer backend.

### Pi-Side Scripts

The pistat reporting scripts (send.py, send_stat.py, etc.) that run ON the Raspberry Pis
are NOT in this repo -- they were moved to `fpgas.online-setup-pi`.

## Conventions

- **Python**: Use `uv` for all Python commands (`uv run`, `uv pip`). Never use bare `python` or `pip`.
- **Dates**: Use ISO 8601 (YYYY-MM-DD) or day-first formats. Never American-style month-first dates.
- **Commits**: Make small, discrete commits. Each logical unit of work gets its own commit.
- **License**: Apache 2.0.
- **Linting**: All repos have CI lint workflows. Fix lint errors before pushing.
- **No force push**: Branch protection is enabled on main. Never force push.

## Related Repos

| Repo | Purpose |
|------|---------|
| [fpgas.online-infra](https://github.com/fpgas-online/fpgas.online-infra) | Ansible infrastructure (playbooks, roles, inventory) |
| [fpgas.online-site](https://github.com/fpgas-online/fpgas.online-site) | Django web application |
| [fpgas.online-poe](https://github.com/fpgas-online/fpgas.online-poe) | SNMP PoE switch management |
| [fpgas.online-cam](https://github.com/fpgas-online/fpgas.online-cam) | Camera capture and streaming |
| [fpgas.online-setup-pi](https://github.com/fpgas-online/fpgas.online-setup-pi) | Raspberry Pi environment setup |
| [fpgas.online-netboot-pi](https://github.com/fpgas-online/fpgas.online-netboot-pi) | Netboot filesystem tools |
| [fpgas.online-tools](https://github.com/fpgas-online/fpgas.online-tools) | Utility scripts |
| [fpgas.online-test-designs](https://github.com/fpgas-online/fpgas.online-test-designs) | FPGA test designs |
| [apt](https://github.com/fpgas-online/apt) | APT package repository (GitHub Pages) |

## Linting

- ruff: blocking (`ruff.toml`, line-length 120, rules E/F/W/I)
- Django migrations are excluded from linting
