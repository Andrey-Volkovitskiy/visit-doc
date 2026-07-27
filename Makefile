.PHONY: sync lint format typecheck precommit install-hooks run-chat run-scheduler

sync:
	uv sync

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy .

precommit:
	uv run pre-commit run --all-files

install-hooks:
	uv run pre-commit install

run-chat:
	uv run --package chat -- python -m chat.main

run-scheduler:
	uv run --package scheduler -- python -m scheduler.main
