# Tasks: Structured Logging for App/AI Behavior

**Input**: Design documents from `/specs/002-structured-logging/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/log-events.md, quickstart.md (all present)

**Tests**: Mandatory per Constitution Principle VIII (Test-Driven Development, NON-NEGOTIABLE).
Every phase's tests MUST be written and observed to fail before its implementation tasks begin.

**Organization**: Tasks are grouped by user story (spec.md) to enable independent implementation
and testing of each story. All work is scoped to the existing `services/chat` uv workspace member
only — no other member changes.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on an incomplete task in the same batch)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- Every task includes its exact file path(s)

---

## Phase 1: Setup

**Purpose**: Add the two new dependencies this feature needs before any code can import them.

- [X] T001 Add `structlog` and `python-ulid` as dependencies of `services/chat` (`uv add --package chat structlog python-ulid`), committing the resulting `services/chat/pyproject.toml` and root `uv.lock` changes (research.md #1, #2).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The one shared processor chain, correlation-ID binding mechanism, and centralized
terminal renderer that every log call site in every later phase flows through (FR-006, FR-009,
FR-013, FR-014, FR-017, FR-021). No user story's log calls can be implemented before this exists.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Tests for Foundational (write first, confirm failing) ⚠️

- [X] T002 [P] Write `services/chat/tests/test_logging.py`: the truncation processor clips any string field over 2,000 characters to 2,000 chars + `"..."` and leaves shorter strings untouched (FR-013); the redaction processor replaces exact occurrences of live secret values — `Settings.ANTHROPIC_API_KEY`, `Settings.VOYAGE_API_KEY`, and the credential portion of `Settings.DATABASE_URL`/`Settings.QDRANT_URL` — wherever they appear in any string value (FR-017 known-value matching); the redaction processor replaces the value of any key case-insensitively containing `password`/`token`/`secret`/`api_key`/`apikey`/`credential`/`authorization` regardless of the value itself (FR-017 key-name matching); every emitted event carries `timestamp`, `level`, `event` fields (FR-009); a processor raising an exception during a log call does not propagate past the call site (FR-008).
- [X] T003 [P] Write `services/chat/tests/test_correlation.py`: binding a `turn_id` for one simulated request and an `operation_id` for another are each present only on that request's log entries and cleared afterward; `turn_id` and `operation_id` are never both bound at once on the same entry (data-model.md); two concurrent `asyncio` tasks each binding their own ID never see the other's bound value (FR-006, FR-021).

### Implementation for Foundational

- [X] T004 [P] Implement `services/chat/src/chat/core/logging.py`: a `structlog` processor chain (merge contextvars → add timestamp → add log level → truncate-over-2000-chars processor → redact-secrets processor sourcing its known-value list from `Settings` → level-to-tier `ConsoleRenderer`, `critical` styled more prominently than `error`, `info` left unstyled) behind one `configure_logging(settings: Settings) -> None` entrypoint, plus a `get_logger(**initial_values)` helper whose returned logger swallows any exception a processor raises rather than letting it propagate to the caller (FR-008, FR-009, FR-011, FR-013, FR-014, FR-017, FR-019; research.md #1, #3, #4).
- [X] T005 [P] Implement `services/chat/src/chat/core/correlation.py`: FastAPI middleware/dependency helpers that generate a ULID (`python-ulid`) and bind it via `structlog.contextvars.bind_contextvars` as `turn_id` (for chat requests) or `operation_id` (for FAQ requests), clearing bound context via `clear_contextvars` after each request completes (FR-006, FR-021; research.md #2, #6).
- [X] T006 Wire `configure_logging()` (T004) into `services/chat/src/chat/main.py`'s `create_app()`/module setup, and register the correlation binding (T005) so it's available to the `/chat` and `/faq` routers (depends on T004, T005).
- [X] T007 Run `services/chat/tests/test_logging.py` and `services/chat/tests/test_correlation.py`, confirm both now pass (depends on T004, T005, T006).

**Checkpoint**: Foundation ready — correlation, truncation, redaction, and terminal rendering are available to every log call site added in the phases below.

---

## Phase 3: User Story 1 - Reconstruct why the assistant answered the way it did (Priority: P1) 🎯 MVP

**Goal**: Every chat turn emits its full per-turn decision trace — message received, message
embedded, retrieval outcome, groundedness verdict, final answer/abstention, and any pipeline error
— all sharing one `turn_id` (FR-001–FR-006, FR-020).

**Independent Test**: Ask the chat endpoint a question, then inspect the emitted logs and confirm
every step of that turn is present and attributable to that one turn.

### Tests for User Story 1 (write first, confirm failing) ⚠️

- [X] T008 [US1] Extend `services/chat/tests/test_chat_api.py`, using `structlog.testing.capture_logs`, to assert: (a) a grounded turn emits `turn.message_received` (visitor's message text present verbatim, unredacted per FR-010), `turn.message_embedded`, `turn.retrieval_completed` (`retrieved_chunks` ordered by `score` descending, each with `entry_id`/`chunk_index`/`score`/`chunk_text`), `turn.groundedness_verdict` (`grounded=True`), and `turn.completed` (`outcome="grounded"`, `answer_text`, `citations` with their scores) — all sharing one `turn_id`; (b) an abstained turn emits the same event shape with `grounded=False` and `turn.completed(outcome="abstained", abstention_message=...)`; (c) simulating an unhandled error (e.g. patch the Anthropic call to raise) emits `turn.error` with the correct `pipeline_step` and `error_detail`, sharing that turn's `turn_id` (FR-001–FR-006, FR-020).

### Implementation for User Story 1

- [X] T009 [P] [US1] In `services/chat/src/chat/rag/retriever.py`'s `search_faq`: emit `turn.message_embedded` right after `embed_texts()` returns, and `turn.retrieval_completed` (ordered `retrieved_chunks`) right after `search()` returns (FR-002, FR-020).
- [X] T010 [P] [US1] In `services/chat/src/chat/agent/answer_faq.py`'s `answer_faq`: emit `turn.message_received` at the start; emit `turn.groundedness_verdict` right after `is_grounded()`; emit `turn.completed` exactly once, either `outcome="grounded"` (with `answer_text`, `citations`+scores) or `outcome="abstained"` (with `abstention_message`) (FR-001, FR-003, FR-004).
- [X] T011 [US1] In `services/chat/src/chat/rag/retriever.py` and `services/chat/src/chat/agent/answer_faq.py`: wrap each of the four pipeline steps (embedding, retrieval, groundedness check, generation) in try/except that logs `turn.error(pipeline_step=..., error_detail=...)` before re-raising, so a failure at any step is attributed correctly (FR-005; depends on T009, T010).
- [X] T012 [US1] In `services/chat/src/chat/api/chat.py`: bind `turn_id` (via `core/correlation.py`, T005) for the `/chat` route, and add a catch-all try/except around consuming the `answer_faq` stream that logs `turn.error` for any error not already tagged by T011, so no turn-scoped error escapes unlogged (FR-005, FR-006).
- [X] T013 [US1] Run `services/chat/tests/test_chat_api.py`, confirm the new assertions from T008 pass (depends on T009–T012).

**Checkpoint**: User Story 1 is independently functional — a developer can reconstruct any turn's full decision trace from the logs alone.

---

## Phase 4: User Story 2 - Read a chat turn's trace straight from the terminal (Priority: P1)

**Goal**: The terminal rendering of a turn's trace is human-readable, with routine outcomes
(grounded or abstained) unflagged, turn-scoped errors distinguishable, and critical events the
most visually prominent tier (FR-011, FR-012, FR-014, FR-019).

**Independent Test**: Run the chat service locally, ask it a question, and confirm the terminal
output for that turn can be read and understood directly, without piping it through any parser.

### Tests for User Story 2 (write first, confirm failing) ⚠️

- [X] T014 [US2] Extend `services/chat/tests/test_logging.py`: render an `info`-level `turn.completed(outcome="abstained")` event and assert it carries no error/problem styling; render an `error`-level `turn.error` event and assert it's visually distinguishable from the `info` rendering; render a `critical`-level `critical.dependency_unreachable` event and assert it's visibly more prominent than the `error` rendering of comparable content (FR-012, FR-019).

### Implementation for User Story 2

- [X] T015 [US2] Tune the `ConsoleRenderer` tier styling in `services/chat/src/chat/core/logging.py` until T014 passes (depends on T004, T014).
- [X] T016 [US2] Manually run quickstart.md Scenarios 1–2 against a locally running chat service (`make run-chat`) and confirm the terminal output reads as labeled prose per step, not a raw machine serialization (FR-011, SC-006; depends on T015).

**Checkpoint**: User Story 2 is independently functional — the terminal alone is a usable debugging view, with no external tool needed.

---

## Phase 5: User Story 3 - Tell concurrent visitors' turns apart (Priority: P2)

**Goal**: Under concurrent chat traffic, every entry can still be correctly attributed to the one
turn that produced it (FR-006, SC-002).

**Independent Test**: Send two chat requests concurrently and confirm each request's log entries
can be filtered down to just that request's entries, regardless of interleaving.

### Tests for User Story 3 (write first, confirm failing) ⚠️

- [X] T017 [US3] Write a concurrency test in `services/chat/tests/test_chat_api.py` that fires two `/chat` requests concurrently (e.g. via `asyncio.gather` against concurrent client calls) and asserts each request's captured log entries share exactly one `turn_id`, distinct from the other request's `turn_id`, with no entry misattributed between the two (FR-006, SC-002).

### Implementation for User Story 3

- [X] T018 [US3] Run T017 against the existing `contextvars`-based binding (T005, T012); if isolation fails, fix `services/chat/src/chat/core/correlation.py` accordingly (depends on T017).

**Checkpoint**: User Story 3 is independently functional — concurrent turns never bleed into each other's trace.

---

## Phase 6: User Story 4 - Catch errors and critical events outside a single chat turn (Priority: P2)

**Goal**: FAQ content management operations (including their chunking/embedding sub-steps) are
logged with their own correlating `operation_id`, failures are logged with enough detail to
identify what and why, and critical events (dependency unreachable, at startup or mid-turn/mid-
operation) are always captured, correlated per FR-018 rather than lost or merged (FR-007, FR-015,
FR-016, FR-017, FR-018, FR-021, FR-022).

**Independent Test**: Trigger a failure that isn't part of a chat turn — e.g., make a FAQ content
operation fail, or simulate the service being unable to reach a required dependency — and confirm
it appears in the logs with enough detail to identify what failed and why.

### Tests for User Story 4 (write first, confirm failing) ⚠️

- [X] T019 [US4] Extend `services/chat/tests/test_faq_api.py`: assert create/update/delete each emit `faq.entry_created`/`faq.entry_updated`/`faq.entry_deleted` (`entry_id`) sharing one `operation_id` (FR-007, FR-021).
- [X] T020 [P] [US4] Extend `services/chat/tests/test_indexing.py`: assert a create/update operation emits `faq.content_chunked` (`chunk_count`) then `faq.chunks_embedded` (`chunk_count`) before the entry event, both sharing that operation's `operation_id`, while a delete operation (`deindex_faq_entry`) emits neither (FR-022, SC-013).
- [X] T021 [US4] Extend `services/chat/tests/test_faq_api.py`: simulate a failure during a create operation (e.g. patch `qdrant_repository.upsert_chunks` to raise) and assert `faq.operation_failed` (`operation`, `entry_id`, `failed_step`, `error_detail`) is emitted with no secret values present, sharing that operation's `operation_id`; and that when the failure stems from a dependency being unreachable, a `critical.dependency_unreachable` entry is ALSO emitted, correlated via that same `operation_id` — two entries, not one merged entry (FR-007, FR-015, FR-016, FR-017, FR-018).
- [X] T022 [P] [US4] Write a test in `services/chat/tests/test_main.py` asserting a failed `ensure_collection()` check during the `lifespan` startup path emits `critical.dependency_unreachable` (`dependency="qdrant"`) with no `turn_id`/`operation_id` present (FR-015).

### Implementation for User Story 4

- [X] T023 [P] [US4] In `services/chat/src/chat/api/faq.py`: bind `operation_id` (via `core/correlation.py`, T005) for the `/faq` routes (FR-021).
- [X] T024 [P] [US4] In `services/chat/src/chat/rag/indexing.py`'s `index_faq_entry`: emit `faq.content_chunked` (`chunk_count`) right after `chunk_content()` returns, and `faq.chunks_embedded` (`chunk_count`) right after `embed_texts()` returns — `deindex_faq_entry` (delete path) is unaffected (FR-022).
- [X] T025 [US4] In `services/chat/src/chat/api/faq.py`: wrap each CRUD handler body in try/except — on success, log `faq.entry_created`/`faq.entry_updated`/`faq.entry_deleted`; on failure, log `faq.operation_failed` (`operation`, `entry_id` if known, `failed_step`, `error_detail`), then re-raise/return the existing error response unchanged (FR-007, FR-016; depends on T023).
- [X] T026 [US4] Extend the failure handling from T025, plus `services/chat/src/chat/repositories/qdrant_repository.py` and `services/chat/src/chat/repositories/faq_repository.py`, so that when the caught failure is specifically a dependency being unreachable (Qdrant/Postgres), `critical.dependency_unreachable` (`dependency`, `error_detail`) is ALSO logged — in addition to, not instead of, `faq.operation_failed` — correlated via `operation_id` (FR-015, FR-018; depends on T025).
- [X] T027 [P] [US4] Extend the `turn.error` try/except blocks added for US1 (T011, in `services/chat/src/chat/rag/retriever.py` and `services/chat/src/chat/agent/answer_faq.py`) so that when the caught failure is specifically a dependency being unreachable (Qdrant/Anthropic), `critical.dependency_unreachable` (`dependency`, `error_detail`) is ALSO logged — in addition to, not instead of, `turn.error` — correlated via `turn_id` (FR-015, FR-018; depends on T011).
- [X] T028 [P] [US4] In `services/chat/src/chat/main.py`'s `lifespan`: wrap the existing `ensure_collection()` startup check in try/except that logs `critical.dependency_unreachable` (`dependency="qdrant"`) with no `turn_id`/`operation_id`, before re-raising (FR-015).
- [X] T029 [US4] Run `services/chat/tests/test_faq_api.py`, `services/chat/tests/test_indexing.py`, and `services/chat/tests/test_main.py`, confirm the new assertions from T019–T022 pass (depends on T023–T028).

**Checkpoint**: All four user stories are independently functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T030 [P] Run `make lint` and `make typecheck` (ruff + strict mypy) and fix any violations introduced across the new/modified logging code.
- [X] T031 [P] Add a "Structured logging" entry to `README.md` documenting the `structlog`-vs-stdlib-`logging`-vs-`loguru` tradeoff and the ULID-vs-UUID4 tradeoff, matching how `specs/001-grounded-faq-chat`'s choices are documented (Constitution Principle VI; research.md #1, #2).
- [X] T032 [P] Manually run quickstart.md Scenarios 3–8 (concurrent turns, mid-turn dependency failure, FAQ operation failure, chunking/embedding sub-steps, long-field truncation, secret redaction) against a locally running chat service and confirm each "Expected" block matches.
- [X] T033 Run `make test-unit` for the full suite and confirm every test — existing and new — passes together (depends on T030–T032).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational only.
- **User Story 2 (Phase 4)**: Depends on Foundational; its renderer tuning (T015) builds on `core/logging.py` (T004) and benefits from US1's events existing to render, but has no hard code dependency on US1's tasks.
- **User Story 3 (Phase 5)**: Depends on Foundational and on US1's `/chat` wiring (T012) existing, since it exercises real chat requests.
- **User Story 4 (Phase 6)**: Depends on Foundational; T027 explicitly depends on US1's T011 (extends the same try/except blocks).
- **Polish (Phase 7)**: Depends on all four user stories being complete.

### Within Each Phase

- Tests are written and observed to FAIL before implementation (TDD, non-negotiable per Constitution VIII).
- Implementation tasks touching the same file are sequenced, not parallelized.
- Each user story's checkpoint marks it independently testable per its spec.md Independent Test.

### Parallel Opportunities

- T002 and T003 (Foundational tests, different files).
- T004 and T005 (Foundational implementation, different files).
- T009 and T010 (US1, different files).
- T020 (US4 test, `test_indexing.py`) and T022 (US4 test, `test_main.py`) alongside T019/T021 (`test_faq_api.py`).
- T023 and T024 (US4 implementation, different files); T027 and T028 alongside T025/T026 (all touch disjoint files).
- T030, T031, T032 (Polish, independent concerns).

---

## Parallel Example: Foundational

```bash
Task: "Write services/chat/tests/test_logging.py — truncation, redaction, event shape, FR-008 exception safety"
Task: "Write services/chat/tests/test_correlation.py — turn_id/operation_id binding, isolation, mutual exclusivity"

