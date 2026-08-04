# Implementation Plan: Grounded FAQ Chat (Phase 0 Walking Skeleton)

**Branch**: `001-grounded-faq-chat` | **Date**: 2026-07-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-grounded-faq-chat/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

A visitor asks a free-text question in a minimal streaming chat UI; a plain async function in the
`chat` FastAPI service embeds the question, retrieves the most relevant chunks from an FAQ knowledge
base indexed in Qdrant, and — only if retrieval confidence clears a threshold — calls the Claude API
directly to generate an answer grounded strictly in those chunks, streaming the answer back while
citing the exact retrieved passage(s) it drew from (the chunk text itself, verbatim — entries have no
separate title). Below threshold, the agent abstains with an explicit "I don't know" instead of
calling the LLM at all. FAQ content (free-form policy documents, source of truth in PostgreSQL,
chunked and embedded into Qdrant on write) is authored via an open, unauthenticated CRUD API,
including deletion — no staff web-app yet. No conversation memory, no intent routing, no scheduling,
no MCP tool servers, and no agent framework in this phase; LangGraph and the rest of Phase 1 land per
`docs/ROADMAP.md` once real branching exists to justify them.

## Technical Context

**Language/Version**: Python 3.12 (backend, per `.python-version`); TypeScript (frontend, React + Vite)

**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.0 async (plain declarative models, not
SQLModel), Alembic, `asyncpg` (app runtime driver) + `psycopg` v3 sync (Alembic migration driver
only), `anthropic` (Claude API, called directly — no agent framework this phase; LangGraph is
deferred to Phase 1), `qdrant-client`, `voyageai` (embeddings — see research.md). Frontend: React,
Vite, no additional chat/streaming library (plain `fetch` + `ReadableStream`).

**Storage**: PostgreSQL (source-of-truth `faq_entries` table, owned by the `chat` service's existing
database) + Qdrant (`faq_chunks` collection, embeddings derived from `faq_entries` content)

**Testing**: pytest (backend unit tests, colocated in `services/chat/tests/` per
`docs/testing-strategy.md`); Vitest + React Testing Library (frontend component tests, new for this
feature since `services/frontend` has no test setup yet)

**Target Platform**: Linux server (backend, run locally via `uvicorn` for this phase — no
containerization/deployment work here); modern evergreen browsers (frontend SPA)

**Project Type**: Web application — existing monorepo `services/chat` (backend) + `services/frontend`
(SPA, first code in this directory)

**Performance Goals**: None formally required this phase (see spec.md SC-004 / Clarifications
2026-07-29) — only that the reply streams incrementally rather than arriving as one block

**Constraints**: Visitor message ≤2,000 chars, rejected below 1 (FR-001a); FAQ entry content
≤20,000 chars (FR-015); FAQ content API has no authentication in this phase (FR-012, accepted risk);
chat is single-turn / stateless across questions (FR-013)

