POETRY ?= poetry
export PYTHONPATH := src

.PHONY: install
install:
	$(POETRY) install --no-root

.PHONY: lint
lint:
	$(POETRY) run ruff check .

.PHONY: format
format:
	$(POETRY) run ruff check . --fix
	$(POETRY) run ruff format .

.PHONY: typecheck
typecheck:
	$(POETRY) run mypy src tools

.PHONY: test
test:
	$(POETRY) run pytest

.PHONY: seed-sheets
seed-sheets:
	$(POETRY) run python tools/seed_sheets.py

.PHONY: import-templates
import-templates:
	$(POETRY) run python tools/import_templates.py templates/opening_v3.json templates/continue_v2.json templates/closing_v3.json

.PHONY: run
run:
	$(POETRY) run python -m retailcheck

.PHONY: reminders
reminders:
	$(POETRY) run python tools/send_reminders.py --mode pending_steps $(if $(shop),--shop-id $(shop),)

.PHONY: reminders-scheduler
reminders-scheduler:
	$(POETRY) run python tools/reminder_scheduler.py