# once both fail as expected:
Task: "Implement services/chat/src/chat/core/logging.py — processor chain + ConsoleRenderer"
Task: "Implement services/chat/src/chat/core/correlation.py — turn_id/operation_id middleware"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup.
2. Complete Phase 2: Foundational (blocks everything else).
3. Complete Phase 3: User Story 1.
4. **STOP and VALIDATE**: run quickstart.md Scenarios 1–2 manually against `make run-chat`.
5. This alone delivers SC-001 (full per-turn trace reconstructable from logs).

### Incremental Delivery

1. Setup + Foundational → shared infrastructure ready.
2. Add User Story 1 → full per-turn trace captured (MVP).
3. Add User Story 2 → that trace is readable directly in the terminal, no tooling needed.
4. Add User Story 3 → concurrent turns stay distinguishable under real traffic.
5. Add User Story 4 → FAQ operations and critical events outside a turn are no longer invisible.
6. Polish → lint/typecheck clean, README documents the tradeoffs, full quickstart validated, full suite green.

---

## Notes

- [P] tasks touch different files with no unmet dependency within their batch.
- [Story] labels map every user-story-phase task to spec.md's US1–US4 for traceability.
- No task introduces a new service, a new workspace member, or touches `services/scheduler`/`services/frontend` — scope stays exactly what plan.md's Structure Decision calls for.
- Two new modules carry all shared logic: `services/chat/src/chat/core/logging.py` and `services/chat/src/chat/core/correlation.py` — every other touched file only adds log calls at its own existing pipeline steps, per FR-014's "one centralized place" requirement.

