.PHONY: sync lint format typecheck test test-unit test-frontend test-integration test-e2e \
        precommit install-hooks run-chat run-chat-dev run-scheduler run-frontend-dev db-up db-down db-reset \
        alembic-chat-history

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

run-chat-dev:
	uv run --directory services/chat alembic upgrade head
	uv run --package chat -- uvicorn chat.main:app --reload --reload-dir services/chat/src --host 0.0.0.0 --port 8000

run-scheduler:
	uv run --package scheduler -- python -m scheduler.main

run-frontend-dev:
	cd services/frontend && npm run dev

db-up:
	docker compose up -d

db-down:
	docker compose down

# Destructive: wipes Postgres + Qdrant data volumes. Confirm before running.
db-reset:
	docker compose down -v

alembic-chat-history:
	uv run --directory services/chat alembic history
