.PHONY: install test lint typecheck check run docker-build up down

install:
	python -m pip install -e ".[dev]"

test:
	pytest -q

lint:
	ruff check .
	ruff format --check .

typecheck:
	mypy app

check: lint typecheck test

run:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

docker-build:
	docker build -t superset-devin-autofix .

up:
	docker compose up --build

down:
	docker compose down