---

## Phase 8: Convergence

Findings from a `/speckit-converge` pass run after full implementation of Phases 1–7. All 33
prior tasks (T001–T033) are complete and verified (76/76 tests passing, `ruff`/`mypy` clean); the
items below close gaps between the current code and what spec.md/plan.md/the constitution call
for, found by re-assessing the implemented feature against those artifacts.

- [ ] T034 Update plan.md's Project Structure entries for `services/chat/src/chat/rag/retriever.py`, `services/chat/src/chat/repositories/faq_repository.py`, and `services/chat/src/chat/repositories/qdrant_repository.py` to describe the actual design — critical-event logging for dependency failures is centralized at the API layer (`api/chat.py`'s `_CRITICAL_DEPENDENCY_BY_STEP` mapping, `api/faq.py`'s `_log_faq_failure`/`_log_dependency_unreachable`), not inside those three files, which were never touched — per plan: Project Structure (contradicts; Constitution VI).
- [ ] T035 Add tests to `services/chat/tests/test_chat_api.py`: a retrieval-step failure (e.g. patch `chat.repositories.qdrant_repository.search` to raise) logs `turn.error(pipeline_step="retrieval")`; both a retrieval-step and a generation-step dependency failure additionally log a correlated `critical.dependency_unreachable` sharing that turn's `turn_id`, extending the existing `test_generation_failure_logs_turn_error_with_step` coverage — per FR-005, FR-015, FR-018, SC-009 (partial).
- [ ] T036 Add tests to `services/chat/tests/test_faq_api.py` for the update and delete failure paths in `services/chat/src/chat/api/faq.py` (analogous to the existing `test_create_failure_logs_operation_failed_and_critical_event`): an update failure and a delete failure (both the Qdrant-deindex and Postgres-delete sub-paths) each log `faq.operation_failed` plus a correlated `critical.dependency_unreachable` sharing that operation's `operation_id` — per FR-007, FR-015, FR-018 (partial).
- [ ] T037 Add a concurrency test to `services/chat/tests/test_correlation.py` for `bind_operation_id()`, analogous to the existing `test_concurrent_turn_ids_never_leak_between_tasks`, confirming concurrent `asyncio` tasks each binding their own `operation_id` never see the other's bound value — per FR-021, SC-002 (partial).
- [ ] T038 Address SC-004 ("no perceptible slowdown in the streamed chat response as a result of logging"), currently unaddressed by any task: either add a lightweight timing spot-check to `specs/002-structured-logging/quickstart.md`'s manual validation, or add an explicit note (in quickstart.md or this file) documenting why it's satisfied by construction (synchronous processors, no blocking I/O, per plan.md's Performance Goals) — per SC-004 (missing).
