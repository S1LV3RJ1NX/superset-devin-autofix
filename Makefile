.PHONY: install lock test test-unit test-integration lint typecheck check hooks run docker-build up down

install:
	uv sync --all-groups --frozen

lock:
	uv lock

test:
	uv run pytest -q

test-unit:
	uv run pytest -q tests/unit

test-integration:
	uv run pytest -q tests/integration

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy app

check:
	uv run pre-commit run --hook-stage pre-push --all-files

hooks:
	uv run pre-commit install --install-hooks

run:
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

docker-build:
	docker build -t superset-devin-autofix .

up:
	docker compose up --build

down:
	docker compose down
