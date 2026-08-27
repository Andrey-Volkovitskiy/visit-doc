# Implementation Plan: Conversational Chat History

**Branch**: `003-conversational-chat-history` | **Date**: 2026-08-06 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/003-conversational-chat-history/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

Turn the existing stateless, single-turn `/chat` endpoint into a persistent, multi-turn
chat — ROADMAP Phase 1a (spec.md's **Implements** note). An anonymous visitor is identified
by an httponly cookie holding a `Session` row's ULID primary key — no login, matching the project's
current no-auth phase. Each `Session` currently owns exactly one active `Chat` (enforced by
application logic, not a schema constraint): `Session` is kept as its own row and its own concept,
distinct from `Chat`, specifically because spec.md's Future Direction section anticipates a
later **Patient** entity sitting between them (one `Session` owning several Patients, each with its
own chat(s)) — this shape lets that later feature add a `patients` table and repoint
`chats.patient_id`/`.session_id` without renaming or restructuring the identity/cookie
mechanism itself.

A chat is a **flat, sender-tagged log of `Message` rows** (`sender: patient | assistant`
this phase — an open set, not a hardcoded pair, since ROADMAP Phase 1d is expected to add `staff` as
a third sender later without restructuring stored data, FR-013). Messages are not paired into fixed
request/response turns: a patient message is inserted the moment it's validated, and an assistant
message is inserted only once the RAG pipeline completes successfully — nothing is ever inserted
"pending" and updated later, so a failed generation (FR-012) simply means no assistant row appears,
with zero special-case handling. Prior messages are passed to Claude as a proper alternating
`user`/`assistant` list (not concatenated into the prompt string); any consecutive run of
same-sender rows — a burst of patient messages (FR-014) or an unanswered message followed by another
patient message — is merged to satisfy the Messages API's strict alternation requirement, one
general rule rather than a per-cause special case.

When a new message arrives for a chat while an earlier message's reply is still generating,
that earlier generation is **cancelled and discarded unconditionally** (FR-015, the user's explicit
clarification choice, re-confirmed even for the case where tokens have already started streaming —
research.md #9): a process-local `agent/generation_registry.py` tracks at most one in-flight
generation task per chat via `asyncio.Task.cancel()`, and the cancelled request's own stream ends
with a `ChatCancelledEvent` rather than a reply. The restarted pipeline run's retrieval query and its
generation history both draw on the same merged trailing run of unanswered patient messages — not
just the newest one in isolation — so a fragment like "Dr. Josh?" retrieves correctly when it follows
"When can I see" in the same unanswered burst (research.md #6); no LLM-based query rewriting is
introduced, this reuses the existing history-merge output as retrieval's input. On the frontend,
tokens still paint live as they arrive (unchanged from spec 001's FR-004) for every message; the one
addition is that a cancelled stream's bubble is cleanly removed rather than left dangling or shown as
an error (research.md #10). Two new endpoints, `GET /chat` (history) and `DELETE
/chat` (hard-delete the chat and start fresh under the same session), plus the
frontend growing a scrollback view and a confirmation-gated "Clear chat" button. No new
service, no new external dependency, no agent framework — this stays inside the existing `chat`
service's plain retrieve→gate→generate pipeline.

## Technical Context

**Language/Version**: Python 3.12 (backend, per `.python-version`); TypeScript (frontend, React 19 +
Vite)

**Primary Dependencies**: No new dependencies. Reuses what `chat` already depends on: FastAPI,
Pydantic v2, SQLAlchemy 2.0 async + Alembic, `anthropic` (multi-turn `messages` list — a standard
use of the existing SDK, not a new capability), `qdrant-client`, `voyageai`, and `python-ulid`
(already used for the per-request `turn_id`/`operation_id` correlation IDs in
`core/correlation.py` — this feature reuses that same ULID scheme for `Session`/`Chat`/
`Message` primary keys). Cancel-and-restart (FR-015) uses only stdlib `asyncio` (`Task.cancel()`) —
no new dependency for the in-flight-generation registry either. Frontend: plain `fetch` with
`AbortController`, no new library.

**Storage**: PostgreSQL — three new tables (`sessions`, `chats`, `messages`) in the `chat`
service's existing database, added via an Alembic migration. No new datastore. The visitor's
identity itself lives in an httponly browser cookie (not a new store — the cookie value *is* the
`sessions.id` primary key; `chats.session_id` references it, see research.md). The
in-flight-generation registry (FR-015) is process-local in-memory state, not persisted (data-model.md
"Runtime state").

**Testing**: pytest (`services/chat/tests/`) — extends `test_chat_api.py` (multi-turn context,
cookie behavior, cancellation producing a `cancelled` event, and now also the history/clear coverage
that would otherwise have lived in a separate `test_conversation_api.py` — folded in here since
`GET`/`DELETE /chat` share the same router file as `POST /chat`, see Project Structure), adds
`test_chat_repository.py`, `test_history.py` (same-sender merge logic), and
`test_generation_registry.py` (cancel-and-restart unit tests, mocking task timing rather than
relying on wall-clock delay). Vitest + React Testing Library (`services/frontend/tests/`) — extends
`ChatWindow.test.tsx`, adds tests for history hydration and the clear-chat flow.

**Target Platform**: Unchanged — Linux server (backend, local `uvicorn`); evergreen browsers
(frontend SPA).

**Project Type**: Web application — existing `services/chat` + `services/frontend`, no new project.

**Performance Goals**: None formally required this phase (consistent with spec 001's SC-004) —
history hydration and clearing just need to complete without a noticeable stall at this phase's
demo scale.

**Constraints**: `Session.id` MUST be generated with `python-ulid`'s standard random-payload
constructor, never its monotonic factory, so it satisfies FR-017's non-guessability requirement
(research.md #1); session cookie is httponly + `SameSite=Lax`, not readable by frontend JavaScript,
`Secure=False` for now (no HTTPS in this phase — see research.md); no caller authentication
anywhere in this feature, matching spec 001's FR-011/FR-012 no-auth scope decision; chat
history has no automatic expiration (FR-011) and is only removed by an explicit
`DELETE /chat` (hard delete, FR-005) — which removes the `Chat`, not the `Session`,
so the same cookie keeps identifying the visitor across a clear; existing per-message validation
(1–2,000 chars, FR-008) and grounding/abstention behavior (FR-007) are unchanged; no fixed cap on
how many prior messages are fed to Claude as context this phase (spec.md Assumptions - since
superseded by spec 005, which bounds every model call to the last five turns); no `patients`
table or multi-chat-per-session behavior is built this phase (spec.md Future Direction is
explicitly deferred — only the `Session`/`Chat` seam is added now); no `staff` sender is
built this phase (`Message.sender` is an open set that anticipates it, FR-013, but only `patient`/
`assistant` values are ever written); the in-flight-generation registry (FR-015) is process-local —
correct for this phase's single `chat` process, but not designed to coordinate cancellation across
multiple instances, should the service ever be scaled horizontally (spec.md Assumptions explicitly
defers rate limiting/throttling, and this is the same "not this phase" posture applied to
cancellation's scaling limits).

**Scale/Scope**: Same portfolio-demo scale as spec 001 — single-digit to low-hundreds of FAQ
entries, a handful of concurrent visitors, chats of a few dozen turns at most; not tuned for
production load or unbounded context growth.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Phase-Gated Scope Discipline | PASS | No new service, no new external infrastructure, no agent framework — everything lands inside the existing `chat` service and its existing Postgres database. This feature explicitly supersedes spec 001's Phase-0 "single-turn, no memory" scope decision (spec.md Assumptions), which is not itself itemized in ROADMAP's phase table as a later-phase item — it's an application-level capability increment ROADMAP Phase 1a itself calls for (spec.md's **Implements** note), not the kind of infra/platform-layer creep this principle exists to block (no LangGraph, no MCP servers, no Scheduling changes, no new datastore). The extra `sessions` table is a schema seam, not new scope: it builds nothing from spec.md's Future Direction (no `patients` table, no multi-chat UI, no access-control logic, no `staff` sender despite `Message.sender` being open to one) — it only avoids near-certain breaking migrations later (a `patients` table, a `staff` sender), at the cost of one small table and one open-set column now. Cancel-and-restart's `generation_registry.py` (FR-015) is in-process `asyncio` state, not new infrastructure — explicitly the alternative chosen over a Redis-backed registry for exactly this reason (research.md #9). |
| II. AI Core Is the Centerpiece | PASS | Directly strengthens the agent's core behavior — grounded generation now reasons over chat context, not just a single isolated message. |
| III. Deliberate, Minimal Service Boundaries | PASS (N/A) | No new service boundary; `sessions`/`chats`/`messages` live in `chat`'s existing database, same as `faq_entries`. |
| IV. Structured Outputs & Decoupled Tool Interfaces | PASS | No new routing/classification introduced (still no intent classification this phase). `search_faq`'s interface is unchanged; chat history is passed as plain data into generation, not a new tool surface. |
| V. Grounded Retrieval with Mandatory Abstention | PASS | FR-007 requires grounding/abstention to keep applying per message, unchanged. `is_grounded` and citation derivation are untouched — only the generation call's `messages` list grows to include prior messages, and (research.md #6) retrieval's own query grows the same way for an unanswered burst, which strengthens rather than weakens groundedness: a fragment like "Dr. Josh?" that would otherwise retrieve on no useful signal and likely fail the gate now retrieves on the merged, meaningful query instead. |
| VI. Documentation as a First-Class Deliverable | PASS | research.md records the cookie/identity, `Session`/`Chat` split, the flat `Message` model's append-only persistence, history-construction, and the cancel-and-restart mechanism (§9), including why each is worth its small added cost now; a new "Conversational Chat History: technology choices" README section is a task for implementation, mirroring the existing two sections. |
| VII. Clean Architecture, SOLID & Design Patterns | PASS | `chat_repository.py` mirrors `faq_repository.py`'s stateless-function-takes-`AsyncSession` shape (the documented repository convention); `agent/answer_faq.py` stays persistence-ignorant, gaining only a plain `history` data parameter; the API layer keeps owning the transaction/cookie boundary, exactly like the existing `FaqEntry` CRUD flow. Separating `Session` (identity) from `Chat` (what that identity currently owns) is itself a single-responsibility split, not just forward-compatibility hedging — each row now answers exactly one question. The flat `Message` entity with an open `sender` set is a better open/closed fit than the paired-turn design it replaces: a third sender (`staff`) can be added later purely by extending the enum, no schema restructuring (FR-013) — SOLID's OCP applied to a data shape, not just to code. |
| VIII. Test-Driven Development (NON-NEGOTIABLE) | PASS (procedural gate, not a design gate) | Applies at the tasks/implementation stage; contracts and data model below define the testable surface for `/speckit-tasks` to sequence tests-before-implementation. |

No violations — Complexity Tracking table is empty (not needed).

**Post-Phase 1 re-check**: Re-evaluated against `data-model.md`, `contracts/openapi.yaml`, and
`quickstart.md` below — three new tables, two new endpoints, and one in-process cancellation
registry, all within the existing `chat` service/process, no new external dependency. Nothing
changes any principle's status above; all PASS results stand. (This re-check itself was re-run when
spec.md was later revised to the flat `Message` model and FR-015's cancel-and-restart behavior — see
this plan's Summary — and the conclusions were unchanged: still no new service, dependency, or
external infrastructure.)

## Project Structure

### Documentation (this feature)

```text
specs/003-conversational-chat-history/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/
│   └── openapi.yaml     # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
services/chat/
├── src/chat/
│   ├── api/
│   │   ├── chat.py                    # MODIFIED: one router, all three methods on /chat — resolve/
│   │   │                              # create session from cookie, get-or-create that session's
│   │   │                              # chat; POST inserts the patient message, cancels-and-registers
│   │   │                              # via generation_registry (FR-015), loads prior messages as
│   │   │                              # history, inserts the assistant message only on success, sets
│   │   │                              # the cookie only when the session is new; GET returns history
│   │   │                              # (FR-002); DELETE hard-deletes the chat only, session/cookie
│   │   │                              # untouched (FR-005) — GET/DELETE were originally planned as a
│   │   │                              # separate conversation.py router before the /conversation ->
│   │   │                              # /chat rename put them on the same path as the existing POST
│   │   │                              # handler, so they live in this one file instead
│   │   └── session_cookie.py          # NEW: cookie name + read/mint helpers used by chat.py's
│   │                                  # POST/GET/DELETE handlers — named for what it now holds (a
│   │                                  # Session id)
│   ├── agent/
│   │   ├── answer_faq.py              # MODIFIED: accepts a `history` parameter (prior messages as
│   │   │                              # Anthropic message dicts), prepended to the Claude call
│   │   ├── history.py                 # NEW: build_history_messages() — Message rows -> alternating
│   │   │                              # user/assistant messages, merging any consecutive same-sender
│   │   │                              # run (FR-014 bursts and FR-012 unanswered messages alike, see
│   │   │                              # research.md #5); its merged trailing user entry is also
│   │   │                              # reused as search_faq's query (research.md #6), not just fed
│   │   │                              # to Claude
│   │   └── generation_registry.py     # NEW: process-local dict[chat_id, asyncio.Task]
│   │                                  # implementing cancel-and-restart (FR-015, research.md #9) —
│   │                                  # register_and_cancel_previous(), clear_if_current()
│   ├── domain/
│   │   ├── models.py                  # MODIFIED: + Session, Chat, Message SQLAlchemy models
│   │   │                              # (Message.sender: patient | assistant, open enum, FR-013)
│   │   └── schemas.py                 # MODIFIED: + MessageOut, ChatHistoryResponse,
│   │                                  # ChatCancelledEvent
│   └── repositories/
│       └── chat_repository.py     # NEW: create_session, get_session,
│                                      # get_or_create_chat_for_session, create_message,
│                                      # list_messages, delete_chat (no "complete" step — a
│                                      # Message is written once, in full; research.md #3)
├── alembic/versions/
│   └── <new>_add_sessions_chats_and_messages.py   # NEW migration
└── tests/
    ├── test_chat_api.py                # MODIFIED: multi-turn context, cookie issuance/reuse,
    │                                    # cancellation producing a `cancelled` event (FR-015), and
    │                                    # GET/DELETE /chat coverage (folded in here, not a separate
    │                                    # test_conversation_api.py, matching api/chat.py above)
    ├── test_chat_repository.py         # NEW
    ├── test_history.py                 # NEW: build_history_messages() merge-logic unit tests
    ├── test_generation_registry.py     # NEW: cancel-and-restart unit tests (mocked task timing)
    └── test_models.py                  # MODIFIED: + new tables

services/frontend/
├── src/
│   ├── components/
│   │   ├── ChatWindow.tsx              # MODIFIED: flat message-list state, hydrates via
│   │   │                              # GET /chat on mount, renders the message list, hosts
│   │   │                              # the clear button, aborts its own in-flight fetch (via
│   │   │                              # AbortController) when the patient sends a new message; on a
│   │   │                              # `cancelled` event (FR-015) removes the in-progress bubble and
│   │   │                              # any partial tokens painted for it entirely — no error state,
│   │   │                              # no leftover text (research.md #10); tokens still paint live
│   │   │                              # as they arrive otherwise, unchanged from spec 001's FR-004
│   │   ├── MessageView.tsx             # NEW: renders one message by sender (patient/assistant) with
│   │   │                              # no derived "unanswered" treatment — a patient message with no
│   │   │                              # reply yet is the normal shape of a mid-burst message (FR-014),
│   │   │                              # not a failure signal (research.md #8); reused for historical
│   │   │                              # and the in-progress streaming message
│   │   └── ClearChatButton.tsx # NEW: button + confirmation dialog ("All messages in the
│   │                                  # chat will be deleted. Do you agree?" / Clear /
│   │                                  # Cancel, per FR-004), calls DELETE /chat
│   └── lib/
│       └── chatStream.ts               # MODIFIED: + fetchChatHistory(), clearChat(),
│                                      # handling for the `cancelled` streamed event (FR-015)
└── tests/
    ├── ChatWindow.test.tsx             # MODIFIED
    ├── MessageView.test.tsx            # NEW
    └── ClearChatButton.test.tsx # NEW

tests/integration/                      # unchanged placeholder — no cross-service surface here
tests/e2e/                              # unchanged placeholder
```

**Structure Decision**: Same two-project monorepo layout as spec 001 — no new service, no new
workspace member. Backend additions mirror the existing `FaqEntry` pattern exactly (a
`domain/models.py` SQLAlchemy model, a stateless `repositories/*.py` module taking `AsyncSession`
explicitly, a thin `api/*.py` router owning the transaction boundary), so `Session`/`Chat`/
`Message` read as a natural extension of the codebase rather than a parallel style. `Session` lives
in the same `domain/models.py` and `chat_repository.py` as `Chat` rather than its
own module — it's one extra table and a handful of functions, not a separate concern that justifies
its own files yet; that split can happen later if/when Patient support actually lands and
`chat_repository.py` starts feeling overloaded. `generation_registry.py` is its own small
module under `agent/` rather than folded into `chat.py`, since it's pure in-memory coordination logic
with its own focused unit tests (`test_generation_registry.py`), independent of the HTTP layer that
calls it. The frontend gains its first multi-component structure (`ChatWindow` was previously the
only component); `vite.config.ts` is unchanged — `GET`/`DELETE /chat` reuse the `/chat` path spec
001's dev proxy already forwards for `POST /chat`, unlike the original `/conversation` design, which
would have needed a new proxy entry. `packages/shared-models` and `packages/shared-proto` are
untouched — this feature has no cross-service surface (Scheduling is unaffected), so
`tests/integration/` and
`tests/e2e/` stay placeholders.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations — table intentionally empty.
