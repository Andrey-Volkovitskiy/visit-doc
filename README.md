# visit-doc
A conversational AI-assistant for a for medical clinics that automates appointment booking via chat and get grounded answers to policy/FAQ questions.

See `docs/ROADMAP.md` for the full design and phased build plan.

## Repository layout

A `uv`-workspace monorepo:

```
services/
├── chat/          # FastAPI core backend: agent, RAG, chat, auth
├── scheduler/      # FastAPI + own Postgres, gRPC server
└── frontend/       # React + Vite SPA
packages/
├── shared-models/  # cross-service Pydantic schemas
└── shared-proto/   # chat<->scheduler gRPC contract
```

## Installation

Prerequisites: [`uv`](https://docs.astral.sh/uv/) (Python 3.12 is installed automatically by `uv`
if you don't have it).

```bash
uv sync                    # install every service/package into one shared venv
uv run pre-commit install  # install git hooks (lint/type-check before each commit)
```

## Getting started

```bash
uv run --package chat -- python -m chat.main            # run the chat service
uv run --package scheduler -- python -m scheduler.main  # run the scheduler service
```

See the [`Makefile`](Makefile) for shortcuts (`make sync`, `make lint`, `make format`,
`make typecheck`, `make precommit`, `make install-hooks`, `make run-chat`, `make run-scheduler`).
