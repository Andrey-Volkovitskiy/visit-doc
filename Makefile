.PHONY: sync lint format typecheck test test-unit test-frontend test-integration test-e2e \
        precommit install-hooks run-chat run-scheduler run-frontend db-up db-down db-reset

sync:
	uv sync

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy .

test: test-unit test-frontend

test-unit:
	uv run pytest

test-frontend:
	cd services/frontend && npm test

test-integration:
	uv run pytest tests/integration

test-e2e:
	uv run pytest tests/e2e

precommit:
	uv run pre-commit run --all-files

install-hooks:
	uv run pre-commit install

run-chat:
	uv run --package chat -- python -m chat.main

run-scheduler:
	uv run --package scheduler -- python -m scheduler.main

run-frontend:
	cd services/frontend && npm run dev

db-up:
	docker compose up -d

db-down:
	docker compose down

# Destructive: wipes Postgres + Qdrant data volumes. Confirm before running.
db-reset:
	docker compose down -v
