.PHONY: sync lint format typecheck typecheck-python typecheck-frontend \
        test test-unit test-frontend test-integration test-e2e test-db-prune \
        precommit install-hooks run-chat run-chat-dev run-scheduler run-scheduler-dev run-frontend-dev \
        services-up services-down services-status migrate \
        db-up db-down db-reset alembic-chat-history alembic-scheduler-history

sync:
	uv sync

lint:
	uv run ruff check .

format:
	uv run ruff format .

# Both languages: `tsc` is the only thing that typechecks the frontend - vitest
# transpiles without checking, so a type error is invisible to `make test`.
typecheck: typecheck-python typecheck-frontend

typecheck-python:
	uv run mypy .

typecheck-frontend:
	cd services/frontend && npm run typecheck

test: test-unit test-frontend

# Distributed across processes, since every test in these suites talks to real Postgres
# and real Qdrant and spends most of its time waiting on one of them. Each worker gets
# its own database and its own Qdrant collection (shared_db.testing), so they cannot
# clear each other's rows mid-test. Two rather than one-per-core: the speedup plateaus
# early, the Postgres and Qdrant containers need cores of their own, and a second run
# alongside this one (another terminal, another Claude Code session) has to fit on the
# same machine. `--dist loadfile` keeps a file's tests together, so a suite's
# within-file ordering survives.
test-unit:
	uv run pytest -n 2 --dist loadfile

test-frontend:
	cd services/frontend && npm test

test-integration:
	uv run pytest tests/integration

# Drop the per-session test databases/collections the suites create automatically
# (one set per Claude Code session, never reaped). Leaves the plain `_test` ones alone.
test-db-prune:
	@./scripts/prune-test-stores.sh

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

run-scheduler-dev:
	uv run --directory services/scheduler alembic upgrade head
	uv run --package scheduler -- uvicorn scheduler.main:app --reload --reload-dir services/scheduler/src --host 0.0.0.0 --port 8001

run-frontend-dev:
	cd services/frontend && npm run dev

# All three services in the background at once, for manual testing. Each records its pid under
# .run/ and is stopped by that pid - never with `pkill -f "chat.main"`, whose pattern also matches
# the shell running that very command and kills the caller with it.
services-up:
	@./scripts/dev-services.sh up all

services-down:
	@./scripts/dev-services.sh down all

services-status:
	@./scripts/dev-services.sh status all

# Bring both dev databases to head. `run-chat-dev`/`run-scheduler-dev` each do their own half;
# this is for the background services above, which deliberately don't migrate on start.
migrate:
	uv run --directory services/scheduler alembic upgrade head
	uv run --directory services/chat alembic upgrade head

db-up:
	docker compose up -d

db-down:
	docker compose down

# Destructive: wipes Postgres + Qdrant data volumes. Confirm before running.
db-reset:
	docker compose down -v

alembic-chat-history:
	uv run --directory services/chat alembic history

alembic-scheduler-history:
	uv run --directory services/scheduler alembic history
