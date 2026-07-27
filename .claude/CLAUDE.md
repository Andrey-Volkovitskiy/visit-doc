# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repository is at the walking-skeleton stage: each service has only a placeholder `main.py`
("Hello from chat!" / "Hello from scheduler!") and no real application code exists yet.
`docs/ROADMAP.md` is the authoritative design document — read it before starting any implementation
work, since it defines the architecture, phased build plan, and the reasoning behind each technology
choice. Treat its "Design principles" and "Phased build plan" sections as binding scope guidance,
not just background: build Phase 0 before Phase 1, don't introduce Phase 3+ platform layers early,
and don't add services/infra beyond what the current phase calls for.

The repo is a **monorepo**: `services/chat`, `services/scheduler`, and `services/frontend` are
independent services, with cross-service Python code (Pydantic schemas, the gRPC contract) factored
out into `packages/`. See "Repository layout" below.

## What this project is

VisitDoc is a conversational AI assistant for a medical clinic (a portfolio project targeting an AI
developer role). Patients book/reschedule/cancel appointments via chat and get grounded,
citation-backed answers to policy/FAQ questions; the assistant abstains and escalates to human staff
rather than confabulating when retrieval is weak. The applied-AI core (agent graph, RAG, tool use,
eval/observability) is the focus — platform/infrastructure work is explicitly scoped as optional
later phases.

## Stack
### AI
- Claude API
- Qdrant

### Backend
- Python / FastAPI / Pydantic
- Postgres / Alembic / SQLModel / SQLAlchemy

### Ffontend
- REACT/Vite


## Repository layout

`uv` workspace (root `pyproject.toml` is a *virtual* workspace root — no `[project]` table of its
own, it only lists members):

```
services/
├── chat/          # FastAPI core backend: agent, RAG, chat, auth (uv member "chat")
├── scheduler/     # FastAPI + own Postgres, gRPC server (uv member "scheduler")
└── frontend/      # React + Vite SPA — plain Node project, NOT a uv workspace member
packages/
├── shared-models/ # cross-service Pydantic schemas (uv member "shared-models")
└── shared-proto/  # chat<->scheduler gRPC contract: protos/ source + generated *_pb2*.py (uv member "shared-proto")
```

`chat` and `scheduler` depend on `shared-models`/`shared-proto` via `tool.uv.sources` workspace
references. All Python members share **one `uv.lock` and one `.venv`** at the repo root — they
can't pin conflicting versions of a shared dependency.

## Commands

`.python-version` is pinned to 3.12 for the whole workspace.

```bash
uv sync                                          # install/sync every workspace member
uv sync --package chat                            # sync just one member (e.g. in CI)
uv run --package chat -- python -m chat.main      # run chat's entry point
uv run --package scheduler -- python -m scheduler.main   # run scheduler's entry point
uv add --package chat <package>                   # add a dep to services/chat
uv add --package shared-proto <package>           # add a dep to a shared package
```

Regenerating the gRPC stubs (after editing `packages/shared-proto/protos/scheduling/v1/scheduling.proto`)
is documented in `packages/shared-proto/README.md` — it requires a manual import fixup after
running `protoc`, don't skip that step.

No test suite, linter, or CI configuration exists yet (though `ruff`, `pytest`, and `mypy` are
available workspace-wide as a dev dependency group). When these are wired up, prefer
`uv run pytest`, `uv run ruff check`, etc., and update this file with the exact invocations.

## Architecture

### Core design principles
- Follow SOLID, major clean architecture principles, and best industry paractices.

### Target shape (AI-core phase, per ROADMAP)

- **Core backend** — FastAPI, hosting the agent, RAG, chat, and auth. Single deployable for
  everything except Scheduling.
- **Scheduling service** — a separate FastAPI service with its own PostgreSQL, the one deliberate
  service boundary in this phase. Owns doctor calendars, availability, and booking. Talks to the
  core backend over gRPC (`CheckAvailability`, `BookAppointment`). Double-booking is prevented at
  the database level via a PostgreSQL exclusion constraint on interval/range types, not application
  code.
- **Vector store** — Qdrant, for RAG over clinic policy/FAQ documents.
- **Agent framework** — LangGraph, with real branching (parallel specialist nodes + merge) for
  mixed-intent messages rather than a linear chain.
- **Frontend** — a minimal React + Vite streaming chat UI.
- **Tracing/eval** — Langfuse (self-hosted) for per-step latency, token cost, and decision traces.

### Key design decisions to preserve

- Intent classification (FAQ / booking / escalation) uses **structured output**, not free-text
  parsing, and a cheap/fast model — reserve the stronger model for generation.
- Capabilities are exposed to the agent as **MCP tools** (`search_faq`, `check_availability`,
  `book_appointment`, `escalate_to_staff`) so agent logic stays decoupled from implementation.
- RAG must include defensible chunking, a reranking step, citations to source documents, and an
  explicit **abstention path** plus a **groundedness check** before any FAQ answer is returned.
- Scheduling failure handling (timeouts, retries, agent behavior when Scheduling is unreachable) is
  part of the design, not an afterthought.
- Each significant technology choice should be documented with its tradeoff in the README, so later
  additions should follow that pattern rather than going undocumented.

See `docs/ROADMAP.md` for the full phased plan (Phase 0 walking skeleton → Phase 1 agent → Phase 2
eval/observability → Phase 3+ optional platform layers) and the target microservices reference
architecture.
