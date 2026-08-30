---

description: "Task list for Rescheduling and Cancellation (Phase 1d, part 1)"
---

# Tasks: Rescheduling and Cancellation (Phase 1d, part 1)

**Input**: Design documents from `/specs/006-reschedule-and-cancel/`

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
- **[Story]**: Which user story this task belongs to (US1–US4)
- Exact file paths are included in every task

## Path Conventions

`uv` workspace monorepo (see plan.md "Source Code"): `services/chat/src/chat/`,
`services/scheduler/src/scheduler/`, `packages/shared-models/src/`, `packages/shared-proto/src/`.
Unit tests are colocated per member (`<member>/tests/`); cross-service tests live at
`tests/integration/`. `services/frontend/` is **untouched by this feature**.

**Mocking discipline** (`docs/testing-strategy.md`): every test that exercises a turn MUST mock
`AsyncAnthropic` — including the tool-use responses that drive the change loop — and assert on
unmocked artifacts (rows written, gRPC requests issued, log events emitted), never on canned model
text. The confirmation rules of FR-024–FR-030 are only testable against a script.

**Where the weight is**: the load-bearing rules of this feature are *datastore* rules — two partial
exclusion constraints, a partial unique index, and a conditional `UPDATE` whose `WHERE` clause is the
staleness guard. All of them are testable against a real database before any service code exists, and
all of them pass every single-threaded application test when written wrong. Phase 2 is therefore
large relative to the feature, deliberately.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the pre-change baseline. This feature adds **no dependency, no environment
variable, no service, and no database** (plan.md "Technical Context"), so there is nothing to install.

- [X] T001 Verify the 005 baseline is green before any edit: `make sync && make lint && make typecheck && make test-unit` from the repo root, and record the pass
- [X] T002 [P] Confirm the scheduler test database is reachable, since the whole datastore-rule tier runs against a real one: `docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_scheduler_test -c '\d appointments'` shows 005's two **unconditional** exclusion constraints and the `appointments_idempotency_key_unique` constraint

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The shared vocabulary, the wire contract, the one migration, and the status predicate on
every audited read. Every user story writes or reads an appointment's status, so none can begin until
the column exists and the constraints that give it meaning are partial.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

### Shared vocabulary

