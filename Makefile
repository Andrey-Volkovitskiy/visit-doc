.PHONY: sync lint format typecheck test test-unit test-integration test-e2e precommit \
        install-hooks run-chat run-scheduler

sync:
	uv sync

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy .

test: test-unit

test-unit:
	uv run pytest

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
