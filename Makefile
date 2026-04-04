.PHONY: help setup venv install migrate loaddata run lint clean

VENV := .venv
PYTHON := $(VENV)/bin/python
MANAGE := $(PYTHON) manage.py

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'

setup: venv install migrate loaddata ## Full dev setup (venv + install + migrate + loaddata)

venv: ## Create virtualenv if missing
	@test -d $(VENV) || (uv venv && echo "Created $(VENV)")

install: venv ## Install package and dependencies in dev mode
	uv pip install -e .

migrate: ## Run database migrations
	$(MANAGE) migrate

loaddata: ## Load fixture data (FPGA board definitions)
	$(MANAGE) loaddata fpgas.online.json

run: ## Run the development server
	$(MANAGE) runserver

lint: ## Run ruff linter
	uv run ruff check .

clean: ## Remove venv and database
	rm -rf $(VENV) db.sqlite3