- [X] T003 Write tests for `AppointmentStatus` (two members) and `ChangeFailureReason` (twelve members in the FR-006 precedence order), including the **member-by-member pin** asserting every `BookingFailureReason` value appears in `ChangeFailureReason` with the identical string (research #10), in `packages/shared-models/tests/test_scheduling.py`; observe failing
- [X] T004 Add `AppointmentStatus` and `ChangeFailureReason` to `packages/shared-models/src/shared_models/scheduling.py` exactly as declared in data-model.md "Cross-cutting Python types"; run T003 to green

### gRPC contract

- [X] T005 [P] Write stub smoke assertions in `packages/shared-proto/tests/test_smoke.py`: `AppointmentStatus`, `ChangeFailureReason`, `ChangeFailure`, `NoChange`, `ChangeAppointmentResponse`, `RescheduleAppointmentRequest`, `CancelAppointmentRequest`, `ListAppointmentsRequest`, `ListAppointmentsResponse`, `TimeFilter`, `StatusFilter` all import; `Appointment` has field `status`; `CheckAvailabilityRequest` has field `excluded_appointment_id`; `ListUpcomingAppointments` is **absent** from the service descriptor; observe failing
- [X] T006 Apply the delta in `specs/006-reschedule-and-cancel/contracts/scheduling.proto` to `packages/shared-proto/protos/scheduling/v1/scheduling.proto`: two rpcs added, `ListUpcomingAppointments` replaced by `ListAppointments`, `Appointment.status`, the two filter enums with their **narrowest-corner zero values**, and `CheckAvailabilityRequest.excluded_appointment_id = 7`
- [X] T007 Regenerate the stubs into `packages/shared-proto/src/shared_proto/scheduling/v1/` following `packages/shared-proto/README.md`, **including the manual import fixup**; run T005 to green

### Schema — the column and the four constraints

- [X] T008 [P] Write `services/scheduler/tests/test_appointment_status_constraints.py` against a real database: a cancelled row does **not** block a standing booking of the same interval on either exclusion constraint; two standing rows still collide on both; the partial unique index accepts a second row reusing a **cancelled** row's `idempotency_key` and rejects a duplicate among standing rows; the `CHECK` rejects a status outside `{standing, cancelled}`; observe failing
- [X] T009 [P] Extend `services/scheduler/tests/test_migrations.py` with the upgrade/downgrade round trip for this revision: `status VARCHAR(16) NOT NULL DEFAULT 'standing'` present, both exclusion constraints carry `WHERE (status::text = 'standing'::text)`, `ix_appointments_idempotency_key_standing` exists, `appointments_idempotency_key_unique` is **gone**, `ix_appointments_patient_status_starts` exists; observe failing
- [X] T010 Modify `services/scheduler/src/scheduler/domain/models.py`: add `Appointment.status` with its `CheckConstraint`, add `where=` to both `ExcludeConstraint`s, replace the `UniqueConstraint("idempotency_key", …)` with `Index("ix_appointments_idempotency_key_standing", "idempotency_key", unique=True, postgresql_where=…)`, and add `Index("ix_appointments_patient_status_starts", "patient_id", "status", "starts_at")`
- [X] T011 Create `services/scheduler/alembic/versions/<rev>_add_appointment_status.py` performing the four steps in plan.md "Storage" (add column; drop and recreate both exclusion constraints partial; drop the unique constraint and create the partial unique index; add the composite index), with a working downgrade; run T008 and T009 to green
- [X] T012 [P] Write `services/scheduler/tests/test_cascade_takes_cancelled.py` pinning that deleting a patient, and deleting a practitioner, removes that party's **cancelled** appointments along with their standing ones (research #4). This is a regression pin, not a red-green step: it must pass as written the moment T011 lands, and exists to fail the day someone scopes a cascade to `status = 'standing'`

### The audited reads gain their status predicate

- [X] T013 Write the status-predicate tests: `busy_intervals` ignores cancelled appointments of both the practitioner and the patient, in `services/scheduler/tests/test_appointment_repository.py`; `get_by_idempotency_key` ignores a cancelled row holding the key, and a booking whose key belonged to a cancelled appointment inserts a **new** appointment rather than replaying, in `services/scheduler/tests/test_idempotency.py`; observe failing
- [X] T014 Apply the predicates in `services/scheduler/src/scheduler/repositories/appointment_repository.py`: `+ status = 'standing'` on `busy_intervals` and on `get_by_idempotency_key`, and rename `_IDEMPOTENCY_KEY_CONSTRAINT` to match the **index** name `ix_appointments_idempotency_key_standing` (research #3); run T013 to green

### The wire and the client learn about status

- [X] T015 [P] Write `services/scheduler/tests/test_converters.py` asserting `to_proto_appointment` sets `status` for both members and that an unspecified status never round-trips as standing; observe failing
- [X] T016 Add the `AppointmentStatus` mapping in both directions to `services/scheduler/src/scheduler/grpc/converters.py` and set `status` in `to_proto_appointment`; run T015 to green
- [X] T017 [P] Write the test that `AppointmentInfo` carries `status: AppointmentStatus` and that the client reads it off the wire, in `services/chat/tests/test_scheduling_client.py`; observe failing
- [X] T018 Add `status: AppointmentStatus` to `AppointmentInfo` in `services/chat/src/chat/clients/scheduling.py`; run T017 to green

**Checkpoint**: the column exists, a cancelled appointment blocks nothing, its key is free, and both services can name a status. User story work can now begin.

---

## Phase 3: User Story 1 - Cancel an appointment I no longer need (Priority: P1) 🎯 MVP

**Goal**: A patient can cancel an upcoming appointment through conversation, after an explicit
confirmation that reads the appointment back to them. The record is retained and marked cancelled,
the slot becomes bookable again immediately, the booking key is released, and the patient's listing
answers all four time/status corners.

**Independent Test**: Book an appointment, then in the same chat ask to cancel it. The assistant
states the start date-time, practitioner full name and specialty and asks to confirm; nothing changes
until the patient says yes; afterwards the appointment is absent from "what have I got booked?",
present and labelled cancelled under "what have I cancelled?", its slot is offered by the
availability check, and booking that exact slot again succeeds and produces a **new** id.

### Tests for User Story 1 (write first, confirm failing) ⚠️

- [X] T019 [P] [US1] Write `services/scheduler/tests/test_cancel.py`: the conditional `UPDATE` sets `status` and returns both sides; an appointment id from **another session** is not found rather than cancelled (FR-018); an already-cancelled appointment answers **no-op**, not a failure (FR-017); an appointment whose current start is not strictly after `local_now` is refused `already_started`; a guard naming a start or practitioner the row no longer holds is refused `stale_confirmation`; a guard naming the **target** state answers no-op (the second arm, FR-021); every refusal leaves start, end, practitioner and id untouched (FR-008, SC-005)
- [X] T020 [P] [US1] Write `services/scheduler/tests/test_change_precedence.py` covering the four eligibility reasons in their fixed order — `appointment_not_found` → `already_cancelled` → `already_started` → `stale_confirmation` — including cases breaking two rules at once to pin which one is reported, and asserting a **cancellation** is reachable by only **three** of them — `already_cancelled` is the target state of a cancellation, not a refusal of one, so it answers `no_change` (research #9, contracts/scheduling.proto)
- [X] T021 [P] [US1] Write `services/scheduler/tests/test_list_appointments.py`: the four time/status corners return exactly their own appointments and nothing leaks between them (SC-013); the future leg ascends and is unbounded, the past leg descends and is capped at 20 with `past_truncated` set from a `LIMIT 21` probe; a request spanning both returns two separate legs; the read carries session **and** patient as predicates
- [X] T022 [P] [US1] Write `services/scheduler/tests/test_servicer_changes.py` for `CancelAppointment`'s three outcomes (`appointment` / `no_change` / `failure`), and rewrite `services/scheduler/tests/test_servicer_appointments.py` onto `ListAppointments` — replacing its 19 `ListUpcomingAppointments` assertions rather than adding beside them: each filter combination maps to the right legs, an unset filter pair yields the **future + standing** corner, and an unresolvable patient aborts `NOT_FOUND` with detail `NotFoundEntity.PATIENT` (two empty legs must not stand for it)
- [X] T023 [P] [US1] Write the scheduler log-event assertions in `services/scheduler/tests/test_cancel.py`: `appointment.cancelled` carries `appointment_id`, `old_starts_at`, `practitioner_id` and **no new-start field at all** (FR-037); `appointment.unchanged` carries `operation` and `starts_at`; `change.refused` carries the single resolved `reason`; `change.key_released` carries `appointment_id` and `idempotency_key`
- [X] T024 [P] [US1] Write the chat-client tests in `services/chat/tests/test_scheduling_client.py`: `cancel_appointment()` maps the wire's three outcomes onto `ChangeApplied` / `ChangeNoOp` / `ChangeRefusal`, passes the guard fields through verbatim, and follows the 2s/2-attempt budget; `list_appointments()` returns `AppointmentListing` with both legs and `past_truncated`
- [X] T025 [P] [US1] Write `services/chat/tests/test_change_tools.py` for `cancel_appointment`: the four result shapes (`changed` / `unchanged` / `refused` / `unknown`); already-cancelled returns `unchanged`, never `refused` (FR-017); `appointment_not_found` is distinguishable from it (FR-018); the explanation table is **total** over `ChangeFailureReason`, asserted by iterating the enum; and `status: "unknown"` is a value distinct from `"unavailable"` whose explanation never says nothing happened (FR-023, research #13)
- [X] T026 [P] [US1] Rewrite the `list_my_appointments` tests in `services/chat/tests/test_scheduling_tools.py`, replacing the 6 assertions that name the removed RPC: both axes optional and defaulting to the narrowest corner, the result carrying `id` and `status` per entry, two legs never merged, and `past_truncated` surfaced
- [X] T027 [P] [US1] Write `services/chat/tests/test_handle_booking_changes.py` with scripted `AsyncAnthropic` tool-use responses: the turn's outcome is `CANCELLED` on a completed cancellation, `UNCHANGED` on a no-op, `OUTCOME_UNKNOWN` when the budget was exhausted after the request was sent; the precedence any-completed-change → `UNCHANGED` → `OUTCOME_UNKNOWN` → `UNAVAILABLE` → `REFUSED` → `AWAITING_CONFIRMATION` → `INFORMATIONAL`; and `change.outcome_unknown` is emitted chat-side at error level with `operation`, `appointment_id`, `attempts`
- [X] T028 [P] [US1] Write the confirmation-rule tests in `services/chat/tests/test_handle_booking_changes.py` (SC-002, SC-003): no `cancel_appointment` call is made without a confirmation **given in the current turn**; a reply that neither confirms nor declines changes nothing and is answered on its own terms with the confirmation re-stated in full; a bare "yes" after an intervening turn does not act until re-stated; several upcoming appointments are listed and asked between, never chosen from (FR-030); no upcoming appointments says so with no empty list (FR-031); every date-time the assistant states is plain local with no timezone, resolved against the client-supplied `local_now` and never a server clock (FR-035); no appointment id, practitioner id or tool name appears in any reply text (FR-034, SC-001); a truncated past leg is reported as *that part* of the list being incomplete, never the whole answer (FR-016); and one confirmation never acts on more than one appointment (FR-030)
- [X] T029 [P] [US1] Write the `compose_answer` test in `services/chat/tests/test_compose_answer.py`: a merged reply cannot claim a cancellation the outcome does not record, for each of `CANCELLED`, `UNCHANGED` and `OUTCOME_UNKNOWN` (FR-028, SC-007)

### Implementation for User Story 1

- [X] T030 [US1] Implement `cancel()` in `services/scheduler/src/scheduler/repositories/appointment_repository.py` as **one** conditional `UPDATE … FROM appointments AS old … RETURNING` carrying identity, `session_id`, `status = 'standing'`, `starts_at > local_now` and the guard in its `WHERE` (research #5, #6) — nothing is read first. Note the asymmetry research #5's SQL does not spell out: a cancellation has no destination, so its guard carries only the **described-state** arm. FR-021's second arm is discharged for cancel by the classification path instead — a re-sent cancellation fails the `status = 'standing'` predicate, and `already_cancelled` maps to **no-op**, not failure (research #9)
- [X] T031 [US1] Implement `classify_change_failure()` in the same module, run **only** on rowcount 0 and scoped to the session, resolving the four eligibility reasons in precedence; it returns a reason and decides nothing, so it cannot reintroduce the race it reports on
- [X] T032 [US1] Implement `list_for_patient()` in the same module: two statements, two orderings, `LIMIT 21` on the past leg to detect truncation; delete `list_upcoming()`; run T019–T021 to green
- [X] T033 [US1] Add the change-outcome converters to `services/scheduler/src/scheduler/grpc/converters.py`: `ChangeFailureReason` both ways, `ChangeFailure`, `NoChange`, the `ChangeAppointmentResponse` oneof, the two filter enums, and the two-leg `ListAppointmentsResponse`
- [X] T034 [US1] Implement `CancelAppointment` and `ListAppointments` in `services/scheduler/src/scheduler/grpc/servicer.py`, and **remove** `ListUpcomingAppointments`; run T022 to green
- [X] T035 [US1] Migrate the last references to the removed RPC that no other task owns: the 2 in `services/scheduler/tests/test_servicer_provisioning.py` (T022 and T026 carry `test_servicer_appointments.py` and `test_scheduling_tools.py`, T005 carries `test_smoke.py`), then sweep — `rg 'ListUpcomingAppointments|list_upcoming' --glob '!specs/**' --glob '!**/*_pb2*'` must return nothing. Without this the unit suite goes red at T034 with no task owning the fix
- [X] T036 [US1] Emit `appointment.cancelled`, `appointment.unchanged`, `change.refused` and `change.key_released` per `contracts/log-events.md` from `services/scheduler/src/scheduler/repositories/appointment_repository.py`, **after** the commit and never awaited for correctness (FR-041); run T023 to green
- [X] T037 [US1] Add `ChangeApplied`, `ChangeNoOp`, `ChangeRefusal` and `AppointmentListing` to `services/chat/src/chat/clients/scheduling.py`, and implement `cancel_appointment()` and `list_appointments()` (replacing `list_upcoming_appointments()`) — this module stays the only one importing `shared_proto`; run T024 to green
- [X] T038 [US1] Add the `cancel_appointment` tool to `SCHEDULING_TOOLS` in `services/chat/src/chat/agent/tools/scheduling_tools.py` per `contracts/agent-tools.md`, with the closed schema, the handler-authored explanation table keyed by `ChangeFailureReason`, and `_outcome_unknown()` changed to return `status: "unknown"`; run T025 to green
- [X] T039 [US1] Extend `list_my_appointments`'s schema and handler in the same file with `time_filter`/`status_filter`, returning `id`, `status`, the two legs and `past_truncated`; run T026 to green
- [X] T040 [US1] Add `CANCELLED`, `UNCHANGED` and `OUTCOME_UNKNOWN` to `BookingOutcome` and extend `_outcome_from`'s precedence in `services/chat/src/chat/agent/handle_booking.py`, and emit `change.outcome_unknown`; run T027 to green
- [X] T041 [US1] Add the confirmation, disclosure and refusal rules from `contracts/agent-tools.md` "Loop and prompt rules" **plus the truncation rule stated in that document's `list_my_appointments` section** — on `past_truncated`, say that *that part* of the list is not complete, never that the whole answer is partial (FR-016) to the system prompt in `services/chat/src/chat/agent/handle_booking.py`; run T028 to green
- [X] T042 [US1] Constrain `services/chat/src/chat/agent/compose_answer.py` by the new outcomes so a merged reply cannot claim a change that did not complete; run T029 to green
- [X] T043 [US1] Write the degraded-dependency test in `services/chat/tests/test_handle_booking_changes.py` (SC-015): with the scheduler unresponsive across a cancellation turn, the patient gets a reply inside the 5-second budget that 005's FR-047 2s/2-attempt policy implies; the reply claims neither that the appointment was cancelled nor that nothing happened (FR-023); and grounded FAQ answering in the same session continues to work unchanged
- [X] T044 [US1] Write `tests/integration/test_cancel_roundtrip.py` — the chat client against a real servicer and a real scheduler database: cancel, then the availability check offers the freed start time and a booking placed on it succeeds (FR-010, SC-011)
- [X] T045 [US1] Write `tests/integration/test_cancel_then_rebook.py`: rebooking the cancelled slot with the same patient, practitioner and start produces a **new** appointment with a new id rather than replaying the cancelled one — the released key (FR-011)
- [X] T046 [US1] Write the cross-session guard test in `tests/integration/test_cancel_roundtrip.py`: an appointment id from another session resolves as not found and nothing in the reply distinguishes it from one that never existed (FR-018, SC-014)

**Checkpoint**: cancellation works end to end, the freed slot is immediately bookable, and the listing answers all four corners. This is a shippable increment on its own.

---

## Phase 4: User Story 2 - Move an appointment to a different time (Priority: P2)

**Goal**: A patient keeps their practitioner and moves the appointment to another time. It is the
same appointment — same identifier — with a new start and an end recomputed from the practitioner's
current length. Every rule that governs a booking's placement governs the move.

**Independent Test**: Book at 09:00, ask to move to 10:00, confirm; the patient holds exactly one
appointment, with the id it already had, at 10:00, ending one appointment-length later; 09:00 is
offered again and 10:00 is not; and asking to move it again offers **10:00 among the options**,
because the appointment does not block its own slot.

**Depends on**: Phase 3 — `classify_change_failure()`, the change converters and the change result
types are extended here rather than rebuilt, and the racing-changes test needs `cancel()`.

### Tests for User Story 2 (write first, confirm failing) ⚠️

- [X] T047 [P] [US2] Write `services/scheduler/tests/test_reschedule.py`: the move rewrites `starts_at`/`ends_at` and returns both sides from one statement (research #6); `ends_at` is derived from the practitioner's length read at the moment of the change, never carried over (FR-004); the id, the patient and the practitioner are unchanged; a refusal leaves the row exactly as it was (SC-005); the two guard arms behave as in T019
- [X] T048 [P] [US2] Extend `services/scheduler/tests/test_change_precedence.py` to all twelve reasons in order, pinning that `already_started` (the appointment's **current** start) and `in_past` (the **new** start asked for) are different values reached by different situations, and that a reschedule can be refused by any of the twelve
- [X] T049 [P] [US2] Write the placement tests in `services/scheduler/tests/test_reschedule.py` asserting a moved appointment is validated by `domain/availability.validate_start` — the same implementation booking uses — for grid, working range, horizon and past (FR-006, SC-004)
- [X] T050 [P] [US2] Write the `excluded_appointment_id` tests in `services/scheduler/tests/test_appointment_repository.py`: `busy_intervals` omits the named appointment from **both** the practitioner's and the patient's commitments; an id from another session excludes nothing rather than revealing it exists; and the moved appointment's own current slot is therefore among its options (FR-007, research #7)
- [X] T051 [P] [US2] Write `RescheduleAppointment` servicer tests in `services/scheduler/tests/test_servicer_changes.py` (three outcomes; `previous_starts_at` present only alongside a real move) and the `CheckAvailability` exclusion test in `services/scheduler/tests/test_availability.py`
- [X] T052 [P] [US2] Write the `appointment.rescheduled` log assertions in `services/scheduler/tests/test_reschedule.py`: `appointment_id`, `old_starts_at`, `new_starts_at`, and both practitioner fields always present; and that a move which transitioned nothing emits `appointment.unchanged` **instead**, never both (FR-036, FR-040)
- [X] T053 [P] [US2] Write the chat-client tests for `reschedule_appointment()` in `services/chat/tests/test_scheduling_client.py`: the three outcomes, `previous_starts_at` carried onto `ChangeApplied`, guard passthrough, and the same 2s/2-attempt budget as every other call (FR-023)
- [X] T054 [P] [US2] Write the `reschedule_appointment` tool tests in `services/chat/tests/test_change_tools.py` (four result shapes, `"change": "rescheduled"`, the reason→explanation mapping for the six placement reasons) and the `check_availability` `excluded_appointment_id` passthrough test in `services/chat/tests/test_scheduling_tools.py`
- [X] T055 [P] [US2] Write the move-specific confirmation tests in `services/chat/tests/test_handle_booking_changes.py`: the read-back states **both** the current and the proposed start (FR-026); the outcome is `RESCHEDULED`; a stale refusal re-describes the appointment and asks again and **never** re-issues the change (FR-022, SC-018); a placement refusal is accompanied by alternatives drawn from `check_availability` (FR-032); **every** start the assistant offers in a move turn appears in that turn's `check_availability` result, none invented, rounded or inferred (FR-033); and `already_started`, `already_cancelled` and `appointment_not_found` are answered with **zero** alternative times, since those three admit none (FR-032, SC-018)

### Implementation for User Story 2

- [X] T056 [US2] Implement `reschedule()` in `services/scheduler/src/scheduler/repositories/appointment_repository.py` for the same-practitioner path: one `UPDATE … FROM appointments AS old … RETURNING` with the predicate of research #5, `ends_at` recomputed from the practitioner's current length, placement validated through `domain/availability.validate_start`; run T047 and T049 to green
- [X] T057 [US2] Extend `classify_change_failure()` with booking's eight reasons after the four, giving the full twelve-value precedence; run T048 to green
- [X] T058 [US2] Add `excluded_appointment_id` to `busy_intervals` in the same module, session-scoped, omitting the appointment from both parties' commitments; run T050 to green
- [X] T059 [US2] Implement `RescheduleAppointment` in `services/scheduler/src/scheduler/grpc/servicer.py` and thread `excluded_appointment_id` through `CheckAvailability`; add `previous_starts_at`/`previous_practitioner_id` to the response in `services/scheduler/src/scheduler/grpc/converters.py`; run T051 to green
- [X] T060 [US2] Emit `appointment.rescheduled` per `contracts/log-events.md` from the repository, after the commit; run T052 to green
- [X] T061 [US2] Implement `reschedule_appointment()` in `services/chat/src/chat/clients/scheduling.py`, carrying `previous_starts_at` onto `ChangeApplied`; run T053 to green
- [X] T062 [US2] Add the `reschedule_appointment` tool to `SCHEDULING_TOOLS` in `services/chat/src/chat/agent/tools/scheduling_tools.py` per `contracts/agent-tools.md`, and add the optional `excluded_appointment_id` property to `check_availability`'s schema and handler; run T054 to green
- [X] T063 [US2] Add `RESCHEDULED` to `BookingOutcome` and the FR-026 read-back rule plus the per-reason response table to the system prompt in `services/chat/src/chat/agent/handle_booking.py`; run T055 to green
- [X] T064 [US2] Write `tests/integration/test_reschedule_roundtrip.py`: a move through the real client and servicer leaves exactly one appointment with its original id at the new time, the old slot offered again and the new one not
- [X] T065 [US2] Write `tests/integration/test_change_resend.py`: calling `RescheduleAppointment` twice with identical arguments, quoting the pre-move state **both** times, returns `appointment` then `no_change` — **not** `stale_confirmation` (SC-008, quickstart Scenario 6)
- [X] T066 [US2] Write `tests/integration/test_change_race.py`: a cancellation and a move issued concurrently for one appointment — the pairing the datastore cannot catch, since a cancellation collides with nothing — leave exactly one applied and the other refused `stale_confirmation`, with no completed change overwritten (FR-021, SC-016)
- [X] T067 [US2] Write `tests/integration/test_move_sequence.py`: 09:00 → 10:00 → 09:00 → 10:00 as three confirmed moves against one appointment; all three take effect in order, the appointment finishes at 10:00 holding the id it started with, and no move is answered by replaying an earlier one (FR-019, FR-020, SC-017)

**Checkpoint**: cancellation and moving both work independently, and a re-sent or raced change behaves correctly.

---

## Phase 5: User Story 3 - See a different practitioner instead (Priority: P3)

**Goal**: The appointment changes hands. Practitioner, start and end change together in one write,
the identifier survives, and the patient is told when the change alters how long they must be at the
clinic.

**Independent Test**: Book with practitioner A, ask to see practitioner B instead, confirm; the
patient holds exactly one standing appointment with the id it already had, now with B, ending one of
**B's** appointment-lengths later; A's slot is offered again; and a forced refusal leaves the
appointment with A entirely untouched.

**Depends on**: Phase 4 — this is one more field on the same write and the same tool.

### Tests for User Story 3 (write first, confirm failing) ⚠️

- [X] T068 [P] [US3] Extend `services/scheduler/tests/test_reschedule.py`: with `new_practitioner_id` set, practitioner, start and end change **together or not at all** (FR-003); `ends_at` is derived from the **new** practitioner's length, so an appointment can come out longer or shorter than it went in (FR-004); an unknown practitioner is refused `practitioner_not_found` with the row untouched; a swap keeping the same start succeeds when the new practitioner is free then (FR-007)
- [X] T069 [P] [US3] Extend `services/scheduler/tests/test_servicer_changes.py`: `new_practitioner_id` left empty means "keep the practitioner it has", and `previous_practitioner_id` comes back alongside a completed move
- [X] T070 [P] [US3] Extend the `appointment.rescheduled` assertions in `services/scheduler/tests/test_reschedule.py`: `old_practitioner_id` and `new_practitioner_id` are **always** present and differ on a swap, so a same-time swap cannot read as a change that did nothing (FR-038)
- [X] T071 [P] [US3] Extend `services/chat/tests/test_scheduling_client.py`: `ChangeApplied` carries `previous_practitioner_full_name`
- [X] T072 [P] [US3] Extend `services/chat/tests/test_change_tools.py`: the tool accepts `new_practitioner_id`, omits it to keep the current practitioner, and returns `previous_practitioner_full_name` on a completed swap
- [X] T073 [P] [US3] Extend `services/chat/tests/test_handle_booking_changes.py` with the swap confirmation rules (SC-003): both practitioners named with both specialties and framed as the same appointment changing (FR-027); the new length or end time stated **whenever it differs** and nothing said about length when it does not (FR-025); an unmatched specialty request names the specialties that do exist and leaves the appointment alone (FR-032)

### Implementation for User Story 3

- [X] T074 [US3] Extend `reschedule()` in `services/scheduler/src/scheduler/repositories/appointment_repository.py` with the `new_practitioner_id` path: the new practitioner is resolved session-scoped, `ends_at` derived from **its** length, and all three columns written by the one statement; run T068 to green
- [X] T075 [US3] Thread `new_practitioner_id` and `previous_practitioner_id` through `services/scheduler/src/scheduler/grpc/servicer.py` and `converters.py`; run T069 to green
- [X] T076 [US3] Add `old_practitioner_id`/`new_practitioner_id` to the `appointment.rescheduled` event; run T070 to green
- [X] T077 [US3] Add `previous_practitioner_full_name` to `ChangeApplied` in `services/chat/src/chat/clients/scheduling.py`; run T071 to green
- [X] T078 [US3] Add `new_practitioner_id` to the `reschedule_appointment` tool schema and handler in `services/chat/src/chat/agent/tools/scheduling_tools.py`; run T072 to green
- [X] T079 [US3] Add the FR-025 length-disclosure and FR-027 two-practitioner rules to the system prompt in `services/chat/src/chat/agent/handle_booking.py`; run T073 to green
- [X] T080 [US3] Write `tests/integration/test_practitioner_swap_roundtrip.py`: one standing appointment with the original id and the new practitioner, `ends_at - starts_at` equal to the **new** practitioner's duration, and the old practitioner's slot offered again

**Checkpoint**: all three change operations are independently functional.

---

## Phase 6: User Story 4 - Trace every change that was made (Priority: P4)

**Goal**: Every completed change is recoverable from the logs alone, every non-completing outcome is
recorded as the thing it is, and the count of change records equals the count of appointments
actually altered.

**Independent Test**: Perform one cancellation, one move and one practitioner swap; each produces a
record carrying the appointment identifier and the old start, with the new start present for the two
moves and **absent** for the cancellation, all on the turn that caused them. Then decline one
confirmation and force one refusal, and confirm neither produced a change record.

**Depends on**: any one of Phases 3–5. The events are emitted there; this phase pins the record
contract as a whole and closes any gap that per-story testing left.

### Tests for User Story 4 (write first, confirm failing) ⚠️

- [X] T081 [P] [US4] Write `services/scheduler/tests/test_change_records.py` asserting the five scheduler events field-for-field against `contracts/log-events.md` — including that `appointment.cancelled` carries **no** new-start key at all, not an empty one (FR-037), and that `appointment.unchanged` and `appointment.rescheduled` are mutually exclusive for one request
- [X] T082 [P] [US4] Write the turn-correlation test in `services/scheduler/tests/test_change_records.py`: every record carries the `turn_id` re-bound from the `x-turn-id` metadata, so a change joins to the conversation that caused it on one key (FR-039)
- [X] T083 [P] [US4] Write the negative-record tests in `services/chat/tests/test_handle_booking_changes.py` and `services/scheduler/tests/test_change_records.py`: a declined confirmation, a refusal, and an unknown outcome each produce **no** `appointment.rescheduled` or `appointment.cancelled`; the refusal appears as `change.refused` with its single reason and the unknown as `change.outcome_unknown` (FR-040, SC-010)
- [X] T084 [P] [US4] Write the over-counting test in `tests/integration/test_move_sequence.py`: the 09:00 → 10:00 → 09:00 → 10:00 sequence leaves **exactly three** `appointment.rescheduled` records, and a re-send of a move that already landed adds zero records and zero stale refusals — it appears as `appointment.unchanged` (SC-009, SC-017)
- [X] T085 [P] [US4] Write the privacy assertion in `services/scheduler/tests/test_change_records.py`: no new event carries a patient's or practitioner's name, a raw message, or a reply body — ids, times and reasons only

### Implementation for User Story 4

- [X] T086 [US4] Reconcile the event payloads against `contracts/log-events.md`'s field lists — every field present, no field beyond them, and `appointment.cancelled` carrying no new-start key — in `services/scheduler/src/scheduler/repositories/appointment_repository.py` and `services/chat/src/chat/agent/handle_booking.py`, and run T081–T085 to green

**Checkpoint**: every change is traceable, and nothing that did not happen is recorded as though it did.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: The documentation this change makes stale, and the full-suite verification.

- [X] T087 [P] Add README entries for the two choices a reader would otherwise reverse-engineer from a migration: that cancellation **retains** the record rather than deleting it, and that the consequences of retaining it are carried by **partial** constraints rather than application filters — plus the note that 005's FR-064 key lifetime is amended by FR-011 (plan.md "Documentation changes carried by this feature")
- [X] T088 [P] Split the Phase 1d bullet in `docs/ROADMAP.md` to record that rescheduling and cancellation ship here and escalation follows in part 2 (FR-042)
- [X] T089 [P] Update the scheduling section of `.claude/CLAUDE.md` only if the repository-layout or command notes there went stale; `docs/testing-strategy.md` needs no change — no tier is added and no harness convention changes
- [X] T090 Run the quickstart end to end: `specs/006-reschedule-and-cancel/quickstart.md` Scenarios 1–7, starting with the `\d appointments` check that both exclusion constraints print `WHERE (status::text = 'standing'::text)`
- [X] T091 Run `make lint && make typecheck && make test` and confirm the whole suite is green
- [X] T092 Confirm the CI `test` job passes on the branch (`.github/workflows/ci.yml`) with no new services or fixtures required

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies — can start immediately
- **Foundational (Phase 2)**: depends on Setup — **BLOCKS all user stories**. Nothing here is optional: the migration, the partial constraints and the status predicates are what give every story its meaning
- **User Story 1 (Phase 3)**: depends on Foundational only. Independently shippable
- **User Story 2 (Phase 4)**: depends on Foundational; **extends** US1's `classify_change_failure()`, change converters and change result types, and its racing-changes test needs `cancel()`. Run after US1
- **User Story 3 (Phase 5)**: depends on US2 — one more field on the same write and the same tool
- **User Story 4 (Phase 6)**: depends on any one of US1–US3; pins the record contract across all of them
- **Polish (Phase 7)**: depends on all desired stories being complete

### Within Each User Story

- Tests MUST be written and observed to FAIL before implementation (TDD, non-negotiable)
- Repository (the write and its predicate) → servicer/converters → chat client → tool → node/prompt → integration
- The datastore rules come first within Phase 2 because they are testable before any service code exists, and because a partial constraint written without its `WHERE` passes every single-threaded application test

### Parallel Opportunities

- T002 runs alongside T001
- Within Phase 2: T005 (proto smoke), T008/T009 (schema tests), T012 (cascade pin), T015 and T017 touch different files and can be written together
- Within each story phase, **all** the test-writing tasks are marked [P] — they live in different files and none depends on another's implementation
- Implementation tasks within a story are largely sequential: they walk one call path from the repository outward
- US2 and US3 could be staffed in parallel with US4's record tests once US1 is green

---

## Parallel Example: User Story 1

```bash
# Launch the whole test-writing front for US1 together (all different files):
Task: "Cancel repository tests in services/scheduler/tests/test_cancel.py"
Task: "Four-reason precedence in services/scheduler/tests/test_change_precedence.py"
Task: "Two-leg listing in services/scheduler/tests/test_list_appointments.py"
Task: "Servicer outcomes in services/scheduler/tests/test_servicer_changes.py"
Task: "Client mapping in services/chat/tests/test_scheduling_client.py"
Task: "Tool result shapes in services/chat/tests/test_change_tools.py"
Task: "Outcome precedence and confirmation rules in services/chat/tests/test_handle_booking_changes.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational — **critical**, and the largest single block of this feature
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: run quickstart Scenario 1 end to end, including the freed-slot rebooking
5. A clinic where patients can book and cancel is already usable — deploy/demo if ready

### Incremental Delivery

1. Setup + Foundational → the column exists and a cancelled appointment counts for nothing
2. Add US1 → cancel, list across all four corners → validate → demo (**MVP**)
3. Add US2 → move, plus the re-send and race guarantees → validate → demo
4. Add US3 → practitioner swap → validate → demo
5. Add US4 → the record contract pinned end to end
6. Polish → docs, quickstart, full suite

### Parallel Team Strategy

Phase 2 is a genuine bottleneck and is best done by one person in one pass — it is one migration and
one vocabulary, and splitting it invites two halves of a constraint set landing separately. After
that:

- Developer A: US1 (P1), then US4's record tests
- Developer B: US2 (P2) once US1's `classify_change_failure()` and converters are in, then US3

---

## Notes

- [P] tasks = different files, no dependencies
- Every implementation task names the exact file it lands in; every file it names already exists,
  except the new migration and the new test modules — this feature adds no module, package or layer
- The one rule whose violation is invisible in every single-threaded test is the staleness guard being
  a `WHERE` clause rather than a preceding check (research #5). T066's race test is the only thing
  that catches it — do not skip it, and do not "simplify" the write into a read-then-update
- `services/frontend/` is untouched. A change is a conversation: no new event field, no new route, no
  new component
- Commit after each task or logical group; stop at any checkpoint to validate a story independently

---

## Phase 8: Convergence

Findings from assessing the implementation against spec.md, plan.md and the constitution
after Phase 7. No constitution principle is violated and nothing is missing outright; what
remains is one requirement whose capability exists but is never reached, three places where
a value carries more meanings than it should, and one undocumented log event.

- [X] T093 Direct the assistant to widen the **time** axis whenever the patient asks about cancelled appointments, in `list_my_appointments`'s tool description and the system prompt in `services/chat/src/chat/agent/handle_booking.py` — a request for cancelled ones returns them from either side of now, since a cancellation is not something the patient is still waiting for. The filter defaults stay at the narrowest corner; only the assistant's instruction changes. Cover it in `services/chat/tests/test_handle_booking_changes.py` with a scripted turn that asks "what have I cancelled?" and dispatches `time_filter="both"`, and in `services/chat/tests/test_scheduling_tools.py` for the past leg carrying its cancelled entries per FR-015, SC-012 (partial)
- [X] T094 Give a change that provably never reached the scheduler its own explanation in `services/chat/src/chat/agent/tools/scheduling_tools.py` — "nothing was **changed**", not `_UNAVAILABLE_EXPLANATION`'s "Nothing was booked", which is the wrong verb for a cancellation or a move; assert it in `services/chat/tests/test_change_tools.py` alongside the existing `unavailable` case per spec Edge Cases ("the scheduling capability is unreachable before a change was sent"), FR-032 (partial)
- [X] T095 Split the two meanings `SchedulingRequestError` currently collapses in both change handlers in `services/chat/src/chat/agent/tools/scheduling_tools.py`: a request the scheduler rejected before acting, or a refusal whose reason this build cannot name, means nothing changed and that is **known** — report it as `unavailable`, matching how `book_appointment` already handles the same exception; reserve `unknown` for a response that carried no result at all. Add the two cases to `services/chat/tests/test_change_tools.py` per FR-023, plan.md "one value, one meaning" (partial)
- [X] T096 Scope `_load_change_context()` in `services/scheduler/src/scheduler/repositories/appointment_repository.py` to `session_id` and `patient_id` rather than reading by appointment id alone, so the guarantee lives in the query instead of resting on all three callers proving ownership first; pin it with a test in `services/scheduler/tests/test_cancel.py` per FR-018 ("every lookup MUST carry the session as part of the query rather than checking it after the fact") (partial)
- [X] T097 Remove `change.write_unconfirmed` from `services/chat/src/chat/agent/tools/scheduling_tools.py` — it records the same domain fact as the contract's `change.outcome_unknown` from a different layer, and its one extra field duplicates `scheduling.unavailable`'s — or, if it earns its place, document it in `specs/006-reschedule-and-cancel/contracts/log-events.md`, whose chat-side section currently states `change.outcome_unknown` is the only new chat-side event per contracts/log-events.md (unrequested)

---

## Phase 9: Convergence

The second convergence pass finds the opposite shape to the first. Nothing is missing and no
constitution principle is violated: every requirement is implemented and every success
criterion covered. What remains is that the implementation improved on three design
artifacts and never wrote the improvement back, plus one edge case that holds by
construction and is pinned by nothing.

- [X] T098 Reconcile plan.md's "Storage" migration steps with the order the migration actually uses in `services/scheduler/alembic/versions/b4c7e19d2a58_add_appointment_status.py` — the idempotency key's partial index is created **before** the two exclusion constraints are recreated, not after. The plan's order is the one that fails: PostgreSQL checks indexes in creation order and reports only the first violation, so the key index would sit behind the overlap rules and two identical concurrent booking attempts would be answered `patient_busy` instead of replaying the winner's appointment, breaking 005's idempotent-replay guarantee. Record that the order is load-bearing per plan.md "Storage", Constitution VI (contradicts)
- [X] T099 Record the two shipped design elements the Phase 1 artifacts do not describe: `TimeFilter`/`StatusFilter` as cross-cutting types in `data-model.md` (the repository signature and `list_my_appointments`'s schema `enum` both need them, so they could not stay wire-only), and `previous_practitioner_full_name = 6` on `ChangeAppointmentResponse` in `contracts/scheduling.proto` — without which `data-model.md`'s own `ChangeApplied.previous_practitioner_full_name` cannot be populated, and FR-027's "name both practitioners" has no source for the old one per data-model.md "Cross-cutting Python types", contracts/scheduling.proto (contradicts)
- [X] T100 Record in spec.md FR-011 that a **move** releases the booking key as a cancellation does — the key is derived from exactly (patient, practitioner, starts_at), so an appointment that has moved no longer sits where its key describes, and holding it makes availability offer a slot that every subsequent booking of is refused as a key-derivation defect, permanently. FR-011 already amends 005's FR-064; this is the same amendment carried one step further. Prefer `/speckit-specify` refinement over a silent edit, since this changes a requirement rather than restating one per spec.md FR-011 (contradicts)
- [X] T101 Pin the grandfathered-appointment edge case with tests in `services/scheduler/tests/test_cancel.py` and `services/scheduler/tests/test_reschedule.py`: an appointment left outside its practitioner's current schedule by a later edit can still be **cancelled**, and can still be **moved** — but only onto a time the practitioner's *current* schedule and grid allow, so its own out-of-schedule start is never re-validated while the new one always is. It holds by construction today (`cancel()` runs no placement validation; `reschedule()` validates only the new start) and nothing asserts it, so either path could lose it silently per spec Edge Cases ("a grandfathered appointment") (partial)
- [X] T102 Decide `AppointmentVanishedError` in `services/scheduler/src/scheduler/repositories/appointment_repository.py`: it was introduced during implementation, is raised on the cancel and reschedule paths when both parties vanished between the write and the read-back, and has no servicer handler and no test — it escapes to `LoggingInterceptor`'s generic clause and completes as gRPC `UNKNOWN`, which the chat client maps to an unknown outcome. That is a safe answer for a change that did commit, but an unasserted one. Either pin that behaviour with a test, or remove the type if the cascade makes it unreachable per spec Edge Cases ("the chat is deleted mid-turn") (unrequested)

---

## Phase 10: Convergence

One finding. Everything the previous two passes raised is closed, and the code satisfies
every requirement — but one clause of one success criterion is demonstrated by nothing, on
a property the whole feature's scoping discipline rests on.

- [X] T103 Assert the **cross-patient** half of SC-014, which nothing currently covers: a `cancel()` and a `reschedule()` naming an appointment that belongs to a *different patient in the same session* must be refused `appointment_not_found` and leave that appointment exactly as it was. Add the pair to `services/scheduler/tests/test_cancel.py` and `services/scheduler/tests/test_reschedule.py`, and one over the wire in `services/scheduler/tests/test_servicer_changes.py`. The predicate is already there and correct - `Appointment.patient_id == patient_id` appears seven times in `appointment_repository.py` - but the two on the change `UPDATE`s are exercised by no test: removing either would leave the whole suite green while a change landed on another patient's appointment, surfacing only as an `AppointmentVanishedError` from the separately-scoped read-back. Two chats in one session each hold their own patient (`test_a_second_chat_in_one_session_gets_its_own_patient_but_no_roster`), so this is a reachable configuration rather than a hypothetical one. The cross-*session* half is already covered at unit, servicer and integration level; this is the other axis per SC-014, FR-018 (partial)
