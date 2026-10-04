.PHONY: install lint format test check build docs examples clean

PY ?= uv run

install:
	uv venv
	uv pip install -e ".[dev]"

lint:
	$(PY) ruff check .
	$(PY) ruff format --check .

format:
	$(PY) ruff format .
	$(PY) ruff check --fix .

test:
	$(PY) pytest -q

check: lint test

build:
	rm -rf dist
	uv build

docs:
	$(PY) cc-hooks events --markdown > docs/events.md

examples:
	$(PY) cc-hooks test examples/fixtures --settings examples/settings.json --cwd .

clean:
	rm -rf dist build .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
