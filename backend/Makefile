# CareLink Backend - developer task runner.
# Usage: `make <target>` (run from backend/ directory).

.PHONY: install dev test test-cov lint format migrate migrate-create openapi clean

PYTHON ?= python
PIP    ?= $(PYTHON) -m pip

install:
	$(PIP) install -U pip
	$(PIP) install -r requirements.txt

dev:
	$(PYTHON) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	$(PYTHON) -m pytest tests/ -v

test-cov:
	$(PYTHON) -m pytest tests/ -v --cov=app --cov-report=term-missing

lint:
	$(PYTHON) -m ruff check app tests
	$(PYTHON) -m mypy app

format:
	$(PYTHON) -m ruff format app tests
	$(PYTHON) -m ruff check --fix app tests

migrate:
	$(PYTHON) -m alembic upgrade head

migrate-create:
	@if [ -z "$(m)" ]; then echo "usage: make migrate-create m=\"message\""; exit 1; fi
	$(PYTHON) -m alembic revision --autogenerate -m "$(m)"

openapi:
	$(PYTHON) scripts/export_openapi.py

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -type d -name .mypy_cache -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +
