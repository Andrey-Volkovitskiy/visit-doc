---

description: "Task list for Scheduling Service and End-to-End Booking (Phase 1c)"
---

# Tasks: Scheduling Service and End-to-End Booking (Phase 1c)

**Input**: Design documents from `/specs/005-scheduling-and-booking/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: Per Constitution Principle VIII (Test-Driven Development, NON-NEGOTIABLE), test tasks are
mandatory and MUST precede their implementation tasks: contract → test cases → tests (observed
failing) → implementation → tests run (observed passing). Every task group below is ordered that way;
do not reorder it.

**Organization**: Tasks are grouped by user story so each story can be implemented, tested, and
demoed independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US5)
- Exact file paths are included in every task

## Path Conventions

`uv` workspace monorepo (see plan.md "Source Code"): `services/chat/src/chat/`,
`services/scheduler/src/scheduler/`, `services/frontend/src/`, `packages/shared-models/src/`,
`packages/shared-proto/src/`. Unit tests are colocated per member (`<member>/tests/`);
cross-service tests live at `tests/integration/`.

**Mocking discipline** (`docs/testing-strategy.md`): every test that exercises a turn MUST mock
`AsyncAnthropic` — including the tool-use responses that drive the booking loop — and assert on
unmocked artifacts (rows written, gRPC requests issued), never on canned model text.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Rename the chat databases (FR-059), provision the scheduler's, and give the scheduler
service its dependencies and commands.

- [X] T001 [P] Change `POSTGRES_DB` default from `visitdoc` to `visitdoc_chat` in `docker-compose.yml`
- [X] T002 [P] Rename the created database from `visitdoc_test` to `visitdoc_chat_test` in `docker/postgres-init/01-create-test-db.sql`
- [X] T003 [P] Create `docker/postgres-init/02-create-scheduler-dbs.sql` creating `visitdoc_scheduler` and `visitdoc_scheduler_test`
- [X] T004 Update `.env` and `.env.example`: point `DATABASE_URL` at `visitdoc_chat`, add `SCHEDULING_GRPC_TARGET`/`SCHEDULING_TIMEOUT_SECONDS`/`SCHEDULING_MAX_ATTEMPTS`, add `SCHEDULER_DATABASE_URL`/`SCHEDULER_GRPC_PORT`/`SCHEDULER_HTTP_PORT` (values in quickstart.md "Prerequisites")
- [X] T005 Rename the existing local databases with the `ALTER DATABASE` commands in quickstart.md "Prerequisites" (chat service stopped), or `make db-reset && make db-up` to start clean
- [X] T006 Add the scheduler's runtime dependencies with `uv add --package scheduler fastapi "uvicorn[standard]" "sqlalchemy[asyncio]" asyncpg "psycopg[binary]" alembic pydantic-settings structlog python-ulid`, then `uv lock`
- [X] T007 [P] Add `run-scheduler-dev` and `alembic-scheduler-history` targets to `Makefile`, mirroring the existing `run-chat-dev`/`alembic-chat-history`
- [X] T008 [P] Update `.github/workflows/ci.yml`: the `test` job's `POSTGRES_DB` becomes `visitdoc_chat_test`, and `visitdoc_scheduler_test` is provisioned alongside it

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The shared vocabulary, the gRPC contract, the scheduler's whole skeleton and schema, and
the chat service's `/chats` resource. Nothing in any user story can be built on top of a missing
schema or a placeholder proto.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Shared packages

- [X] T009 [P] Write failing tests for `Specialty` (ten values, display-name values), `Weekday`, and `BookingFailureReason` in `packages/shared-models/tests/test_scheduling.py`
- [X] T010 [P] Write failing tests for `parse_local_datetime`/`format_local_datetime` — round-trip, and rejection of any offset, `Z`, or tz-aware input — in `packages/shared-models/tests/test_localtime.py`
- [X] T011 [P] Implement `packages/shared-models/src/shared_models/scheduling.py` per data-model.md "packages/shared-models"
- [X] T012 [P] Implement `packages/shared-models/src/shared_models/localtime.py` per research.md #5
- [X] T013 Replace `packages/shared-proto/protos/scheduling/v1/scheduling.proto` wholesale with [contracts/scheduling.proto](./contracts/scheduling.proto)
- [X] T014 Regenerate the stubs into `packages/shared-proto/src/shared_proto/scheduling/v1/` per `packages/shared-proto/README.md`, including the manual import fixup
- [X] T015 [P] Extend `packages/shared-proto/tests/test_smoke.py` to assert the six RPCs and `CheckAvailabilityRequest.patient_id` exist on the regenerated stubs

### Scheduler skeleton

- [X] T016 [P] Create `services/scheduler/src/scheduler/core/config.py` with `Settings` (`SCHEDULER_DATABASE_URL`, `GRPC_PORT`, `HTTP_PORT`, `BOOKING_HORIZON_DAYS=90`) and an `lru_cache`d `get_settings()`, mirroring chat's
- [X] T017 [P] Create `services/scheduler/src/scheduler/core/logging.py` mirroring `services/chat/src/chat/core/logging.py` — one processor chain, wrapping `get_logger()`, and `SCHEDULER_DATABASE_URL` in the URL-secret constant list from day one
- [X] T018 [P] Create `services/scheduler/src/scheduler/core/correlation.py` binding the incoming `x-turn-id` via `structlog.contextvars` (research.md #18)
- [X] T019 Create `services/scheduler/src/scheduler/db/session.py` (async engine + `session_factory`), mirroring `services/chat/src/chat/db/session.py`
- [X] T020 Create `services/scheduler/alembic.ini`, `services/scheduler/alembic/env.py`, and `services/scheduler/alembic/script.py.mako`, mirroring chat's and reading `SCHEDULER_DATABASE_URL`
- [X] T021 Create `services/scheduler/tests/conftest.py`: override the database env var to `visitdoc_scheduler_test` before any `scheduler.*` import, session-scoped autouse `alembic upgrade head`, and the `engine.dispose()`-per-test fixture (`docs/testing-strategy.md` "Async engine and event loops" — both halves are required)

### Scheduler schema (TDD)

- [X] T022 [P] Write failing schema tests in `services/scheduler/tests/test_migrations.py`: `btree_gist` installed, `timerange` type exists, four tables present, both appointment EXCLUDE constraints reject an overlap, back-to-back `[start, end)` intervals are ACCEPTED (FR-061), `working_ranges` overlap rejected, both FK cascades fire, `UNIQUE (session_id, full_name)` and `UNIQUE (chat_id)` and `UNIQUE (idempotency_key)` enforced
- [X] T023 Create `services/scheduler/src/scheduler/domain/models.py` — `Practitioner`, `WorkingRange`, `Patient`, `Appointment` per data-model.md, each entity table carrying `created_at`/`updated_at` (`server_default=func.now()`, `onupdate=func.now()`), naive `TIMESTAMP`/`TIME` for all domain times
- [X] T024 Create the initial migration `services/scheduler/alembic/versions/*_create_scheduling_schema.py`: `CREATE EXTENSION btree_gist`, `CREATE TYPE timerange AS RANGE (subtype = time)`, the four tables, both `EXCLUDE USING gist` constraints on `tsrange(starts_at, ends_at)`, the `working_ranges` exclusion constraint, both FK cascades, all UNIQUEs, and the `session_id` indexes
- [X] T025 Run `uv run --directory services/scheduler alembic upgrade head` against `visitdoc_scheduler`, then confirm T022's tests pass

### Scheduler process

- [X] T026 [P] Create `services/scheduler/src/scheduler/grpc/converters.py` — proto↔domain translation, ISO-8601 local date-time parse/format via `shared_models.localtime`, rejection of any tz-aware or offset-bearing value, and `Specialty` membership validation (research.md #5/#25)
- [X] T027 [P] Create `services/scheduler/src/scheduler/grpc/interceptors.py` — bind `x-turn-id` into the log context, emit `rpc.received`/`rpc.completed` per contracts/log-events.md
- [X] T028 Rewrite `services/scheduler/src/scheduler/main.py` — `create_app()` plus a `lifespan` that starts and gracefully stops the `grpc.aio` server alongside uvicorn (research.md #14), and a `GET /health` route

### Chat foundation

- [X] T029 [P] Create the chat migration `services/chat/alembic/versions/*_add_patient_id_to_chats.py` — `chats.patient_id` `VARCHAR(26)` NULL, indexed, no foreign key
- [X] T030 [P] Add `SCHEDULING_GRPC_TARGET`, `SCHEDULING_TIMEOUT_SECONDS`, `SCHEDULING_MAX_ATTEMPTS` to `Settings` in `services/chat/src/chat/core/config.py`
- [X] T031 [P] Write failing schema tests in `services/chat/tests/test_validation.py` for `ChatRequest.local_now` rejecting tz-aware values and requiring `chat_id`
- [X] T032 Update `services/chat/src/chat/domain/schemas.py` — `ChatRequest` gains `chat_id`/`local_now` (naive-only validator), `ChatDoneEvent` gains `answer_source` and `grounded: bool | None`, and `ChatSummary`/`ChatListResponse`/`AnswerSource` are added (data-model.md)
- [X] T033 [P] Add `patient_id` to the `Chat` model in `services/chat/src/chat/domain/models.py`
- [X] T034 Write failing tests in `services/chat/tests/test_chat_repository.py` for `list_chats_for_session` (FR-056 ordering: chats with messages first by newest message, then chats without by newest created), `create_chat`, session-scoped `get_chat`, and `set_patient_id`
- [X] T035 Rework `services/chat/src/chat/repositories/chat_repository.py` — remove `get_or_create_chat_for_session` and `get_chat_for_session`, add `list_chats_for_session`/`create_chat`/`get_chat`/`set_patient_id`
- [X] T036 Write failing tests in `services/chat/tests/test_scheduling_client.py` for the 2s deadline, the single retry on `UNAVAILABLE`/`DEADLINE_EXCEEDED` only, no retry on other statuses, `x-turn-id` metadata, and `BookingFailure` → domain-result mapping
- [X] T037 Create `services/chat/src/chat/clients/scheduling.py` — the only module importing `shared_proto`; owns the channel, deadline, retry, metadata, and failure taxonomy, raising `SchedulingUnavailableError` for transport failure only (research.md #9)
- [X] T038 [P] Write failing tests in `services/chat/tests/test_node_logging.py` for `node_span()` emitting `node.started`/`node.completed`/`node.failed`/`node.cancelled` and binding `node` into the context for events raised inside it
- [X] T039 [P] Create `services/chat/src/chat/agent/node_logging.py` implementing `node_span()` per research.md #24
- [X] T040 Write failing tests in `services/chat/tests/test_chats_api.py` for `GET /chats` (empty for an unknown cookie, FR-056 order), `POST /chats` (creates chat + session, mints the cookie on first visit), and `GET /chats/{chat_id}/messages` (404 for another session's chat)
- [X] T041 Create `services/chat/src/chat/api/chats.py` with `GET /chats`, `POST /chats` (chat row only — provisioning arrives in US2), and `GET /chats/{chat_id}/messages`, all scoped to the cookie's session
- [X] T042 Update `services/chat/src/chat/api/chat.py` — `POST /chat` takes `chat_id` and `local_now` and resolves that chat instead of "the session's chat"; **remove** `GET /chat` and `DELETE /chat` (research.md #19)
- [X] T043 Update `services/chat/src/chat/main.py` — build the gRPC channel in `lifespan` on the existing `AsyncExitStack`, store it on `app.state`, and include the `chats` router
- [X] T044 Update `services/chat/tests/test_chat_api.py` for the new request shape and the removed endpoints
- [X] T045 Adapt `services/frontend/src/lib/chatStream.ts` — `/chats` calls, `askChat(chatId, message, localNow)`, `ChatDoneEvent` carrying `answer_source` and `grounded: boolean | null`, and render `message` when present else the accumulated tokens; update `services/frontend/src/components/ChatWindow.tsx` to load `chats[0]` so the app still runs before US4's chat list exists

**Checkpoint**: Both services start, the schema is in place with its constraints proven, and the chat
UI still works against the new `/chats` resource. User story work can begin.

---

## Phase 3: User Story 1 - Book an appointment by chatting (Priority: P1) 🎯 MVP

**Goal**: A patient describes what they want in plain language and ends the conversation with a real
appointment in the scheduler's database.

**Independent Test**: With a session that already has a patient and at least one practitioner
(created directly through the repositories in a fixture), hold a conversation that ends in a booking,
then verify from outside the conversation that the appointment exists with the expected patient,
practitioner, and time.

### Tests for User Story 1 (write first, confirm failing) ⚠️

- [X] T046 [P] [US1] Write failing availability tests in `services/scheduler/tests/test_availability.py`: slot grid walks from each working range's own start, a trailing remainder shorter than one duration is not offered, slots overlapping the practitioner's appointments are excluded, slots overlapping the **requesting patient's** appointments with any practitioner are excluded (FR-024), a slot starting exactly when the patient's previous appointment ends IS offered (FR-061), past and beyond-horizon slots are excluded against the caller's `local_now` at their exact boundaries — a slot starting at exactly `local_now` is NOT offered (FR-020) and one starting exactly 90 days out IS (FR-021) — grandfathered appointments are excluded from offers while the slots they overlap are dropped like any other conflict (FR-023), two contiguous ranges each anchor their own grid and no slot crosses the junction (FR-006/FR-018/FR-019), a range shorter than one appointment duration yields no slots at all (FR-019), a window longer than 14 days or richer than 50 starts is clamped and marked `truncated` rather than refused (FR-067), and — the case that would catch a stray `datetime.now()` — a `local_now` on a **different calendar day from the host clock** filters past and horizon exactly as one agreeing with it does (FR-058, SC-006)
- [X] T047 [P] [US1] Write failing booking tests in `services/scheduler/tests/test_appointment_repository.py`: successful insert derives `ends_at` from the practitioner's duration, each of the eight refusal reasons is returned for its own cause, an attempt breaking several rules at once reports the first in FR-065's precedence (notably: a start inside no working range is `OUTSIDE_SCHEDULE`, never `OFF_GRID`), a patient or practitioner id from another session is reported as not-found with nothing distinguishing it from a nonexistent one (FR-066), two patients in one session both book the same practitioner — at different times both succeed, and neither can take a time the other already holds (FR-009, US4-8) — and a concurrent duplicate loses to the exclusion constraint with exactly one row surviving (SC-002)
- [X] T048 [P] [US1] Write failing idempotency tests in `services/scheduler/tests/test_idempotency.py` covering all four cases in research.md #8's table: an unused key inserts; a used key with a matching patient/practitioner/`starts_at` returns the original with `idempotent_replay = true` and creates nothing (FR-051); a used key with **any** of those three differing is refused with `INVALID_ARGUMENT`, returns neither the stored nor a new appointment, and logs `booking.key_mismatch` (FR-063); and a key presented after a *refused* attempt is evaluated afresh rather than replaying the refusal (FR-064)
- [X] T049 [P] [US1] Write failing servicer tests in `services/scheduler/tests/test_servicer_booking.py` over an in-process channel for `ListPractitioners`, `CheckAvailability`, and `BookAppointment`, asserting domain refusals arrive as typed `BookingFailure` responses rather than gRPC error statuses (research.md #9)
- [X] T050 [P] [US1] Write failing tool-handler tests in `services/chat/tests/test_scheduling_tools.py` for `list_practitioners`, `check_availability`, and `book_appointment` — ambient `session_id`/`patient_id`/`local_now` are never model-supplied, the idempotency key is derived from `(patient_id, practitioner_id, starts_at)` so identical bookings produce identical keys and differing ones never collide (FR-062), an exhausted retry budget yields `status: "unavailable"`, and an `INVALID_ARGUMENT` key mismatch is treated as non-retryable and reported with the same nothing-was-booked wording rather than as a conflict (FR-063)
- [X] T051 [P] [US1] Write failing booking-node tests in `services/chat/tests/test_handle_booking.py`: the tool loop chains list → availability → book within one turn, `BookingOutcome` is derived from observed tool results and never from reply text, no `book_appointment` call is made without a confirmation turn (FR-027), the 6-iteration bound ends the loop with `booking.loop_exhausted`, and context is bounded to the last 5 turns while within-turn `tool_result` blocks survive (research.md #23). Also the two disambiguation rules, which drive US1-8/US1-9 and are otherwise untested: a request matching **several** practitioners calls `list_practitioners` and issues **no** `check_availability` until a later turn supplies the patient's choice (FR-052), and a request matching **none** produces a reply naming the specialties this session actually holds rather than any of the ten it does not (FR-053). Plus two cheap guards on the same fixtures: the client's `local_now` reaches the system prompt verbatim (FR-032, SC-006's chat half) and no reply text ever matches a ULID, since every tool result carries ids the patient must never see (FR-026)
- [X] T052 [P] [US1] Write failing routing tests in `services/chat/tests/test_graph.py`: booking-only intents launch `handle_booking` alone, both intents fan out concurrently with `merge_required=True`, `call_staff`/`unknown`/`classification_failed` fall back to `answer_faq`, and each node emits its lifecycle pair with the right `node` and `result` (contracts/log-events.md)
- [X] T053 [P] [US1] Write failing merge tests in `services/chat/tests/test_compose_answer.py`: a single specialist makes `compose_answer` a no-op that emits nothing but still emits `turn.completed`; a merged turn carries the FAQ half's citations through structurally, reports an abstaining FAQ half as an abstention, and never composes a `refused`/`unavailable` booking into a success (FR-028)
- [X] T054 [P] [US1] Write a failing end-to-end booking test in `tests/integration/test_booking_roundtrip.py` — chat's gRPC client against a live servicer and a real `visitdoc_scheduler_test` database, covering the booking round trip and the idempotent replay

### Implementation for User Story 1

- [X] T055 [US1] Create `services/scheduler/src/scheduler/domain/availability.py` — the one slot-grid implementation used by both `CheckAvailability` and `BookAppointment`'s validator, half-open throughout, one grid per working range, filtering by practitioner AND requesting patient, with every boundary (`start > local_now`, `start <= local_now + 90d`) written once so the offer path and the write path cannot drift, plus the 14-day/50-start clamp and its `truncated` flag (research.md #21, FR-065/FR-067)
- [X] T056 [P] [US1] Create `services/scheduler/src/scheduler/repositories/practitioner_repository.py` — session-scoped reads, `AsyncSession` as an explicit parameter
- [X] T057 [US1] Create `services/scheduler/src/scheduler/repositories/appointment_repository.py` — idempotency-key lookup before insert that **compares the request's patient, practitioner, and `starts_at` against the stored row** and refuses a mismatch rather than replaying it (research.md #8's four-case table, FR-051/FR-063/FR-064), `ends_at` derived from the practitioner's duration, `IntegrityError` from either exclusion constraint mapped to `PRACTITIONER_BUSY`/`PATIENT_BUSY`, and the `booking.*` log events including `booking.key_mismatch`
- [X] T058 [US1] Create `services/scheduler/src/scheduler/grpc/servicer.py` implementing `ListPractitioners`, `CheckAvailability`, and `BookAppointment`, returning domain refusals as `BookingFailure` and a key mismatch as `INVALID_ARGUMENT` (FR-063, research.md #9)
- [X] T059 [US1] Create `services/scheduler/src/scheduler/grpc/server.py` wiring the servicer and T027's interceptor into the `grpc.aio` server started by T028's lifespan
- [X] T060 [P] [US1] Create `services/chat/src/chat/agent/tools/registry.py` — `(name, description, input_schema, handler)` records rendered into the Anthropic `tools=` parameter, with ambient arguments bound per turn (contracts/agent-tools.md)
- [X] T061 [US1] Create `services/chat/src/chat/agent/tools/scheduling_tools.py` with the `list_practitioners`, `check_availability`, and `book_appointment` handlers, including the fixed per-reason explanations and the `unavailable` result
- [X] T062 [US1] Create `services/chat/src/chat/agent/handle_booking.py` — the bounded tool-use loop producing `BookingResult`/`BookingOutcome`, streaming in single-specialist mode and collecting in merge mode, emitting `booking.tool_called`/`tool_result`/`tool_failed`
- [X] T063 [US1] Create `services/chat/src/chat/agent/compose_answer.py` — no-op for a single specialist, otherwise one Sonnet call composing both results; the only emitter of `turn.completed`, on every path (research.md #24)
- [X] T064 [US1] Rework `services/chat/src/chat/agent/graph.py` — `classify_intent` becomes the router writing `intents`/`merge_required`, conditional fan-out to the specialists, both edging into `compose_answer`, with disjoint result keys so no channel reducer is needed
- [X] T065 [US1] Update `services/chat/src/chat/agent/answer_faq.py` — collect mode returning `FaqResult`, the 5-turn context bound via `bound_to_last_n_turns`, a new `faq.retrieved` event, and `turn.completed` removed (it now lives in `compose_answer`)
- [X] T066 [US1] Update `services/chat/src/chat/api/chat.py` to pass `session_id`, `chat_id`, `patient_id`, and `local_now` into graph state and to persist `grounded`/`citations` per `answer_source`
- [X] T067 [US1] Update `services/frontend/src/components/MessageView.tsx` and `ChatWindow.tsx` to render a booking reply (streamed tokens, no citations) distinctly from a grounded FAQ reply
- [X] T068 [US1] Run `make test` and `make test-integration`; confirm T046–T054 now pass

**Checkpoint**: A patient can book an appointment by chatting, end to end, against real data.

---

## Phase 4: User Story 2 - A first visit produces a usable clinic (Priority: P2)

**Goal**: A first-time visitor gets a session, a chat, a named patient, and a bookable practitioner —
and still gets a working chat with grounded FAQ answers when the scheduler is down.

**Independent Test**: Open the site with no prior state and confirm a session, a chat, a named
patient, and both default practitioners all exist. Repeat with the scheduler stopped and confirm the chat is
still created and still answers an FAQ question.

### Tests for User Story 2 (write first, confirm failing) ⚠️

- [X] T069 [P] [US2] Write failing naming tests in `services/scheduler/tests/test_naming.py`: names are drawn in strict pool order, the same creation sequence in a fresh session yields the identical sequence (SC-007), the 101st patient is the first pool name plus `" 2"` (FR-013), a concurrent collision retries rather than failing, and the same name may exist in two sessions (FR-014)
- [X] T070 [P] [US2] Write failing provisioning tests in `services/scheduler/tests/test_servicer_provisioning.py`: `EnsureSessionProvisioned` is idempotent on `chat_id`, creates a practitioner only when the session has none, and returns `patient_created`/`practitioner_created` accurately on a second call
- [X] T071 [P] [US2] Write failing defaults tests in `services/scheduler/tests/test_practitioner_repository.py`: a practitioner created with nothing supplied gets the next pool name, General Practice, Mon–Fri 09:00–17:00, and 60 minutes, and is immediately bookable (FR-057)
- [X] T072 [P] [US2] Write failing degraded-mode tests in `services/chat/tests/test_chats_api.py` and `services/chat/tests/test_chat_api.py`: `POST /chats` succeeds with `patient_name: null` when the scheduler is unreachable, FAQ answering still works, the patient is created on a later turn without duplicating (FR-045), and a booking request in that state yields the "temporarily unavailable" reply within 5 seconds (SC-013)

### Implementation for User Story 2

- [X] T073 [P] [US2] Create `services/scheduler/src/scheduler/domain/name_pools.py` — 100 internationally recognized writers dead more than 50 years, and 20 comparable physicians, as ordered tuples
- [X] T074 [US2] Create `services/scheduler/src/scheduler/domain/naming.py` — the deterministic pool walk with `" 2"`/`" 3"` passes and `name.allocated`/`name.collision_retried` logging
- [X] T075 [P] [US2] Create `services/scheduler/src/scheduler/repositories/patient_repository.py` — create-if-absent on `chat_id`, bounded retry on a name collision
- [X] T076 [US2] Add default-applying creation (FR-057) to `services/scheduler/src/scheduler/repositories/practitioner_repository.py`
- [X] T077 [US2] Add `EnsureSessionProvisioned` to `services/scheduler/src/scheduler/grpc/servicer.py`, guarding practitioner creation on "the session has none"
- [X] T078 [US2] Add provisioning to `POST /chats` in `services/chat/src/chat/api/chats.py` — commit the chat row first, then call under the bounded budget, then write `patient_id` back; log `chat.created`/`patient.provisioned` (research.md #10)
- [X] T079 [US2] Add the lazy re-provisioning attempt for a chat whose `patient_id` is still NULL to `services/chat/src/chat/api/chat.py`, before the graph runs
- [X] T080 [US2] Make the booking tools return `status: "unavailable"` when the chat has no patient record yet, in `services/chat/src/chat/agent/tools/scheduling_tools.py`
- [X] T081 [US2] Run `make test`; confirm T069–T072 now pass

**Checkpoint**: A cold visit provisions a usable clinic, and a scheduler outage degrades to FAQ-only
without failing chat creation.

---

## Phase 5: User Story 3 - Ask who's available and what I've booked (Priority: P3)

**Goal**: The two read-only questions — which practitioners this session has, and what this patient
has booked — answered from real data, in local time.

**Independent Test**: In a session with two practitioners and one booked appointment, ask both
questions and check the answers match the stored data exactly, with nothing from another session or
another patient.

### Tests for User Story 3 (write first, confirm failing) ⚠️

- [X] T082 [P] [US3] Write failing tests in `services/scheduler/tests/test_servicer_appointments.py` for `ListUpcomingAppointments`: only starts strictly after the caller's `local_now`, earliest first, scoped to one patient, and an appointment that has already started is absent from the list while still blocking an overlapping booking (FR-031/FR-023)
- [X] T083 [P] [US3] Write failing tests in `services/chat/tests/test_scheduling_tools.py` for the `list_my_appointments` handler, including the empty-list case being an explicit "nothing upcoming" rather than an error
- [X] T084 [P] [US3] Write failing conversation tests in `services/chat/tests/test_handle_booking.py` for both read-only questions, asserting cross-session and cross-patient isolation (SC-004) against rows the test wrote itself

### Implementation for User Story 3

- [X] T085 [US3] Add `list_upcoming` to `services/scheduler/src/scheduler/repositories/appointment_repository.py`, filtered against the caller-supplied `local_now`
- [X] T086 [US3] Add `ListUpcomingAppointments` to `services/scheduler/src/scheduler/grpc/servicer.py`
- [X] T087 [US3] Add the `list_my_appointments` handler and its registry entry in `services/chat/src/chat/agent/tools/scheduling_tools.py` and `registry.py`
- [X] T088 [US3] Run `make test`; confirm T082–T084 now pass

**Checkpoint**: Both read-only questions are answered correctly and are session- and patient-isolated.

---

## Phase 6: User Story 4 - Run several patients from one browser (Priority: P4)

**Goal**: A list of the session's chats, each with its own patient, that the user can switch between,
add to, and delete completely.

**Independent Test**: From one browser, create three chats, confirm each has a distinct patient name,
send a message in each, switch between them and confirm each shows its own history, then delete one
and confirm its patient and appointments are gone while the others are untouched.

### Tests for User Story 4 (write first, confirm failing) ⚠️

- [X] T089 [P] [US4] Write failing tests in `services/scheduler/tests/test_servicer_provisioning.py` for `DeletePatientForChat`: cascades to the patient's appointments, is idempotent when the patient is already absent, and reports `appointments_deleted` accurately
- [X] T090 [P] [US4] Write failing deletion tests in `services/chat/tests/test_chats_api.py`: `DELETE /chats/{id}` removes chat, messages, patient, and appointments together while leaving the session's other chats intact (SC-011), returns 404 for another session's chat, and returns 503 without deleting anything when the scheduler is unreachable (research.md #11)
- [X] T091 [P] [US4] Write a failing mid-turn deletion test in `services/chat/tests/test_chats_api.py`: deleting a chat while its reply is streaming cancels the turn, records no assistant message, and leaves no appointment behind (FR-055)
- [X] T092 [P] [US4] Write a failing test in `services/chat/tests/test_chat_repository.py` that a session surviving with zero chats is a valid state (FR-040)
- [X] T093 [P] [US4] Write failing frontend tests in `services/frontend/tests/ChatList.test.tsx` and `ChatWindow.test.tsx`: chats listed by patient name, an unnamed chat shown as `"Unnamed · HH:MM"` from its creation time (FR-054), switching shows only that chat's history, the most recently active chat opens on load (FR-056), the chat area is muted with the create control still working when the session has no chats (FR-041), and every send carries a `local_now` taken from the browser's clock (FR-032) — without which the server rejects the turn on a required field
- [X] T094 [P] [US4] Write a failing integration test in `tests/integration/test_deletion_cascade.py` covering the cross-store cascade against a real scheduler database

### Implementation for User Story 4

- [X] T095 [US4] Add `delete_for_chat` to `services/scheduler/src/scheduler/repositories/patient_repository.py` and `DeletePatientForChat` to `services/scheduler/src/scheduler/grpc/servicer.py`
- [X] T096 [US4] Add cancel-by-chat to `services/chat/src/chat/agent/generation_registry.py` for FR-055's mid-turn deletion
- [X] T097 [US4] Add `DELETE /chats/{chat_id}` to `services/chat/src/chat/api/chats.py` — cancel the in-flight turn, call the scheduler, then delete the local rows, logging `chat.deleted` (research.md #11)
- [X] T098 [P] [US4] Create `services/frontend/src/components/ChatList.tsx` — list, switch, create, delete, with the FR-054 placeholder label
- [X] T099 [US4] Update `services/frontend/src/components/ChatWindow.tsx` to be driven by an active chat id and to mute itself when the session has no chats
- [X] T100 [US4] Delete `services/frontend/src/components/ClearChatButton.tsx` and `services/frontend/tests/ClearChatButton.test.tsx`, replaced by per-chat delete (FR-039)
- [X] T101 [US4] Update `services/frontend/src/App.tsx` to own the chat list and the active-chat selection
- [X] T102 [US4] Run `make test` and `make test-integration`; confirm T089–T094 now pass

**Checkpoint**: One browser drives several patients, and deleting a chat removes exactly its own data.

---

## Phase 7: User Story 5 - Manage practitioners and patient names directly (Priority: P5)

**Goal**: The scheduler's REST admin surface — create, edit, and delete practitioners, rename
patients — scoped to the caller's own session, with no UI.

**Independent Test**: Create, edit, and delete a practitioner and rename a patient through the
interface, then verify the effects — including cascading appointment deletion — without touching the
chat UI.

### Tests for User Story 5 (write first, confirm failing) ⚠️

- [X] T103 [P] [US5] Write failing tests in `services/scheduler/tests/test_specialties_api.py` for `GET /specialties`: exactly ten values, sorted by name, and no `X-Session-Id` required (FR-060)
- [X] T104 [P] [US5] Write failing tests in `services/scheduler/tests/test_practitioners_api.py`: a bare `POST {}` yields an immediately bookable practitioner (FR-057), a specialty outside the list is 422, a duplicate name in one session is 409 (FR-050), narrowing a schedule past an existing appointment succeeds and leaves that appointment untouched (FR-022), deleting a practitioner deletes their appointments and no others (FR-049), a missing `X-Session-Id` is 401, and another session's practitioner is 404 (US5-5)
- [X] T105 [P] [US5] Write failing tests in `services/scheduler/tests/test_patients_api.py` for rename, its 409 on a within-session collision, and the absence of any create or delete route

### Implementation for User Story 5

- [X] T106 [P] [US5] Create `services/scheduler/src/scheduler/api/dependencies.py` — `X-Session-Id` extraction, 401 when absent (research.md #16)
- [X] T107 [P] [US5] Create `services/scheduler/src/scheduler/domain/schemas.py` — the admin DTOs from contracts/scheduler-admin-api.yaml, with naive-datetime and `Specialty` validators
- [X] T108 [P] [US5] Create `services/scheduler/src/scheduler/api/specialties.py` implementing `GET /specialties`, sorted explicitly rather than relying on declaration order
- [X] T109 [US5] Create `services/scheduler/src/scheduler/api/practitioners.py` — list, create, patch, delete, all session-scoped
- [X] T110 [US5] Create `services/scheduler/src/scheduler/api/patients.py` — list and rename only
- [X] T111 [US5] Register the three routers in `services/scheduler/src/scheduler/main.py`
- [X] T112 [US5] Run `make test`; confirm T103–T105 now pass

**Checkpoint**: All five user stories are independently functional.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: The documentation this change is required to carry (Constitution VI), CI, and full
validation.

- [X] T113 [P] Amend `docs/ROADMAP.md` Phase 1c: replace the "MCP tool servers" bullet with the tool-registry seam and state that the MCP transport moves to a later phase (research.md #1, plan.md Complexity Tracking)
- [X] T114 [P] Amend `docs/ROADMAP.md` Phases 1c and 1d: move "parallel specialist nodes with a merge step" from 1d into 1c (research.md #2, plan.md Complexity Tracking)
- [X] T115 [P] Annotate the superseded Assumption in `specs/003-conversational-chat-history/spec.md` — "no fixed cap on context growth" no longer holds; generation is bounded to the last 5 turns (research.md #23)
- [X] T116 [P] Add README entries for the scheduling service, the exclusion-constraint approach, the timezone-free time model, the tool-registry seam, and the shared-Postgres-container tradeoff (Constitution VI)
- [X] T117 [P] Update `docs/testing-strategy.md` for the scheduler unit tier, the now-populated integration tier, and the renamed chat databases in its worked example
- [X] T118 [P] Update the `docker exec … psql` recipes in `.claude/CLAUDE.local.md`, which still name `visitdoc` and `visitdoc_test`
- [X] T119 Add an `integration` job to `.github/workflows/ci.yml` running `make test-integration` against both provisioned test databases
- [X] T120 Work through every scenario in [quickstart.md](./quickstart.md) end to end, including the degraded-mode and concurrency checks
- [X] T121 Run `make lint`, `make typecheck`, and `make test`; resolve everything before opening the PR

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies — start immediately. T005 must follow T001–T004.
- **Foundational (Phase 2)**: depends on Setup. **Blocks every user story.** Within it: shared
  packages (T009–T015) → scheduler skeleton (T016–T021) → schema (T022–T025) → process (T026–T028);
  the chat foundation (T029–T045) depends only on T009–T015 and can proceed alongside the scheduler's.
- **User Stories (Phases 3–7)**: all depend on Foundational. US1 is the MVP.
- **Polish (Phase 8)**: depends on the stories you intend to ship.

### User Story Dependencies

- **US1 (P1)**: Foundational only. Its test fixtures create patients and practitioners directly
  through the repositories, so it does not wait on US2's provisioning.
- **US2 (P2)**: Foundational only, but shares `practitioner_repository.py` with US1 (T076 extends
  T056) and `scheduling_tools.py` (T080 extends T061) — sequence US2 after US1 unless two people are
  splitting them.
- **US3 (P3)**: Foundational only; extends US1's `appointment_repository.py`, `servicer.py`, and tool
  registry, so it is cheapest after US1.
- **US4 (P4)**: Foundational only for the frontend half; its cascade tests are most meaningful once
  US1 can produce appointments and US2 can produce patients.
- **US5 (P5)**: Foundational only. The most independent story — its own API modules, touching US1's
  repositories read-only.

### Within Each User Story

- Tests MUST be written and observed to FAIL before implementation (Constitution VIII, non-negotiable)
- Models before repositories, repositories before servicer/API, servicer before tool handlers, tool
  handlers before graph nodes
- The story's final "run tests" task closes the TDD loop by observing them pass

### Parallel Opportunities

- Setup: T001, T002, T003, T007, T008 are all different files
- Foundational: T009–T012 (shared-models) run together; T016–T018 (scheduler core) run together;
  T026/T027 run together; the whole chat foundation (T029–T045) runs alongside the scheduler's once
  T009–T015 land
- Every user story's test block is fully parallel — they are different files by construction
- Across stories: once Phase 2 closes, US1 and US5 touch almost disjoint files and are the natural
  two-person split

---

## Parallel Example: User Story 1

```bash
# The eight US1 unit-test files are independent — write them together, confirm all failing.
# T054 is left out: it is the integration test, and needs a live servicer and a real database.
Task: "Availability tests in services/scheduler/tests/test_availability.py"
Task: "Booking tests in services/scheduler/tests/test_appointment_repository.py"
Task: "Idempotency tests in services/scheduler/tests/test_idempotency.py"
Task: "Servicer tests in services/scheduler/tests/test_servicer_booking.py"
Task: "Tool handler tests in services/chat/tests/test_scheduling_tools.py"
Task: "Booking node tests in services/chat/tests/test_handle_booking.py"
Task: "Graph routing tests in services/chat/tests/test_graph.py"
Task: "Merge tests in services/chat/tests/test_compose_answer.py"

# Then the two independent implementation entry points:
Task: "Slot grid in services/scheduler/src/scheduler/domain/availability.py"
Task: "Tool registry in services/chat/src/chat/agent/tools/registry.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1: Setup — databases renamed and provisioned, scheduler has its dependencies
2. Phase 2: Foundational — **the long pole**; the schema and its constraints are the load-bearing part
3. Phase 3: User Story 1 — booking end to end
4. **STOP and VALIDATE**: quickstart.md Scenario 1, including all four negative checks
5. Demo: a patient books an appointment by chatting

Note that the MVP needs data to book against. Until US2 lands, create a patient and a practitioner
from a fixture or a `psql` insert — which is exactly what US1's independent test does.

### Incremental Delivery

1. Setup + Foundational → both services run, schema proven
2. + US1 → booking works (MVP) → quickstart Scenario 1
3. + US2 → cold visits provision themselves, outages degrade gracefully → Scenario 2
4. + US3 → the two read-only questions → Scenario 3
5. + US4 → several patients from one browser → Scenario 4
6. + US5 → direct management → Scenario 5
7. + Polish → documentation amendments, CI, full quickstart

### Parallel Team Strategy

Phase 2 is where the critical path lives, so split it first: one person takes the scheduler
(T016–T028), another the chat foundation (T029–T045), after T009–T015 land together. Once Phase 2
closes, US1 and US5 are the cleanest two-person split — near-disjoint files, no shared graph code.

---

## Notes

- `[P]` = different files, no dependencies on incomplete tasks
- Verify tests fail before implementing; the per-story "run tests" task is where you observe them pass
- Commit after each task or logical group; use the repo's `feat: 005 - <summary>` convention
- Stop at any checkpoint to validate a story independently
- [checklists/booking-integrity.md](./checklists/booking-integrity.md) is fully resolved as of
  2026-08-13, as is checklists/requirements.md. The requirements it added along the way —
  FR-061/FR-062/FR-063/FR-064 (half-open intervals, idempotency-key scope and mismatch) and
  FR-065/FR-066/FR-067 (refusal-reason precedence, cross-session ids reported as not-found, the
  availability window cap) — are covered by T046, T047, T048, T055, T057, and T104. Nothing in it is
  outstanding; re-run `/speckit-checklist` if a later spec revision reopens the area

---

## Phase 9: Convergence

- [X] T122 Create the session, chat, patient, and practitioner on a visitor's genuine first arrival — `services/frontend/src/App.tsx` currently renders the muted zero-chat state instead, since `GET /chats` returns an empty list and nothing calls `POST /chats`. Must distinguish a first arrival from a session whose last chat was deleted, which MUST NOT be re-provisioned (FR-040); note the session cookie is `HttpOnly` and so unreadable from the SPA. Cover with a frontend test for both cases, per FR-042 (missing)
- [X] T123 Add a pool-exhaustion test that creates 100 patients in one session through `patient_repository`/`EnsureSessionProvisioned` and asserts the 101st receives the first pool name plus `" 2"`, and that repeating the sequence in a fresh session yields the identical names — `services/scheduler/tests/test_naming.py` proves this against the pure `allocate_name()` only, so the wiring from `taken_names` into the walk is unproven, per SC-007 (partial)
- [X] T124 Add conversation-level tests in `services/chat/tests/test_handle_booking.py` for both read-only questions ("who is available", "what have I booked"), asserting cross-session and cross-patient isolation against rows the test wrote itself — isolation is currently covered only at the tool and scheduler layers, per T084/SC-004 (partial)
- [X] T125 Reconcile `specs/005-scheduling-and-booking/data-model.md`'s `chats` table with the implemented `patient_name` column (migration `c93b1e07a248`), including why the chat service caches a name it never authors and that an admin rename leaves the copy stale until the next provisioning call, per plan: data model / Constitution VI (contradicts)
- [X] T126 Assert in `services/chat/tests/test_handle_booking.py` that the loop's own non-model-authored replies (the loop-exhausted reply and the `unavailable` explanations) contain no identifier, and record in the test why the model-authored reply text is not assertable under the `AsyncAnthropic` mocking rule, per T051/FR-026 (partial)
