# Implementation Plan: Structured Logging for App/AI Behavior

**Branch**: `002-structured-logging` | **Date**: 2026-08-05 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-structured-logging/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

Add structured, per-turn (and per-FAQ-operation) logging to the `chat` service so the agent's actual
decision trace — including its embedding/chunking sub-steps, not just each stage's outcome — can be
reconstructed from logs alone, without re-running the request. Every log entry is emitted as a
structured `structlog` event through one shared processor chain that stamps a correlation ID (a
chat turn's `turn_id` or a FAQ operation's `operation_id`, both bound via `contextvars`-based FastAPI
middleware), truncates any text field over 2,000 characters, and redacts the service's own secrets —
before being rendered, today, as an easy-to-read terminal view (three severity tiers: routine `info`,
`error`, and a more-prominent `critical` for events outside any single turn/operation, e.g. a
dependency becoming unreachable). Because rendering is the last step of that one shared chain,
switching to a Langfuse-ready shape later (`docs/ROADMAP.md` Phase 2, now expected much further out)
is a change to that one renderer, not to any of the ~20 call sites that log. Scoped to `chat`
only — `scheduler` stays a placeholder with nothing yet worth tracing.

## Technical Context

**Language/Version**: Python 3.12 (`services/chat`, per `.python-version`) — this feature touches
no other workspace member