**Scale/Scope**: Portfolio-demo scale — expect single-digit to low-hundreds of FAQ entries and a
handful of concurrent users; not designed or tuned for production load

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Phase-Gated Scope Discipline | PASS | Scope is exactly ROADMAP Phase 0: one chat endpoint, minimal streaming UI, a bare Claude API call doing RAG — no agent framework yet. LangGraph is deferred to Phase 1 per the updated `docs/ROADMAP.md` (a single linear step has no branching to justify a graph framework). No Scheduling, no MCP tool servers, no intent routing, no eval harness — those stay Phase 1/2. |
| II. AI Core Is the Centerpiece | PASS | Nearly all design effort here is in retrieval, chunking, the groundedness gate, and grounded generation; the API/DB layer is thin CRUD in support of it. |
| III. Deliberate, Minimal Service Boundaries | PASS (N/A) | No new service boundary introduced — everything lives in the existing `chat` service. Scheduling is untouched. |
| IV. Structured Outputs & Decoupled Tool Interfaces | PASS, with note | No intent classification exists yet (single-purpose Phase 0), so nothing to route with structured output. Citations are derived structurally from retrieval results (which entries were actually placed in the LLM's context), not parsed from free-text LLM output — see research.md. Both retrieval and generation/orchestration are implemented today as plain internal functions with the same signature/shape Phase 1's `search_faq` MCP tool and LangGraph node will wrap, so no architecture rework is needed when Phase 1 formalizes the tool-call boundary and introduces the graph. |
| V. Grounded Retrieval with Mandatory Abstention | PASS | Core of this feature: FR-002/003/005. A pre-generation similarity-threshold gate on retrieval is this phase's groundedness check (see research.md) — below threshold, the agent abstains without ever calling Claude. Citations quote the retrieved chunk text verbatim (research.md #13), so groundedness is independently checkable by diffing the streamed answer against the cited passage, not just asserted. |
| VI. Documentation as a First-Class Deliverable | PASS | research.md records the tradeoff for every non-obvious choice (embedding provider, drivers, streaming transport, chunking, groundedness gate). A README update recording these choices is expected as an implementation task. |
| VII. Clean Architecture, SOLID & Design Patterns | PASS | Layered structure: API routes → agent step (`answer_faq`) → retrieval/generation services → repositories (Postgres + Qdrant), see Project Structure below. |
| VIII. Test-Driven Development (NON-NEGOTIABLE) | PASS (procedural gate, not a design gate) | Applies at the tasks/implementation stage: contracts and data model below define the testable surface; `/speckit-tasks` must sequence tests-before-implementation. |

No violations — Complexity Tracking table is empty (not needed).

**Post-Phase 1 re-check**: Re-evaluated against `data-model.md`, `contracts/openapi.yaml`, and
`quickstart.md` above — no new entities, endpoints, or dependencies were introduced beyond what the
table above already covers, and none of the design artifacts changed any principle's status. All
PASS results stand unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/001-grounded-faq-chat/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   └── openapi.yaml
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
services/chat/                       # existing uv workspace member — this feature's backend
├── src/chat/
│   ├── main.py                      # existing placeholder; gains the FastAPI app wiring
│   ├── api/
│   │   ├── chat.py                  # POST /chat (streaming)
│   │   └── faq.py                   # POST/GET /faq, GET/PUT/DELETE /faq/{id}
│   ├── agent/
│   │   └── answer_faq.py            # plain async function: retrieve -> groundedness gate -> generate/stream
│   ├── rag/
│   │   ├── chunking.py
│   │   ├── embeddings.py            # Voyage AI client wrapper
│   │   ├── retriever.py             # search_faq() — same signature Phase 1's MCP tool wraps
│   │   └── groundedness.py          # similarity-threshold gate
│   ├── domain/
│   │   ├── models.py                # SQLAlchemy 2.0 declarative model: FaqEntry
│   │   └── schemas.py               # Pydantic request/response DTOs
│   ├── repositories/
│   │   ├── faq_repository.py        # Postgres CRUD (async session)
│   │   └── qdrant_repository.py     # Qdrant upsert/search/delete-by-entry
│   ├── db/
│   │   └── session.py               # async engine/session factory (asyncpg)
│   └── core/
│       └── config.py                # settings: DATABASE_URL, QDRANT_URL, ANTHROPIC_API_KEY, VOYAGE_API_KEY
├── alembic/
│   ├── env.py                       # sync (psycopg) engine for migrations
│   └── versions/
├── alembic.ini
└── tests/                           # existing dir; gains real unit tests for the above

services/frontend/                   # existing placeholder dir — first code lands here
├── package.json
├── vite.config.ts
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── components/
│   │   └── ChatWindow.tsx
│   └── lib/
│       └── chatStream.ts            # fetch + ReadableStream NDJSON parser
└── tests/                           # Vitest + React Testing Library, new

tests/integration/                   # existing centralized dir; still a placeholder after this
tests/e2e/                           # feature — no cross-service integration surface yet
```

**Structure Decision**: Standard two-project web-application layout, mapped onto the repo's existing
monorepo members: all backend work lands in `services/chat` (already a uv workspace member with its
own `pyproject.toml`), and `services/frontend` gets its first real code (a plain Node/Vite project,
not a uv member, per the repo's documented layout). No changes to `packages/shared-models` or
`packages/shared-proto` — FAQ entries aren't needed by `scheduler` in this phase, so there's nothing
to share cross-service yet. `tests/integration/` and `tests/e2e/` stay placeholders since this
feature doesn't cross a service boundary (Scheduling is untouched).

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations — table intentionally empty.
