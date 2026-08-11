# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@CLAUDE.local.md

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
│                  # has its own .claude/CLAUDE.md with the Python code style guide
├── scheduler/     # FastAPI + own Postgres, gRPC server (uv member "scheduler")
│                  # has its own .claude/CLAUDE.md with the Python code style guide
└── frontend/      # React + Vite SPA — plain Node project, NOT a uv workspace member
packages/
├── shared-models/ # cross-service Pydantic schemas (uv member "shared-models")
└── shared-proto/  # chat<->scheduler gRPC contract: protos/ source + generated *_pb2*.py (uv member "shared-proto")
```

`chat` and `scheduler` depend on `shared-models`/`shared-proto` via `tool.uv.sources` workspace
references. All Python members share **one `uv.lock` and one `.venv`** at the repo root — they
can't pin conflicting versions of a shared dependency.

### Convention: directory-scoped CLAUDE.md files

When a subdirectory needs its own Claude Code guidance (e.g. a service-specific style guide), place
it at `<dir>/.claude/CLAUDE.md`, not `<dir>/CLAUDE.md` — matching this repo's own root convention
(this file lives at `.claude/CLAUDE.md`, not the repo root). Claude Code loads a directory's
`CLAUDE.md` only when files under that directory are being read/edited, so this keeps
service-specific rules out of context everywhere else. `services/chat/.claude/CLAUDE.md` and
`services/scheduler/.claude/CLAUDE.md` are the existing examples — both just `@`-import
`docs/python-style-guide.md` rather than duplicating it.

## Commands

`.python-version` is pinned to 3.12 for the whole workspace. Common commands are in the
[`Makefile`](../Makefile): `make sync`, `make lint`, `make format`, `make typecheck`,
`make precommit`, `make install-hooks`, `make run-chat`, `make run-scheduler`.

A few less-common `uv` invocations that don't have a Makefile target (they take arguments):

```bash
uv sync --package chat                    # sync just one member (e.g. in CI)
uv add --package chat <package>           # add a dep to services/chat
uv add --package shared-proto <package>   # add a dep to a shared package
```

Regenerating the gRPC stubs (after editing `packages/shared-proto/protos/scheduling/v1/scheduling.proto`)
is documented in `packages/shared-proto/README.md` — it requires a manual import fixup after
running `protoc`, don't skip that step.

Ruff lint rules are configured once, in the root `pyproject.toml`'s `[tool.ruff]`/`[tool.ruff.lint]`
tables, and apply to every Python workspace member automatically via ruff's hierarchical config
discovery (no member has its own `[tool.ruff]`, so the walk-up always lands on the root). Generated
code is excluded via `extend-exclude` rather than hand-reformatted to match style rules — the
established examples are gRPC stubs (`**/*_pb2.py`, `**/*_pb2_grpc.py`) and Alembic migrations
(`**/alembic/versions/*.py`); follow the same pattern for the next generated-code case instead of
fixing lint violations by hand in generated files.

Mypy is configured once, in the root `pyproject.toml`'s `[tool.mypy]` table, in `strict` mode
(aligns with the style guide's "annotate every function" rule). Unlike ruff, mypy doesn't do
per-file hierarchical discovery — it's pointed explicitly at each workspace member's `src/` via
`files`/`mypy_path`, with `explicit_package_bases = true` since each member is its own package root.
The generated gRPC stubs have their errors suppressed via a `[[tool.mypy.overrides]]` entry
(`ignore_errors = true` for `shared_proto.scheduling.v1.*`) since protoc-generated code isn't
statically typed; `types-protobuf`/`types-grpcio` are installed as dev dependencies so *your* code
using `google.protobuf`/`grpc` directly still gets real type checking.

Testing conventions (folder layout, naming, why `--import-mode=importlib` is required, why `tests/`
is excluded from mypy) are documented in `docs/testing-strategy.md`. In short: unit tests are
colocated per workspace member (`services/chat/tests/`, `services/scheduler/tests/`,
`packages/shared-models/tests/`, `packages/shared-proto/tests/`); integration/e2e tests are
centralized at `tests/integration/`/`tests/e2e/` (placeholders for now). Run via `make test` /
`make test-unit`, `make test-integration`, `make test-e2e`; only the unit tier runs in CI so far
(`test` job in `.github/workflows/ci.yml`, alongside `pre-commit`).

### Pre-commit hooks

`.pre-commit-config.yaml` (repo root) defines four **local** hooks (`language: system`, no
Astral/pre-commit-mirror version pins — they all shell out to the exact ruff/mypy/uv versions
already resolved in `uv.lock`, so there's one source of truth for tool versions, not two):

1. `uv-lock-check` — `uv lock --check`: fails if `uv.lock` is stale relative to `pyproject.toml`.
2. `uv-sync-check` — `uv sync --check`: fails if `.venv` is stale relative to `uv.lock` (read-only —
   does not install anything; run a plain `uv sync` to fix).
3. `ruff-check` — `uv run ruff check .`
4. `mypy-check` — `uv run mypy .`

Installed locally via `uv run pre-commit install`. Each contributor needs to run that once after
cloning (it's a `.git/hooks/` entry, not tracked by git).

## Architecture

### Core design principles
- Follow SOLID, major clean architecture principles, and best industry paractices.
- Dependency Inversion: orchestration/high-level code (e.g. a LangGraph node) should depend only on
  domain types, never on a specific provider's wire format (e.g. Anthropic's `MessageParam`).
  Translating domain data into a provider's request shape is that provider-calling function's own
  responsibility, done internally, not the caller's — keeps provider-specific knowledge in one
  place and out of orchestration code.

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
- RAG must include defensible chunking, a reranking step, citations to source documents — derived
  structurally from what was actually retrieved and placed in context, never self-reported by the
  LLM (avoids hallucinated citations) — and an explicit **abstention path** plus a **groundedness
  check** before any FAQ answer is returned.
- Any entity with both a Postgres row and a derived Qdrant index (e.g. `FaqEntry`/`FaqChunk`) keeps
  them consistent via a fixed ordering: deindex from Qdrant *before* deleting the Postgres row on
  delete, and always delete-then-upsert (never diff) the index on update — so the vector store can
  never outlive or go stale relative to its source of truth.
- Repository functions take the `AsyncSession` as an explicit parameter (e.g.
  `faq_repository.create(session, content)`) rather than a repository class holding session state —
  matches FastAPI's own documented pattern, keeps repository functions stateless and reusable across
  callers, and lets the API layer own the transaction boundary (one session per request via
  `Depends`). New repositories, including `scheduler`'s, should follow this shape.
- Scheduling failure handling (timeouts, retries, agent behavior when Scheduling is unreachable) is
  part of the design, not an afterthought.
- Each significant technology choice should be documented with its tradeoff in the README, so later
  additions should follow that pattern rather than going undocumented.

See `docs/ROADMAP.md` for the full phased plan (Phase 0 walking skeleton → Phase 1 agent → Phase 2
eval/observability → Phase 3+ optional platform layers) and the target microservices reference
architecture.