**Primary Dependencies**: `structlog` (new dependency of `services/chat`, research.md #1) for the
structured event → processor chain → renderer pipeline; `python-ulid` (new dependency, research.md
#2) for short, separator-free turn/operation IDs (the same generator is reused for `operation_id`,
research.md #6 — no additional ID library needed); existing FastAPI (middleware hook for
correlation-ID binding, on both the chat and FAQ routes) and `pydantic-settings` (`Settings`, source
of the secret values FR-017's redaction processor scrubs) — no other new dependencies

**Storage**: N/A — log entries are operational/diagnostic output, never persisted to PostgreSQL or
Qdrant (spec.md Key Entities); written to stdout/terminal only

**Testing**: pytest, colocated in `services/chat/tests/` per `docs/testing-strategy.md`; new tests
assert against captured `structlog` events (`structlog.testing.capture_logs`) rather than parsing
terminal text — the processor chain (truncation, redaction, correlation, level assignment) is
unit-testable independent of the `ConsoleRenderer`'s exact visual styling

**Target Platform**: Linux server (same as `chat` today — run locally via `uvicorn`/`make run-chat`,
no containerization/deployment work here)

**Project Type**: Backend service enhancement — existing `services/chat` only; no `frontend` or
`scheduler` changes (spec.md Assumptions: scheduler is still a placeholder with nothing to trace)

**Performance Goals**: SC-004 — no perceptible slowdown to the streamed chat response. Processors
run synchronously in the request path, so each one stays O(event size) with no blocking I/O beyond
the terminal write itself (already how `print`/stdout logging behaves); no network calls, no
database round-trips inside the logging path

**Constraints**: FR-013 (2,000-char truncation bound, independent of any field's own validation
limit); FR-008 (a logging failure MUST NOT block or delay the visitor-facing response — logging
calls are wrapped/best-effort, never awaited in a way that can stall the response, and a dropped
entry is never retried/reported); FR-017 (secrets never logged, including inside error/critical-
event detail); FR-014 (human-readable rendering lives in exactly one place); FR-018 (a mid-turn/
mid-operation dependency failure logs as two correlated entries, not one merged entry); FR-021
(FAQ operations get their own `operation_id`, distinct from `turn_id`); FR-020/FR-022 (embedding
and chunking/embedding are logged as their own summarized sub-step entries, not per-item)

**Scale/Scope**: Same portfolio-demo scale as `specs/001-grounded-faq-chat` — single process, low
concurrent load; no log aggregation/shipping infrastructure this phase (that's Langfuse ingestion,
explicitly deferred per spec.md Assumptions)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Phase-Gated Scope Discipline | PASS | Scoped to exactly what the spec asks: structured logging + an interim terminal renderer in `chat` only. No Langfuse ingestion (explicitly deferred, spec.md Assumptions), no `scheduler` changes, no new services, no proactive health-check infrastructure (research.md #5 reuses existing dependency-call sites instead of adding a polling task). |
| II. AI Core Is the Centerpiece | PASS | This *is* instrumentation for the AI core — the entire point is making the agent's embedding/retrieval/groundedness/generation decisions inspectable after the fact (User Story 1), now down to the embedding sub-step (FR-020) too. It doesn't compete with AI-core effort; it's in service of debugging it. |
| III. Deliberate, Minimal Service Boundaries | PASS (N/A) | No new service boundary. Everything lives inside the existing `chat` service; `scheduler`/its database are untouched. |
| IV. Structured Outputs & Decoupled Tool Interfaces | PASS, with note | Not agent routing/tool-call structured output (out of scope here), but the same spirit applies: log entries are structured data (FR-009), not free-text, and the rendering layer is decoupled from where events are emitted (FR-014) — swappable without touching `answer_faq`/`faq.py`/`indexing.py`/etc. |
| V. Grounded Retrieval with Mandatory Abstention | PASS | This feature only *observes* retrieval/groundedness/abstention (spec 001's existing FR-002/003/005 behavior); it changes no retrieval or abstention logic. Abstention is explicitly logged as a routine, non-flagged outcome (FR-012), not treated as a failure. |
| VI. Documentation as a First-Class Deliverable | PASS, with note | research.md records the tradeoff for the real technology choices here: `structlog` vs. stdlib `logging` vs. `loguru` (research.md #1), ULID vs. UUID4/`shortuuid` for correlation IDs (research.md #2), and summarized vs. per-item sub-step logging (research.md #6). A README entry recording these choices is expected as an implementation task, matching how spec 001 documented its own choices. |
| VII. Clean Architecture, SOLID & Design Patterns | PASS | The processor-chain design *is* separation of concerns: "what gets logged" (call sites in `agent`/`api`/`rag`) stays fully decoupled from "how it's truncated/redacted/rendered" (one `core/logging.py` module, research.md #1/#4). Correlation-ID binding lives in middleware, reused for both `turn_id` and `operation_id` rather than smeared across business logic (research.md #2, #6). |
| VIII. Test-Driven Development (NON-NEGOTIABLE) | PASS (procedural gate) | data-model.md and contracts/log-events.md define the testable event/field contract; `/speckit-tasks` must sequence tests (asserting on captured `structlog` events) before the processors/call sites that produce them. |

No violations — Complexity Tracking table is empty (not needed).

**Post-Phase 1 re-check**: Re-evaluated against `data-model.md`, `contracts/log-events.md`, and
`quickstart.md` above, including this revision's additions for FR-020/FR-021/FR-022 (embedding/
chunking sub-step events, FAQ operation correlation) — no new entities beyond additional *event
types* on the existing Log Entry shape, no new endpoints, no new dependencies (both `operation_id`
and the sub-step events reuse `structlog`/`python-ulid`, already accounted for under Principle
VI/VII), and no new services. No design artifact changed any principle's status. All PASS results
stand unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/002-structured-logging/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md         # Phase 1 output (/speckit-plan command)
├── quickstart.md         # Phase 1 output (/speckit-plan command)
├── contracts/            # Phase 1 output (/speckit-plan command)
│   └── log-events.md
└── tasks.md              # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
services/chat/                       # existing uv workspace member — the only one this feature touches
├── src/chat/
│   ├── main.py                      # existing; lifespan gains a critical-event log around the
│   │                                 #   existing ensure_collection() startup check (research.md #5)
│   ├── core/
│   │   ├── config.py                # existing Settings — read (not modified) by the redaction
│   │   │                            #   processor for its own secret values (research.md #4)
│   │   ├── logging.py               # NEW: structlog configuration — processor chain (correlation
│   │   │                            #   merge, truncation, redaction, level→tier mapping) and the
│   │   │                            #   one centralized ConsoleRenderer (FR-011, FR-014)
│   │   └── correlation.py           # NEW: FastAPI middleware — generates a turn_id (chat route) or
│   │                                 #   operation_id (FAQ routes) per request, binds it via
│   │                                 #   structlog.contextvars (FR-006, FR-021, research.md #2, #6)
│   ├── api/
│   │   ├── chat.py                  # existing; gains correlation middleware wiring (turn_id) +
│   │   │                            #   turn.error logging around the existing pipeline-step
│   │   │                            #   boundaries
│   │   └── faq.py                   # existing; gains correlation middleware wiring (operation_id)
│   │                                 #   + faq.entry_* / faq.operation_failed logging around each
│   │                                 #   existing CRUD operation
│   ├── agent/
│   │   └── answer_faq.py            # existing; gains turn.message_received / .groundedness_verdict
│   │                                 #   / .completed log calls at each existing pipeline step — no
│   │                                 #   behavior change
│   ├── rag/
│   │   ├── retriever.py             # existing search_faq(); gains turn.message_embedded (FR-020)
│   │   │                            #   right after embed_texts() returns, and
│   │   │                            #   turn.retrieval_completed; its existing dependency-failure
│   │   │                            #   exception handling gains the FR-018 critical-event log
│   │   ├── embeddings.py            # existing embed_texts(); no signature change — logging happens
│   │   │                            #   at its call sites (retriever.py for turns, indexing.py for
│   │   │                            #   FAQ operations), not inside embed_texts() itself, since it
│   │   │                            #   has no correlation-ID context of its own to log under
│   │   ├── chunking.py              # existing chunking function; same — no signature change, no
│   │   │                            #   log call inside it (indexing.py logs the outcome instead)
│   │   └── indexing.py              # existing index_faq_entry()/deindex_faq_entry(); gains
│   │                                 #   faq.content_chunked / faq.chunks_embedded (FR-022) around
│   │                                 #   its existing chunking.py/embeddings.py calls
│   ├── repositories/
│   │   ├── faq_repository.py        # existing; dependency-failure paths gain critical-event log
│   │   └── qdrant_repository.py     # existing; dependency-failure paths gain critical-event log
│   └── domain/schemas.py            # existing — unchanged (no new request/response fields; turn_id/
│                                     #   operation_id are log-only concepts, not API contract fields)
└── tests/                           # existing dir
    ├── test_logging.py              # NEW: processor chain — truncation, redaction, level/tier
    │                                 #   assignment, structured-field shape (contracts/log-events.md)
    ├── test_correlation.py          # NEW: turn_id/operation_id binding/isolation across concurrent
    │                                 #   requests, and that the two are never both present on one
    │                                 #   entry (data-model.md)
    ├── test_chat_api.py             # existing; gains assertions that a turn's log events share
    │                                 #   one turn_id and cover every pipeline step, including
    │                                 #   turn.message_embedded
    ├── test_faq_api.py              # existing; gains assertions that CRUD ops (and failures)
    │                                 #   produce the expected faq.* log events, all sharing one
    │                                 #   operation_id
    ├── test_indexing.py             # existing; gains assertions that create/update operations log
    │                                 #   faq.content_chunked/faq.chunks_embedded with the correct
    │                                 #   chunk_count, and that delete does not (FR-022)
    └── test_main.py                 # existing; gains an assertion that a failed ensure_collection()
                                      #   startup check logs critical.dependency_unreachable with
                                      #   no turn_id/operation_id (FR-015)

services/scheduler/                  # untouched — still a placeholder (spec.md Assumptions)
services/frontend/                   # untouched — this feature has no user-facing UI surface
```

**Structure Decision**: Single-service backend enhancement, entirely inside the existing
`services/chat` uv workspace member — no new workspace member, no changes to `packages/shared-models`
or `packages/shared-proto` (log entries are `chat`-internal, never shared cross-service), and no
`tests/integration`/`tests/e2e` changes (this feature doesn't cross the `chat`↔`scheduler` service
boundary). Two new modules (`core/logging.py`, `core/correlation.py`) hold everything centralized
(FR-014) — `correlation.py` binds either `turn_id` or `operation_id` depending on which route
triggered it, rather than being duplicated per identifier kind. Every other touched file, including
the two newly in-scope `rag/indexing.py` and `rag/retriever.py` sub-step call sites, gains log calls
at its existing pipeline steps without changing its existing behavior or signatures; `rag/chunking.py`
and `rag/embeddings.py` themselves stay untouched, since the sub-step outcome is logged by their
callers, not inside them.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations — table intentionally empty.
