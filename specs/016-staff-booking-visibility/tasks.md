---

description: "Task list for 016 — What staff can see of the schedule"
---

# Tasks: What staff can see of the schedule (Phase 3a leftovers)

**Input**: Design documents from `specs/016-staff-booking-visibility/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: The constitution's Test-Driven Development principle makes test tasks mandatory. Each one
precedes the implementation it covers, and is **run and observed failing** before that
implementation starts. Every test task below says what it must fail on.

**Organization**: Tasks are grouped by user story. US1 (booking acts, P1) and US2 (practitioner
week, P2) share no code, so either can be built, tested and shipped without the other.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 or US2
- Every task names its file(s)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish a green baseline, so any later failure belongs to this feature.

- [X] T001 Run `make migrate`, `make lint`, `make typecheck`, `make test-unit` and `make test-frontend` from the repo root and record that all pass on the starting commit (`Makefile`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: None. The two stories touch disjoint modules. US1 changes the chat tool registry, the
`booking_acts` storage and the evidence marker. US2 changes the scheduler REST read, the console
proxy and the practitioner roster. Nothing needs to exist before either can start.

**Checkpoint**: Both story phases may begin immediately after T001.

---

## Phase 3: User Story 1 - A staff member reads what the assistant did to the schedule (Priority: P1) 🎯 MVP

**Goal**: Record every attempt to book, reschedule or cancel on the patient message the turn
answered, write-ahead, and show it in the staff console's evidence marker.

**Independent Test**: Drive a booking turn and a turn whose write cannot be sent. Open each in the
console, and check that the patient message's marker states the act and its outcome (spec US1, and
quickstart §4).

### Storage (contract: data-model.md § `booking_acts`)

- [X] T002 [P] [US1] Write failing migration tests for the `booking_acts` table in `services/chat/tests/test_migrations.py`. Cover:
  - the columns, the FKs to `chats`/`messages` with `ON DELETE CASCADE`, and the two indexes;
  - each of the five check constraints, each rejecting a violating row;
  - downgrade dropping the table.

  They fail because the table does not exist.
- [X] T003 [US1] Add the `BookingAct` model to `services/chat/src/chat/domain/models.py`, and the Alembic revision `services/chat/alembic/versions/<rev>_record_booking_acts.py` revising `b7e2a9c41d05`, exactly per data-model.md. Run T002 and see it pass.
- [X] T004 [P] [US1] Write failing repository tests in `services/chat/tests/test_booking_act_repository.py` for:
  - `begin` inserting an unsettled row scoped to `(session_id, chat_id, message_id)`, and failing when the message is not in that session;
  - `insert_not_sent` writing a settled `not_sent` row;
  - `settle` updating only `WHERE id AND session_id AND outcome IS NULL`: a second settle is a no-op, and a session-B id changes nothing;
  - `list_for_chat` returning rows in `seq` order, scoped by session;
  - deleting the chat removing its acts.

  They fail on the missing module.
- [X] T005 [US1] Implement `services/chat/src/chat/repositories/booking_act_repository.py`: `begin`, `insert_not_sent`, `settle` and `list_for_chat`. Each takes the `AsyncSession` as a parameter and each carries the session predicate. Run T004 and see it pass.

### Recording (contract: contracts/booking-acts.md § Recording rule)

- [X] T006 [P] [US1] Write failing unit tests in `services/chat/tests/test_booking_acts.py` for:
  - `settlement_from`, covering every row of research R6's status→outcome table, including names and times overwritten from `booked` and `changed` results;
  - `PlannedAct` construction for book, reschedule (with and without `new_practitioner_id`) and cancel, per data-model.md's argument table;
  - invariant 7: `BookingActRecorder` exposes only `learn_practitioners`, `name_of`, `begin`, `record_not_sent` and `settle`;
  - `DiscardingBookingActRecorder` accepting every call;
  - FR-021a: no module under `services/chat/src/chat/agent/` other than `booking_acts.py`, `tools/registry.py`, `tools/scheduling_tools.py` and `handle_booking.py` imports `chat.agent.booking_acts`, and nothing in `agent/history.py`, `compose_answer.py`, `classify_intent.py` or the booking prompt text references it. This is checked by walking the modules' imports, in the style of `test_tracing_boundary.py`.
- [X] T007 [US1] Implement `services/chat/src/chat/agent/booking_acts.py`, containing `PlannedAct`, `Settlement`, `ActHandle`, the `BookingActRecorder` protocol, `settlement_from`, `DiscardingBookingActRecorder`, and `DatabaseBookingActRecorder`. The database recorder is bound to `(session_id, chat_id, message_id)` and uses its own short committed `session_factory()` transaction per call. It never takes the chat's advisory lock, and it logs `booking_act.record_failed` or `booking_act.settle_failed` with the fields in contracts/booking-acts.md. Run T006 and see it pass.
- [X] T008 [P] [US1] Write failing registry tests in `services/chat/tests/test_registry_booking_acts.py`, one per row of contracts/booking-acts.md's recording table, plus plan invariants 1, 2 and 6. Use a spy recorder and a stub handler that asserts the unsettled row already exists when it runs. Cases:
  - invalid arguments: no row, and the error raises;
  - no patient: a `not_sent` row, and the handler is not called;
  - `begin` fails: the handler is not called, and the result is `unavailable`;
  - each handler status settles to its outcome;
  - the handler raises: the row stays unsettled;
  - the turn is cancelled mid-handler (`asyncio.CancelledError`): the row stays unsettled;
  - `settle` fails: the result is returned unchanged;
  - a read tool: no recorder call.
- [X] T009 [US1] In `services/chat/src/chat/agent/tools/registry.py`:
  - add `Tool.plan_act` (optional) and `ToolContext.acts: BookingActRecorder`, defaulting to `DiscardingBookingActRecorder`;
  - implement the five-step recording in `ToolRegistry._run` per research R6, keeping the `requires_patient` short-circuit behaviour for tools without `plan_act`.

  Run T008 and see it pass.
- [X] T010 [P] [US1] Write failing tests in `services/chat/tests/test_scheduling_tools.py` asserting that:
  - `book_appointment`, `reschedule_appointment` and `cancel_appointment` declare a `plan_act`, and the read tools declare none;
  - each `plan_act` raises `ToolArgumentError` on the same malformed arguments its handler rejects;
  - each `plan_act` resolves `practitioner_full_name` through `context.acts.name_of`, giving `None` for an id not in the roster.
- [X] T011 [US1] Add the three `plan_act` functions in `services/chat/src/chat/agent/tools/scheduling_tools.py`, using the same argument helpers as their handlers, and set them on the three write `Tool`s in `SCHEDULING_TOOLS`. Run T010 and see it pass.
- [X] T012 [US1] Write a failing test in `services/chat/tests/test_handle_booking.py` that the booking node calls `acts.learn_practitioners(roster)` after a successful roster read and does not call it on `booking.roster_unread`. Then implement it in `services/chat/src/chat/agent/handle_booking.py` (`_read_roster`'s caller).
- [X] T013 [US1] Write a failing test in `services/chat/tests/test_turn_api.py` that a booking turn's write lands a `booking_acts` row on **that turn's patient message**, the answering message of a burst. Also cover, still in `services/chat/tests/test_turn_api.py`:
  - a turn failing after the write keeps the row, with the patient message marked `assistant_failed` (spec US1 scenario 8);
  - a turn superseded mid-write leaves the row unsettled.

  Then build a `DatabaseBookingActRecorder` in `launch()` in `services/chat/src/chat/api/turn.py` and pass it as `ToolContext.acts`.

### Wire (contract: contracts/booking-acts.md § Wire)

- [X] T014 [P] [US1] Write failing tests in `services/chat/tests/test_chats_api.py` for `GET /chats/{chat_id}/messages`:
  - `booking_acts` is `null` on messages without acts and never `[]`;
  - it is a `seq`-ordered list on the patient message that holds acts;
  - a `null` outcome is serialized as `null`;
  - ids are absent;
  - a different session's acts never appear;
  - acts are unchanged after a staff post into the chat and after the assistant switch is turned off and on (FR-015).
- [X] T015 [US1] Add `BookingActOut` and `MessageOut.booking_acts: list[BookingActOut] | None = None` in `services/chat/src/chat/domain/schemas.py`. In `get_chat_messages` in `services/chat/src/chat/api/chats.py`, load the acts with `booking_act_repository.list_for_chat` in one scoped query and attach them by `message_id`. Run T014 and see it pass.
- [X] T016 [US1] Run the harness suite (`uv run pytest evals/harness/tests`). Then re-score the committed baseline with `make eval-compare BASE=evals/baselines/01M321DWRXSVSY7GW9RY3CR9YW NEW=evals/baselines/01M321DWRXSVSY7GW9RY3CR9YW`, which re-scores both sides offline and writes only under `.run/evals/comparisons/`. **Never** use `make eval-score` on it, because that writes `report.json` into the run it scores. Confirm both pass and the comparison reports no movement (FR-023, SC-006). If `StoredMessage` parsing fails, fix it in `evals/harness/src/golden_harness/driver/turn.py`, not in recorded data. Check that `evals/baselines/` has no working-tree changes afterwards.

### Console (contract: contracts/console-ui.md § Evidence marker)

- [X] T017 [P] [US1] Add the `BookingAct` type and `Message.booking_acts: BookingAct[] | null` in `services/frontend/src/lib/chatStream.ts`, and extend the fixtures in `services/frontend/tests/chatStream.test.ts` so a thread with acts parses.
- [X] T018 [US1] Write failing tests in `services/frontend/tests/OutcomeDisclosure.test.tsx` for:
  - a marker appearing for acts alone;
  - the state being `needs-person` for an `unknown` or `null` outcome and `served` for `done`/`refused`/`unchanged`/`not_sent` on an otherwise unmarked message;
  - each act line's wording per the console-ui.md table (reschedule shows both times; a missing name reads "a practitioner not named in the record");
  - `booking-act` with `data-operation` and `data-outcome` (with `null` attributed as `unknown`);
  - "unknown" being present in text;
  - `booking-outcome-stub` being absent everywhere.
- [X] T019 [US1] Implement the `bookingActs` prop, the amended state rule, the act lines, and the removal of the stub paragraph in `services/frontend/src/components/OutcomeDisclosure.tsx`. Run T018 and see it pass.
- [X] T020 [US1] Pass `message.booking_acts` to `OutcomeDisclosure` in `services/frontend/src/components/MessageView.tsx`. Write the failing-first assertions first:
  - in `services/frontend/tests/MessageView.test.tsx`: a patient message with acts renders a marker in the staff thread;
  - in `services/frontend/tests/ChatWindow.test.tsx`: the patient pane renders no marker and no `booking-act` for the same message (FR-021).

### Re-read signal (contract: contracts/booking-acts.md § `GET /console/conversations`; added for FR-016a)

- [X] T042 [P] [US1] Write failing tests in `services/chat/tests/test_console_api.py` for the listing's `booking_acts_version` (FR-016a, plan invariant 8):
  - it is 0 for a chat with no acts;
  - it increases by one on `begin`, by one on `settle`, and by one on `insert_not_sent` twice over (insert plus settled);
  - it is unchanged by a new message, a staff post and the assistant switch;
  - it is never visible for another session's chat.
- [X] T043 [US1] Add `booking_acts_version` to `ConsoleConversationOut` in `services/chat/src/chat/domain/schemas.py`, and compute it as `count(booking_acts) + count(booking_acts.settled_at)` in `list_conversations_for_console` in `services/chat/src/chat/repositories/chat_repository.py`, in the same session-scoped query. Run T042 and see it pass.
- [X] T044 [P] [US1] Add `booking_acts_version: number` to `ConsoleConversation` in `services/frontend/src/lib/consoleApi.ts`. Write failing tests in `services/frontend/tests/StaffThread.test.tsx`:
  - a poll row whose `booking_acts_version` changes while `last_message_at` stays the same triggers exactly one thread re-read, and the settled act's outcome replaces "unknown" (SC-007);
  - an unchanged pair triggers none.
- [X] T045 [US1] Make `useThreadReads` in `services/frontend/src/lib/useThreadReads.ts` take `bookingActsVersion` beside `lastMessageAt` and treat the pair as the re-read key, then pass it from the poll row through `services/frontend/src/components/StaffThread.tsx` and `services/frontend/src/App.tsx`. Run T044 and see it pass, with the existing `StaffThread` and `App` tests still green.

**Checkpoint**: US1 is complete. Quickstart §4 and §5 pass on a live stack.

---

## Phase 4: User Story 2 - A staff member sees who is booked with a practitioner this week (Priority: P2)

**Goal**: A Show/Hide bookings toggle at the bottom of each roster block, opening that
practitioner's standing appointments for the next seven local days, grouped by day and kept current
by the console poll.

**Independent Test**: Seed the appointments from spec US2's Independent Test, click **Show
bookings**, and see exactly the in-window standing ones, then click **Hide bookings** (quickstart §3).

### Scheduler read (contract: contracts/practitioner-week.md § Scheduler)

- [X] T021 [P] [US2] Write failing tests in `services/scheduler/tests/test_appointment_repository.py` for `list_for_practitioner(session, session_id, practitioner_id, ends_after, starts_before)`:
  - It returns only standing rows of that practitioner in that session with `ends_at > ends_after` and `starts_at < starts_before`.
  - It includes one under way.
  - It excludes these: cancelled, ended, starts at `starts_before` exactly, another practitioner, another session.
  - Results are ordered by `starts_at`, then `id`.
  - Each row carries the patient's `full_name`.
- [X] T022 [US2] Implement `list_for_practitioner` in `services/scheduler/src/scheduler/repositories/appointment_repository.py` as one statement joining `patients` with every predicate in its `WHERE` (data-model.md). Run T021 and see it pass.
- [X] T023 [P] [US2] Write failing tests in `services/scheduler/tests/test_migrations.py` for the index `ix_appointments_practitioner_status_starts`, and add it to `Appointment.__table_args__` in `services/scheduler/src/scheduler/domain/models.py`, with a revision `services/scheduler/alembic/versions/<rev>_index_practitioner_appointments.py` revising the current head `e3c07a5b9d14`.
- [X] T024 [P] [US2] Write failing API tests in `services/scheduler/tests/test_practitioners_api.py` for `GET /practitioners/{id}/appointments`:
  - 200 with `{"appointments": [...]}`;
  - 404 "practitioner not found" for a missing or other-session id;
  - 401 without `X-Session-Id`;
  - 422 for a missing, malformed or offset-carrying bound, and for `starts_before <= ends_after`.
- [X] T025 [US2] Add `PractitionerAppointmentOut` and `PractitionerWeekOut` to `services/scheduler/src/scheduler/domain/schemas.py`, and the route to `services/scheduler/src/scheduler/api/practitioners.py`. It resolves the practitioner with `practitioner_repository.get` before querying. Run T024 and see it pass.

### Console route (contract: contracts/practitioner-week.md § Console, § Transport)

- [X] T026 [P] [US2] Write failing tests in `services/chat/tests/test_practitioner_proxy.py`:
  - `scheduler_rest.forward(query=…)` sends an encoded query string, and still refuses `?` in `path`;
  - `GET /console/practitioners/{id}/appointments?local_now=…` answers 401 with no cookie, with nothing sent;
  - it answers 422 for a missing, malformed or offset `local_now`;
  - it forwards `ends_after`/`starts_before` computed from `local_now`;
  - it relays 200 and 404 verbatim;
  - it maps unreachable → 503 and timeout → 504 with the **read** wording from the contract;
  - the existing write routes keep their current 503/504 wording.
- [X] T027 [P] [US2] Write failing unit tests in `services/chat/tests/test_practitioner_week_bounds.py` for `practitioner_week_bounds(local_now)`. Cover a mid-day `now`, 23:59:59, 00:00:00, and a month or year boundary. `starts_before` is always midnight at the start of the eighth day.
- [X] T028 [US2] Implement `services/chat/src/chat/domain/practitioner_week.py` (`practitioner_week_bounds`). Run T027 and see it pass.
- [X] T029 [US2] Lift `ChatRequest._reject_timezone_aware` into one shared validator in `services/chat/src/chat/domain/schemas.py`, used by both `ChatRequest` and the new route. Existing tests in `services/chat/tests/test_validation.py` must stay green.
- [X] T030 [US2] Add the `query` parameter to `forward` in `services/chat/src/chat/clients/scheduler_rest.py`. Parameterize `_proxy`'s 503/504 details, defaulting to today's write wording, and add the route in `services/chat/src/chat/api/console.py`. Run T026 and see it pass.
- [X] T031 [US2] Write `tests/integration/test_practitioner_week_roundtrip.py`: a real chat app against a real scheduler database, where a seeded booking appears in the console read and a cancelled one does not. Run it with `make test-integration`.

### Roster UI (contract: contracts/console-ui.md § Practitioner roster)

- [X] T032 [P] [US2] Add `PractitionerAppointment` and `fetchPractitionerWeek(practitionerId, localNow)` in `services/frontend/src/lib/consoleApi.ts`, with a failing-first test in `services/frontend/tests/consoleApi.test.ts`. It sends `local_now` from `localNow()`, returns the list on 200, and throws errors whose kind distinguishes not-found (404) from unreadable (503/504/network).
- [X] T033 [P] [US2] Write failing tests in `services/frontend/tests/PractitionerWeek.test.tsx` for:
  - the loading state (`region-loading` with `data-region="practitioner-week"`);
  - days grouped in date order, with each appointment's name and start–end;
  - `week-empty` naming the practitioner;
  - `week-error` wording for unreadable versus not-found;
  - a re-read on each `pollTick` advance, with no overlapping reads and a stale answer dropped;
  - a failed refresh after a success showing `week-error`, not the stale list.
- [X] T034 [US2] Implement `services/frontend/src/components/PractitionerWeek.tsx`. Run T033 and see it pass.
- [X] T035 [US2] Write failing tests in `services/frontend/tests/PractitionerAdmin.test.tsx` for:
  - each `practitioner` block ending with `bookings-toggle` reading **Show bookings** with `aria-expanded=false`;
  - clicking it opening `practitioner-week` inside that block and reading **Hide bookings**, and clicking again removing it;
  - two blocks open independently;
  - the open state surviving a roster re-render;
  - every block starting closed after leaving for the edit view and returning;
  - the edit view (`practitioner-edit`) containing no appointments element;
  - `appointments-stub` being absent;
  - a closed block triggering no fetch;
  - a patient name in an open list being plain text with no link or button (FR-009).
- [X] T036 [US2] Implement the per-block toggle and open-state map, remove the edit-view stub, and accept a `pollTick` prop in `services/frontend/src/components/PractitionerAdmin.tsx`. Pass `poll.tick` in `services/frontend/src/App.tsx`. Run T035 and see it pass, and keep `services/frontend/tests/App.test.tsx` green.

**Checkpoint**: US2 is complete. Quickstart §3 passes on a live stack.

---

## Phase 5: Polish & Cross-Cutting Concerns

- [X] T037 [P] Update the hook table in `services/frontend/.claude/CLAUDE.md`. Retire `booking-outcome-stub` and `appointments-stub`, add the hooks from contracts/console-ui.md, and add one line recording why these two were retired despite the "may not be removed" rule (FR-022).
- [X] T038 [P] Update `docs/ROADMAP.md` Phase 3a: both leftovers are shipped as `specs/016-staff-booking-visibility/`, the week is on the roster behind a show/hide toggle, and it is read over REST rather than `ListAppointments` for the reason in research R1 (FR-024).
- [X] T039 [P] Add a README section to `README.md` covering the tradeoffs: booking acts anchored on the patient message, write-ahead and settled once, a snapshot, and a table rather than JSONB; and the week as a scheduler REST read with the window computed in chat (FR-024).
- [X] T040 [P] Add a key-design-decision entry beside 011's `request_outcomes` entry in `.claude/CLAUDE.md`. It covers booking acts as their own shape, write-ahead, NULL read as unknown, agent-blind (FR-021a) and the patient-message anchor.
- [X] T041 Run the full gates (`make lint`, `make typecheck`, `make test-unit`, `make test-frontend`, `make test-integration`), then walk `specs/016-staff-booking-visibility/quickstart.md` §3–§5 on a live stack and record the results in `specs/016-staff-booking-visibility/quickstart.md`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (T001)**: first.
- **US1 (T002–T020, T042–T045)** and **US2 (T021–T036)**: both depend only on T001. They are independent of
  each other.
- **Polish (T037–T041)**: after whichever stories are being shipped. T041 comes last.

### Within US1

- T002 → T003 → (T004 → T005)
- T006 → T007
- T008 → T009, which needs T007
- T010 → T011, which needs T009
- T012 needs T007
- T013 needs T005, T007, T009 and T011
- T014 → T015, which needs T005
- T016 needs T015
- T017 → T018 → T019 → T020
- T042 → T043, which needs T005
- T044 → T045, which needs T020 (T043 only for the live stack; T044 uses fixtures)
- The IDs T042–T045 were appended after analysis rather than renumbering the tasks that follow.

### Within US2

- T021 → T022 → (T023 ∥ T024 → T025)
- T026 ∥ T027 → T028, then T029 → T030
- T031 needs T025 and T030
- T032 ∥ T033 → T034 → T035 → T036

### Parallel opportunities

- **US1:** T002, T004, T006, T008, T010, T014 and T017 are test or type tasks in distinct files,
  and can be written together.
- **US2:** T021, T023, T024, T026, T027, T032 and T033 likewise.
- **Across stories:** US1 and US2 can proceed in parallel end to end, because they share no files
  except `services/frontend/src/App.tsx`, which T036 and T045 both touch. Land those two
  one after the other.
- **Polish:** T037–T040 are disjoint documents.

## Parallel Example: User Story 2

```bash
# Tests first, together (each must be observed failing):
T021 services/scheduler/tests/test_appointment_repository.py
T024 services/scheduler/tests/test_practitioners_api.py
T026 services/chat/tests/test_practitioner_proxy.py
T027 services/chat/tests/test_practitioner_week_bounds.py
T033 services/frontend/tests/PractitionerWeek.test.tsx
```

## Implementation Strategy

### MVP first (US1)

1. T001 → T002–T020.
2. **Stop and validate** with quickstart §4–§5. Staff can now see what the assistant did to the
   schedule, which is the higher-value gap.
3. Ship behind T037, T039 and T040. The remaining docs can follow with US2.

### Incremental delivery

- **US1**: booking acts visible. On its own it retires only `booking-outcome-stub`.
- **US2**: the roster week. It retires `appointments-stub`.
- **Polish**: ROADMAP marks Phase 3a's leftovers shipped only once both are in.

## Phase 6: Convergence

- [X] T046 Restore the pre-016 answer for a malformed write in a chat with no patient record: catch `ToolArgumentError` from `plan_act` on the no-patient path in `services/chat/src/chat/agent/tools/registry.py`, return `_NO_PATIENT_RESULT` and record nothing, so the model sees exactly what it saw before this feature. Currently `test_registry_booking_acts.py:186` pins the raise. Per FR-021a (contradicts)
- [X] T047 Extend the agent-blind import walk in `services/chat/tests/test_booking_acts.py` so that nothing under `chat/agent/` may import `chat.repositories.booking_act_repository`, the `BookingAct` model or `BookingActOut`, not just `chat.agent.booking_acts`. Per FR-021a / plan: invariant 7 (partial)
- [X] T048 Include the year in booking-act dates (`dayAndTime` in `services/frontend/src/lib/localTime.ts`, used by `services/frontend/src/components/OutcomeDisclosure.tsx`), because an act is a permanent record and a 90-day horizon crosses years. Write the year-bearing wording tests first in `services/frontend/tests/OutcomeDisclosure.test.tsx` and `services/frontend/tests/localTime.test.ts`. Per US1/AC1–3 (partial)
- [X] T049 Give a served-state marker on a message carrying only booking acts an accessible label that names a booking record ("What the assistant did to the schedule"), not "What this answer drew on", in `services/frontend/src/components/OutcomeDisclosure.tsx`, test-first in `services/frontend/tests/OutcomeDisclosure.test.tsx`. Per FR-017 (partial)

## Phase 7: Convergence

- [X] T050 Make the agent-blind walk in `services/chat/tests/test_booking_acts.py` flag `from chat.repositories.booking_act_repository import <name>` (any `ImportFrom` whose module is the repository itself), and add that form to `test_the_walk_catches_every_import_form`. Per FR-021a / plan: invariant 7 (partial)
