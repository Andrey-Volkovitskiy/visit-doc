# Implementation Plan: Scheduling Service and End-to-End Booking (Phase 1c)

**Branch**: `005-scheduling-and-booking` | **Date**: 2026-08-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/005-scheduling-and-booking/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

Three bodies of work land together, per ROADMAP Phase 1c.

**(1) `services/scheduler` becomes a real service.** Today it is a `print("Hello from scheduler!")`
placeholder. It gains its own PostgreSQL database (`visitdoc_scheduler`), its own Alembic tree, the
whole scheduling domain — patients, practitioners, working ranges, appointments — a gRPC server for
the chat service, and a REST admin surface for direct management (FR-048). Double booking is
prevented by two PostgreSQL **exclusion constraints** on `tsrange(starts_at, ends_at)`, one keyed by
patient and one by practitioner, so two concurrent attempts on one slot cannot both succeed
(FR-016/FR-017, SC-002). The "inside a working range" and "on the slot grid" rules are deliberately
*not* constraints: FR-022 grandfathers existing appointments through a schedule edit, so those two
are creation-time predicates evaluated against the practitioner's settings at that moment
(research.md #6). Every time in the service is a **timezone-naive local wall-clock** value —
`TIMESTAMP WITHOUT TIME ZONE`, `tsrange` not `tstzrange` — and every past/upcoming/horizon judgement
is made against the client's `local_now`, never the server's clock (FR-058, research.md #5/#20).

**(2) The chat service gains a booking path and a real chat list.** `chats` gets a nullable
`patient_id`; `GET`/`DELETE /chat` are replaced by a `/chats` resource (list, create, delete,
per-chat history), and `POST /chat` gains a required `chat_id` and `local_now` (research.md #19).
Scheduling capabilities reach the agent through an in-process **tool registry** —
`list_practitioners`, `check_availability`, `book_appointment`, `list_my_appointments` — whose
handlers own the gRPC stub, the 2-second deadline, and the single retry, so the agent node never
sees a wire format (Constitution IV; research.md #1). Chat creation never blocks on the scheduler:
the `Chat` row is committed first, provisioning is attempted under the same bounded budget, and a
failure leaves an unnamed but fully working FAQ chat whose patient is created on a later interaction
(FR-044/FR-045, research.md #10).

**(3) The graph starts branching for real.** `classify_intent` becomes a router that fans out to the
FAQ specialist, the booking specialist, or **both concurrently**, with a `compose_answer` node
generating one coherent reply when both ran:

```
                       ┌──> answer_faq ─────┐
START ──> classify_intent                    ├──> compose_answer ──> END
                       └──> handle_booking ─┘
```

A single-specialist turn streams straight from its specialist and `compose_answer` is a no-op, so
the FAQ path keeps today's latency and behavior byte-for-byte (FR-034). This was the user's explicit
choice during planning over strict single-path routing, and it **pulls forward** work the ROADMAP
assigns to Phase 1d — see Complexity Tracking.

Also in scope: 100 writer + 20 physician name pools with deterministic per-session allocation
(FR-011/FR-013), derived idempotency keys so a lost booking confirmation is never reported as a
conflict with the patient's own appointment (FR-051), a cross-service deletion that removes chat,
messages, patient, and appointments together (FR-039), and the frontend chat list. **Out of scope**
by spec: cancellation, rescheduling, escalation, staff, and any calendar exception (all Phase 1d).

## Technical Context

**Language/Version**: Python 3.12 across all workspace members (`.python-version`); TypeScript/React
19 + Vite for `services/frontend`.

**Primary Dependencies**:
- `services/scheduler` — currently depends on nothing but the two shared packages. Gains
  `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]`, `asyncpg`, `psycopg[binary]`, `alembic`,
  `pydantic-settings`, `structlog`, `python-ulid`, `grpcio` (via `shared-proto`) — deliberately the
  same set `services/chat` already resolves in the shared `uv.lock`, so no new version pins enter
  the workspace.
- `services/chat` — **no new third-party dependency**. `grpcio` arrives through the existing
  `shared-proto` workspace dependency; the tool-use loop is the `anthropic` SDK it already uses; the
  fan-out is `langgraph`'s own conditional edges.
- `packages/shared-proto` — the placeholder `scheduling.proto` is rewritten wholesale and stubs
  regenerated (including the manual import fixup in its README).
- `packages/shared-models` — gains its first real content: `Specialty` (the ten FR-005 values),
  `Weekday`, `BookingFailureReason`, and the local-date-time parse/format helpers both services
  validate with (research.md #17).
- Frontend — no new dependency; the chat list is plain React state over the new `/chats` routes.

**Storage**: Two PostgreSQL databases, each named for its owning service — chat's is **renamed**
`visitdoc` → `visitdoc_chat` (and `visitdoc_test` → `visitdoc_chat_test`) in this change, so neither
store reads as the project-wide default (FR-059, research.md #26). That rename is configuration only:
no code contains the name, and the test harness derives its suffixed name from `DATABASE_URL`
automatically. `visitdoc_chat` gets one migration: `chats.patient_id`,
`VARCHAR(26) NULL`, indexed, no foreign key — cross-database references are opaque ids only.
`visitdoc_scheduler` is new, with four tables, the `btree_gist` extension, a `CREATE TYPE timerange
AS RANGE (subtype = time)` for the working-range non-overlap constraint (research.md #7), and its
own Alembic history. Locally both live in the existing `visitdoc-postgres` container as separate
logical databases (research.md #15). Qdrant is untouched.

**Testing**: pytest across four unit tiers plus a real integration tier for the first time.
`services/scheduler/tests/` is new and hits a real `visitdoc_scheduler_test` database, mirroring
chat's conftest — env override before any `scheduler.*` import, session-scoped `alembic upgrade
head`, and **both** halves of the event-loop fix (`asyncio_*_loop_scope = "session"` plus the
`engine.dispose()` autouse fixture) that `docs/testing-strategy.md` already warns this service will
need. gRPC handlers are tested over an in-process channel. `services/chat/tests/` fakes the
scheduling stub at the client-module boundary. `tests/integration/` stops being a placeholder: chat's
gRPC client against a real servicer and a real scheduler database, covering the booking round trip,
the idempotent replay, and the deletion cascade. Frontend: vitest for the chat list, switching,
deletion, the muted zero-chat state, and that `local_now` is sent. Every turn-exercising test keeps
mocking `AsyncAnthropic` per `docs/testing-strategy.md`, now including the tool-use responses that
drive the booking loop (research.md #22). One targeted regression case per specialist asserts the
5-turn context bound directly — a chat with more than five turns must reach the Anthropic mock with
exactly the last five, and the booking loop's within-turn `tool_result` blocks must survive it
(research.md #23). Log assertions follow spec 004's precedent, which established that the observable
record is part of the contract: every node emits its lifecycle pair with the right `node` and
`result`; a node that raises emits `node.failed` while a turn that still answers does **not** emit
`turn.error`; `turn.completed` is emitted exactly once per turn on all three paths (FAQ-only,
booking-only, merged); and each booking tool iteration leaves a `booking.tool_called` /
`booking.tool_result` pair (research.md #24).

**Target Platform**: Linux server; three local processes (chat `:8000`, scheduler `:8001` HTTP +
`:50051` gRPC, Vite `:5173`) plus the Docker Compose Postgres/Qdrant. The scheduler runs both its
servers in one process, with the `grpc.aio` server started and stopped inside FastAPI's `lifespan`
(research.md #14).

**Project Type**: Web application, now genuinely multi-service — the first feature where the
chat↔scheduler boundary carries real traffic.

**Performance Goals**: SC-013 governs — with the scheduler unresponsive, the patient gets the
"temporarily unavailable" reply within 5 seconds, 100% of the time. The budget is FR-047's: a 2s
deadline per call and at most 2 attempts, retried only on `UNAVAILABLE`/`DEADLINE_EXCEEDED`, worst
case ~4s plus generation overhead. A healthy `check_availability` is a single indexed range query
and is expected in the low tens of milliseconds, so the booking loop's latency is dominated by its
model calls, not by the service boundary. `compose_answer` adds one generation call, but only to
mixed-intent turns — the FAQ-only path is unchanged.

**Constraints**:
- **No timezone anywhere** (FR-033/FR-043). No `TIMESTAMPTZ` on any domain column, no `tzinfo` on
  any domain value, no `google.protobuf.Timestamp` on the wire, no zone identifier stored or
  configurable. Enforced by validators at both service boundaries.
- **The client's clock decides** every past/upcoming/horizon question (FR-058). `local_now` is a
  required field on `POST /chat` and on every RPC that makes such a judgement; the scheduler calls
  no clock for domain logic.
- **Integrity in the datastore** (Constitution III, FR-016/FR-017): overlap prevention is an
  exclusion constraint, not application code.
- **Session isolation** (FR-002, SC-004): every scheduler query filters on `session_id`; every chat
  route is scoped to the cookie's session; ids that belong elsewhere 404 rather than 403.
- **Never claim an unmade booking** (FR-028, SC-008): the reply's truth claim is constrained by a
  machine-derived `BookingOutcome`, never by generated prose.
- **Every model call sees at most the last 5 turns.** `answer_faq` gains the bound
  `classify_intent_node` already applies, and `handle_booking` uses the same one (research.md #23) —
  so a turn's three-to-nine model calls no longer each carry an unbounded, permanently growing chat
  history. This supersedes spec 003's "no fixed cap on context growth" assumption, which is
  annotated in that spec as part of this change. Storage and history endpoints are unaffected.
- **Booking only** — no cancel, no reschedule, no escalation path (spec Assumptions).

**Scale/Scope**: Portfolio-demo scale, unchanged from specs 001–004: a handful of concurrent
visitors, a session holding up to a few hundred chats in the SC-007 pool-exhaustion test, chats of a
few dozen turns. Availability windows are capped server-side at 14 days and 50 returned starts
(spec FR-067, research.md #21) so a vague request cannot flood a model's context; an over-wide
request is clamped and marked truncated rather than refused.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Phase-Gated Scope Discipline | **PASS with deviations** | The service, the booking path, the read-only answers, the local-time model, and the chat-list consequence are ROADMAP 1c verbatim; cancellation, rescheduling, escalation, and staff stay out (spec Assumptions). Two documented departures, both recorded in Complexity Tracking and both requiring a ROADMAP amendment in this same change: the capability seam is a tool registry rather than MCP tool servers (research.md #1), and parallel specialists with a merge step are pulled forward from 1d (research.md #2). No new infrastructure beyond the one database the ROADMAP already assigns to this service; no Phase 3+ platform layer. |
| II. AI Core Is the Centerpiece | PASS | The agent's first real branching, its first tool use, and its first write capability. The service exists to give those something true to act on; its scope is bounded to exactly what the tools call. |
| III. Deliberate, Minimal Service Boundaries | PASS | This is the one boundary the project ever intended to build, and it is built the way the principle demands: its own datastore, its own contract, and integrity enforced *there* — two exclusion constraints, plus the FK cascades that make FR-049/FR-055 structural rather than procedural. Failure handling is designed, not deferred: a typed refusal taxonomy distinct from transport failure, a 2s/2-attempt budget, derived idempotency keys that make the retry safe, and defined degraded behavior for chat creation, booking, and deletion (research.md #9/#10/#11). |
| IV. Structured Outputs & Decoupled Tool Interfaces | PASS | Every capability is behind a tool-call interface with a closed JSON schema, and the handlers own all provider knowledge — the booking node never imports a stub or a protobuf type. `session_id`/`patient_id`/`local_now` are ambient, never model-supplied, so a model can neither cross a session nor substitute a clock. Intent classification keeps 1b's structured output on the cheap model; the strong model is reserved for generation and the tool loop. The seam is not MCP — see Complexity Tracking. |
| V. Grounded Retrieval with Mandatory Abstention | PASS | The FAQ pipeline (retrieve → `is_grounded` → generate, with citations derived structurally) is unchanged (FR-034), and the merge step preserves it: citations still come from the chunks actually retrieved and are carried through state, never re-reported by the composing model; an abstaining FAQ half must be composed as an abstention rather than filled in (research.md #2). Escalation-on-abstention remains Phase 1d/1e. |
| VI. Documentation as a First-Class Deliverable | PASS | research.md records 26 decisions with rationale and rejected alternatives; five contract files define the wire, HTTP, tool, and log surfaces. The change carries its own documentation edits: README entries for the scheduling service, the exclusion-constraint choice, the no-timezone model, the tool-registry seam, and the shared-container tradeoff; the two ROADMAP amendments; `docs/testing-strategy.md` and `.claude/CLAUDE.local.md` updated for the scheduler tier, the now-real integration tier, and FR-059's renamed databases; and the spec.md assumption superseded by research.md #2. |
| VII. Clean Architecture, SOLID & Design Patterns | PASS | The scheduler mirrors chat's proven layering (`api`/`core`/`db`/`domain`/`repositories`) rather than inventing a second house style, and keeps the established repository shape — module-level functions taking `AsyncSession` explicitly, transaction boundary owned by the caller. Slot generation lives in one `domain/availability.py` used by *both* `CheckAvailability` and `BookAppointment`, which is what makes FR-025/SC-009 true by construction instead of by two implementations agreeing. On the chat side, the gRPC client is one module with one failure taxonomy, tool handlers are thin adapters over it, and the graph nodes depend only on domain result types. |
| VIII. Test-Driven Development (NON-NEGOTIABLE) | PASS (procedural gate) | The contracts above define the testable surface — proto messages and failure enum, two OpenAPI documents, tool schemas and result shapes, log events, and data-model.md's enforcement table — for `/speckit-tasks` to sequence tests-before-implementation against. The database-level rules (both exclusion constraints, both cascades, the two UNIQUE constraints) are testable directly against a real database before any service code exists, which is where the ordering matters most here. |

**Post-Phase 1 re-check**: Re-evaluated against data-model.md, the four contracts, and quickstart.md.
The design adds one service build-out, one chat migration, one proto rewrite, four new chat modules
(`clients/scheduling.py`, `agent/tools/`, `agent/handle_booking.py`, `agent/compose_answer.py`), and
no new third-party dependency for `chat`. Nothing changed a status above; the two Principle I
deviations were known before Phase 0 and are unchanged in scope after Phase 1.

## Project Structure

### Documentation (this feature)

```text
specs/005-scheduling-and-booking/
├── plan.md                       # This file (/speckit-plan command output)
├── research.md                   # Phase 0 output — 26 decisions
├── data-model.md                 # Phase 1 output
├── quickstart.md                 # Phase 1 output
├── contracts/                    # Phase 1 output
│   ├── scheduling.proto          #   chat <-> scheduler gRPC contract
│   ├── chat-api.yaml             #   chat service HTTP (the /chats resource + POST /chat)
│   ├── scheduler-admin-api.yaml  #   scheduler REST admin surface (FR-048)
│   ├── agent-tools.md            #   tool registry: schemas, results, loop rules
│   └── log-events.md             #   new structured log events, both services
├── checklists/
│   ├── requirements.md           # pre-existing, fully resolved
│   └── booking-integrity.md      # overlap / grid / horizon / idempotency review, fully resolved
│                                 #   2026-08-13; its findings added FR-061..FR-067
└── tasks.md                      # Phase 2 output (/speckit-tasks — NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
services/scheduler/                          # placeholder today -> real service
├── pyproject.toml                           # MODIFIED: + fastapi, uvicorn, sqlalchemy[asyncio],
│                                            #   asyncpg, psycopg, alembic, pydantic-settings,
│                                            #   structlog, python-ulid
├── alembic.ini                              # NEW (mirrors services/chat/alembic.ini)
├── alembic/
│   ├── env.py                               # NEW: reads SCHEDULER_DATABASE_URL, like chat's
│   └── versions/
│       └── *_create_scheduling_schema.py    # NEW: btree_gist, CREATE TYPE timerange,
│                                            #   practitioners / working_ranges / patients /
│                                            #   appointments, both EXCLUDE constraints, both
│                                            #   cascades, both UNIQUEs, and created_at/updated_at
│                                            #   on every entity table (data-model.md)
├── src/scheduler/
│   ├── main.py                              # REWRITTEN: create_app() + lifespan that starts and
│   │                                        #   gracefully stops the grpc.aio server alongside
│   │                                        #   uvicorn (research.md #14)
│   ├── core/
│   │   ├── config.py                        # NEW: Settings — SCHEDULER_DATABASE_URL, GRPC_PORT,
│   │   │                                    #   HTTP_PORT, BOOKING_HORIZON_DAYS=90
│   │   ├── logging.py                       # NEW: mirrors chat's chain + the two secret-constant
│   │   │                                    #   lists, with DATABASE_URL in the URL-secret list
│   │   └── correlation.py                   # NEW: binds the x-turn-id from gRPC metadata (#18)
│   ├── db/session.py                        # NEW: async engine + session_factory (chat's shape)
│   ├── domain/
│   │   ├── models.py                        # NEW: Practitioner, WorkingRange, Patient, Appointment
│   │   ├── schemas.py                       # NEW: admin-API DTOs (naive-datetime validators)
│   │   ├── availability.py                  # NEW: the slot grid — walk each working range in
│   │   │                                    #   duration-length steps, drop partial trailing
│   │   │                                    #   remainders, subtract intervals booked by the
│   │   │                                    #   PRACTITIONER *and* by the requesting PATIENT
│   │   │                                    #   (FR-024 — availability is patient-relative), apply
│   │   │                                    #   the past/horizon filters against the caller's
│   │   │                                    #   local_now. Overlap is half-open throughout (FR-061),
│   │   │                                    #   matching the exclusion constraints. ONE
│   │   │                                    #   implementation, used by both CheckAvailability and
│   │   │                                    #   BookAppointment's validator (FR-025/SC-009, #21)
│   │   ├── name_pools.py                    # NEW: 100 writers, 20 physicians, as ordered tuples
│   │   └── naming.py                        # NEW: deterministic pool walk + " 2"/" 3" passes
│   ├── repositories/
│   │   ├── practitioner_repository.py       # NEW  (session-as-parameter, like chat's)
│   │   ├── patient_repository.py            # NEW
│   │   └── appointment_repository.py        # NEW: booking insert, idempotency lookup, overlap
│   │                                        #   IntegrityError -> typed BookingFailureReason
│   ├── api/
│   │   ├── dependencies.py                  # NEW: X-Session-Id extraction (401 when absent)
│   │   ├── practitioners.py                 # NEW: FR-048 CRUD
│   │   ├── patients.py                      # NEW: list + rename only (FR-048)
│   │   ├── specialties.py                   # NEW: GET /specialties — the ten, name-sorted, so a
│   │   │                                    #   dropdown is populated from the service rather than
│   │   │                                    #   a copy in the client (FR-060). Not session-scoped
│   │   └── health.py                        # NEW
│   └── grpc/
│       ├── server.py                        # NEW: grpc.aio server wiring + interceptor
│       ├── servicer.py                      # NEW: the six RPCs; domain refusals as typed
│       │                                    #   responses, transport failures as status codes
│       ├── converters.py                    # NEW: proto <-> domain, incl. the ISO-8601 local
│       │                                    #   date-time format and its no-offset validation
│       └── interceptors.py                  # NEW: bind x-turn-id, emit rpc.received/rpc.completed
└── tests/                                   # NEW tier: constraints, availability grid, naming
                                             #   determinism, idempotent replay, cascades, servicer
                                             #   over an in-process channel, admin API, session
                                             #   isolation

packages/shared-proto/
├── protos/scheduling/v1/scheduling.proto    # REWRITTEN from the placeholder (contracts/)
└── src/shared_proto/scheduling/v1/*_pb2*.py # REGENERATED (+ the README's manual import fixup)

packages/shared-models/
├── src/shared_models/
│   ├── scheduling.py                        # NEW: Specialty (ten values, display names as the
│   │                                        #   enum values), Weekday, BookingFailureReason
│   └── localtime.py                         # NEW: parse/format naive local date-times (#5/#17)
└── tests/                                   # NEW cases for both

services/chat/
├── alembic/versions/*_add_patient_id_to_chats.py  # NEW migration (nullable, indexed, no FK)
├── src/chat/
│   ├── core/config.py                       # MODIFIED: + SCHEDULING_GRPC_TARGET,
│   │                                        #   SCHEDULING_TIMEOUT_SECONDS, SCHEDULING_MAX_ATTEMPTS
│   ├── clients/scheduling.py                # NEW: the ONLY module importing shared_proto. Owns the
│   │                                        #   channel (built in lifespan), the 2s deadline, the
│   │                                        #   single retry on UNAVAILABLE/DEADLINE_EXCEEDED, the
│   │                                        #   x-turn-id metadata, and the mapping to domain
│   │                                        #   results. Raises SchedulingUnavailableError only for
│   │                                        #   transport failure (research.md #9)
│   ├── domain/
│   │   ├── models.py                        # MODIFIED: Chat gains patient_id
│   │   └── schemas.py                       # MODIFIED: ChatRequest + chat_id/local_now (naive-only
│   │                                        #   validator); ChatDoneEvent + answer_source, grounded
│   │                                        #   becomes bool|None; + ChatSummary/ChatListResponse
│   ├── repositories/chat_repository.py      # MODIFIED: get_or_create_chat_for_session and
│   │                                        #   get_chat_for_session REMOVED; + list_chats_for_
│   │                                        #   session (FR-056 ordering), create_chat, get_chat
│   │                                        #   (session-scoped), set_patient_id
│   ├── api/
│   │   ├── chats.py                         # NEW: GET/POST /chats, DELETE /chats/{id},
│   │   │                                    #   GET /chats/{id}/messages. Deletion cancels the
│   │   │                                    #   in-flight turn, then scheduler, then local rows
│   │   └── chat.py                          # MODIFIED: takes chat_id instead of resolving "the"
│   │                                        #   chat; lazy patient provisioning when patient_id is
│   │                                        #   NULL; passes local_now/session_id/chat_id/
│   │                                        #   patient_id into graph state
│   ├── agent/
│   │   ├── graph.py                         # MODIFIED: classify_intent becomes a router writing
│   │   │                                    #   intents/merge_required and fanning out via a
│   │   │                                    #   conditional edge; both specialists edge into
│   │   │                                    #   compose_answer; state gains disjoint result keys so
│   │   │                                    #   concurrent branches need no reducer
│   │   ├── answer_faq.py                    # MODIFIED (small): in merge mode, returns a FaqResult
│   │   │                                    #   instead of streaming/emitting; bounds its own
│   │   │                                    #   context to the last 5 turns via history.py's
│   │   │                                    #   bound_to_last_n_turns, as classify_intent_node
│   │   │                                    #   already does (research.md #23); + faq.retrieved;
│   │   │                                    #   LOSES turn.completed, which moves to
│   │   │                                    #   compose_answer (#24). Retrieval, groundedness gate,
│   │   │                                    #   citation derivation untouched
│   │   ├── handle_booking.py                # NEW: the bounded tool-use loop (research.md #3),
│   │   │                                    #   producing BookingResult + BookingOutcome; bounds
│   │   │                                    #   its conversation context the same way (#23); emits
│   │   │                                    #   booking.tool_called/tool_result/tool_failed per
│   │   │                                    #   iteration (#24)
│   │   ├── compose_answer.py                # NEW: no-op for a single specialist; otherwise one
│   │   │                                    #   Sonnet call composing both results, carrying the
│   │   │                                    #   FAQ half's citations through structurally. The one
│   │   │                                    #   node that emits turn.completed, on every path (#24)
│   │   ├── node_logging.py                  # NEW: node_span() — binds `node` into contextvars for
│   │   │                                    #   the node's duration, times it, emits node.started/
│   │   │                                    #   completed/failed/cancelled. Every event inside a
│   │   │                                    #   node inherits the name with no call site passing it
│   │   ├── tools/
│   │   │   ├── registry.py                  # NEW: (name, description, schema, handler) records ->
│   │   │   │                                #   Anthropic tools=, plus dispatch. Ambient args bound
│   │   │   │                                #   per turn, never model-supplied
│   │   │   └── scheduling_tools.py          # NEW: the four handlers (contracts/agent-tools.md)
│   │   └── generation_registry.py           # MODIFIED: + cancel-by-chat, for FR-055's mid-turn
│   │                                        #   deletion
│   └── main.py                              # MODIFIED: gRPC channel on app.state via the existing
│                                            #   AsyncExitStack; chats router included
└── tests/                                   # MODIFIED/NEW: test_chats_api, test_scheduling_client,
                                             #   test_scheduling_tools, test_handle_booking,
                                             #   test_compose_answer, test_graph (routing + fan-out),
                                             #   test_chat_api (chat_id/local_now/degraded),
                                             #   test_migrations

services/frontend/src/
├── lib/chatStream.ts                        # MODIFIED: /chats calls; askChat takes chat_id +
│                                            #   local_now; ChatDoneEvent gains answer_source and
│                                            #   grounded: boolean|null; render `message` if present
│                                            #   else accumulated tokens
├── components/ChatList.tsx                  # NEW: the session's chats, create, switch, delete;
│                                            #   "Unnamed · HH:MM" for a patient-less chat (FR-054)
├── components/ChatWindow.tsx                # MODIFIED: driven by an active chat id; muted when the
│                                            #   session has no chats (FR-041)
├── components/ClearChatButton.tsx           # REMOVED: replaced by per-chat delete (FR-039)
└── App.tsx                                  # MODIFIED: owns the chat list + active chat selection

tests/integration/                            # NOW REAL: chat's gRPC client against a live servicer
                                              #   and a real scheduler database
docker/postgres-init/01-create-test-db.sql    # MODIFIED: visitdoc_test -> visitdoc_chat_test (#26)
docker/postgres-init/02-create-scheduler-dbs.sql   # NEW: visitdoc_scheduler + _test
docker-compose.yml                            # MODIFIED: POSTGRES_DB default -> visitdoc_chat (#26)
.github/workflows/ci.yml                      # MODIFIED: test job's POSTGRES_DB -> visitdoc_chat_test,
                                              #   + visitdoc_scheduler_test, + an integration job
.env / .env.example                           # MODIFIED: DATABASE_URL's database segment (#26)
Makefile, README.md, docs/ROADMAP.md,
docs/testing-strategy.md, .claude/CLAUDE.local.md   # MODIFIED (see below)
```

**Structure Decision**: Multi-service, using the workspace exactly as it was laid out for this
moment. `services/scheduler` mirrors `services/chat`'s layering rather than inventing a second house
style, so a reader who has read one service can read the other; its one structural addition is a
`grpc/` package, since the gRPC surface is a genuinely separate delivery mechanism from the REST
admin app and both sit above the same repositories. `packages/shared-proto` holds the contract and
`packages/shared-models` the shared vocabulary — the split the monorepo already declared and this is
the feature that populates it. On the chat side, everything provider-specific is confined to
`clients/scheduling.py`: the tool handlers depend on its domain result types, and the graph nodes
depend on the handlers' results, so no orchestration code imports a protobuf type
(`.claude/CLAUDE.md`'s dependency-inversion rule). `domain/availability.py` is a single module used
by both the availability RPC and the booking validator, which is the structural reason FR-025 holds.

**Documentation changes carried by this feature** (Constitution VI — same change, not follow-up):
`docs/ROADMAP.md`'s two amendments (tool-registry seam replacing the MCP-tool-servers bullet;
parallel specialists + merge moved from 1d to 1c); the spec.md Assumption that the merge step is
Phase 1d, superseded by research.md #2; spec 003's "no fixed cap on context growth" assumption,
superseded by research.md #23; README entries for the scheduling service, the
exclusion-constraint approach, the timezone-free time model, the tool-registry seam, and the
shared-container tradeoff; `docs/testing-strategy.md` for the scheduler unit tier, the now-populated
integration tier, and the renamed chat databases in its worked example; `.claude/CLAUDE.local.md`'s
`docker exec … psql` recipe, which names both old databases; and CI provisioning of
`visitdoc_chat_test`/`visitdoc_scheduler_test` plus an integration-test job. The FR-059 rename must
land in every one of these at once — a single stale reference is a broken setup for the next person
who follows it (research.md #26).

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| **Capability seam is a tool registry, not the MCP tool servers ROADMAP Phase 1c specifies** (Principle I: the ROADMAP is binding) | User decision during planning. Constitution IV's actual requirement — every capability behind a tool-call interface, agent reasoning decoupled from implementation — is met in full by the registry: the booking node knows only names and JSON schemas, and swapping a handler for an MCP client later changes no agent code. | The *simpler* alternative here is the one being kept; the ROADMAP's version is the more complex one. Standing up a FastMCP server plus an MCP client inside a single process adds two dependencies, a loopback hop, and JSON-RPC error plumbing between an agent and handlers already sharing an address space — machinery whose only real payoff, cross-process reuse by a third-party client, nothing in this phase consumes. The ROADMAP bullet is amended in this change and MCP moves to a later phase (research.md #1). |
| **Parallel specialist nodes + a merge step, pulled forward from ROADMAP Phase 1d into 1c** (Principle I: build in phase order, don't add beyond what the phase calls for) | User decision during planning, overriding both the ROADMAP's 1d placement and spec.md's own Assumption. A message like "what should I bring, and can I book Friday?" is ordinary phrasing, and once 1c has two real specialists, routing it to only one produces a visibly partial answer. It also makes the graph's branching real, which is what LangGraph was adopted for in 1b. | *Strict single-path routing* (booking wins, FAQ half deferred to a follow-up question) was the simpler option and was explicitly rejected by the user: it ships a known-partial answer for the rest of the phase. *FAQ wins on mixed* is simpler still and worse — it silently drops the actionable half. The added cost is bounded: one extra generation call on mixed turns only, disjoint state keys so no channel reducer is needed, and a no-op merge node on single-specialist turns so the FAQ path keeps today's latency and behavior (FR-034, research.md #2). |
