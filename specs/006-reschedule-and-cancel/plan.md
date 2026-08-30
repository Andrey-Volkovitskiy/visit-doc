# Implementation Plan: Rescheduling and Cancellation (Phase 1d, part 1)

**Branch**: `006-reschedule-and-cancel` | **Date**: 2026-08-29 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/006-reschedule-and-cancel/spec.md`

## Summary

One column, four constraints, two RPCs, two tools. That is the whole feature, and its size is the
point: 005 built the seam this rides on, so nothing here is a new service, a new database, a new
dependency, or a new node in the graph.

**(1) An appointment can now exist without counting.** `appointments` gains
`status ∈ {standing, cancelled}` (FR-009). Cancelling sets it rather than deleting the row, which
means every rule that used to read "an appointment exists" has to say which appointments it means.
Two of those rules are the exclusion constraints that make double-booking impossible, and they become
**partial** — `WHERE status = 'standing'` — so a cancelled appointment stops occupying its slot at
the datastore rather than by an application filter something could forget (FR-010, SC-011,
Constitution III). The idempotency key follows the same predicate: its UNIQUE constraint becomes a
partial unique **index**, so cancelling releases the key in the same statement that cancels the
appointment (FR-011). Four reads are audited into naming their statuses (FR-012); the FK cascades
deliberately are not, because deleting a patient or a practitioner must take their cancelled
appointments too (research #4).

**(2) A change is one conditional `UPDATE` whose `WHERE` clause is the guard.** `RescheduleAppointment`
and `CancelAppointment` each write in a single statement whose predicate carries the identity, the
session scope, the eligibility rules, and the staleness guard together — the spec's own resolution of
what "the server checks them before acting" has to mean. A preceding check leaves a window in which
two changes both pass and the second overwrites the first, and the datastore cannot catch the pairing
that matters most: a cancellation racing a move collides with no other appointment. The guard has two
arms — the state the patient was shown, **or** the state the request asks for — which is what makes a
re-sent change return its original outcome instead of a false conflict (FR-021, FR-023, SC-008,
SC-016). When the statement matches nothing, a classification read names which of the four new
refusal reasons applies; it decides nothing, so it cannot reintroduce the race it reports on.

Old and new values come back from that same statement, which joins a **locked** pre-image
(`WITH old AS (SELECT … FOR UPDATE) UPDATE … FROM old … RETURNING`), because FR-036 needs both sides
and reading the "before" separately would describe a state a concurrent change may already have
replaced. The lock is load-bearing rather than cautious: an unlocked self-join returns a stale
pre-image to the loser of two identical moves, which is SC-009's over-counted move (research #6).

**(3) The tool seam grows by two, and one existing tool changes shape.**
`reschedule_appointment` and `cancel_appointment` join the registry; `list_my_appointments` gains
FR-013's two axes and starts returning the appointment `id` and `status`; `check_availability` gains
`excluded_appointment_id` so an appointment does not block its own move on the offer path (FR-007 —
on the write path an exclusion constraint compares distinct rows, so it holds for free). The listing
returns FR-016's **two separately bounded legs** rather than one merged list, so twenty future
appointments can never consume a cap that exists because past ones accumulate without limit.

Tool results gain a `status: "unknown"`, distinct from `unavailable`. 005 had both meanings but
separated them only in prose; `unavailable`'s explanation says "Nothing was booked", and saying that
about a change whose answer never arrived is precisely the sentence FR-023 forbids (research #13).

**Out of scope** by spec: escalation and staff (FR-042 — Phase 1d part 2), any stored audit surface,
any notification, any lead-time policy, and any frontend change. `classify_intent` is untouched: a
message about changing an appointment is the existing booking intent.

## Technical Context

**Language/Version**: Python 3.12 across the workspace. No frontend work, so no TypeScript change.

**Primary Dependencies**: **none added, in any member.** `services/scheduler` and `services/chat` use
what they already resolve in the shared `uv.lock`; `shared-proto` regenerates from an edited
`.proto`; `shared-models` gains two enums and no import.

**Storage**: `visitdoc_scheduler` only, one migration:

- `ALTER TABLE appointments ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'standing'` with a
  `CHECK (status IN ('standing','cancelled'))` — existing rows are standing, which is exactly what
  they are;
- drop `appointments_idempotency_key_unique`; create the partial unique index
  `ix_appointments_idempotency_key_standing`;
- drop and recreate both exclusion constraints with `WHERE (status = 'standing')`;
- add `ix_appointments_patient_status_starts (patient_id, status, starts_at)` for the two legs.

**The order of the middle two is load-bearing, and not obviously so.** PostgreSQL checks a row
against its indexes in creation order and reports only the *first* violation, so recreating the
overlap constraints before swapping the key would leave the key index behind them. Two identical
concurrent booking attempts collide on the key *and* on both overlap rules, so the loser would then
be answered `patient_busy` — "you are already busy then" — instead of replaying the appointment the
winner just created for it, which is 005 FR-051's idempotent replay. The key index has to keep the
position its constraint held in the original table. Nothing single-threaded can tell the difference;
`test_two_concurrent_identical_attempts_yield_one_row_and_a_replay` is what catches it.

`visitdoc_chat` is untouched — there is no chat-side migration. The pending confirmation lives in the
conversation and nowhere else. Qdrant is untouched.

**Testing**: pytest, in the tiers 005 established; no new tier. The centre of gravity is the scheduler
unit tier, because the load-bearing rules here are datastore rules and are testable against a real
database before any service code exists (Constitution VIII): both partial exclusion constraints, the
partial unique index, the conditional `UPDATE`'s predicate, the twelve-reason precedence, the
status-blind cascade, and the two-leg listing. The chat tier fakes the scheduling stub at the client
module boundary as before, and adds the guard-passthrough assertion, the four tool-result shapes, and
the extended `BookingOutcome` derivation. `tests/integration/` gains the reschedule and cancel round
trips, the cancel-then-rebook sequence that proves the key was released, and the two concurrency
cases — two racing changes, and a change re-sent after it landed. Turn-exercising tests keep mocking
`AsyncAnthropic` with scripted tool-use responses, which is also how the confirmation rules
(FR-024–FR-030, SC-002/SC-003) are asserted: a model's behaviour is only testable against a script.

**Target Platform**: unchanged — chat `:8000`, scheduler `:8001` HTTP + `:50051` gRPC, Vite `:5173`,
Docker Compose Postgres/Qdrant.

**Project Type**: Web application, multi-service. This feature adds traffic to the existing
chat↔scheduler boundary rather than creating one.

**Performance Goals**: SC-015 inherits 005's budget verbatim — with the scheduler unresponsive the
patient gets a reply within 5 seconds, from FR-047's 2s deadline and at most 2 attempts. A healthy
change is one indexed `UPDATE` by primary key; the new listing is two indexed range scans, the second
bounded at 21 rows. Neither is on a path where latency is dominated by anything but the model calls
around it.

**Constraints**:
- **No timezone anywhere**, unchanged. `local_now` decides two more things now — whether an
  appointment has already started (FR-005) and which leg a listing entry falls in (FR-016) — and both
  read the client's clock, never the server's (FR-035).
- **Integrity in the datastore** (Constitution III): the two invariants this feature introduces —
  a cancelled appointment blocks nothing, a key outlives only a standing appointment — are both
  `WHERE` clauses on constraints, not application filters.
- **The guard is a write predicate, never a preceding check** (FR-021, research #5). This is the one
  rule whose violation is invisible in every single-threaded test.
- **A timeout never proves the server did nothing** (FR-023). A change whose answer was lost is
  reported as unknown; the words "nothing was changed" are not available on that path.
- **Every query carries its session predicate** (FR-018), including the change `UPDATE` itself.
- **No stored history surface.** FR-036–FR-041 are structured log entries; recording is best-effort
  and never gates a change (FR-041, research #14).

**Scale/Scope**: unchanged portfolio-demo scale. The one new bound is FR-016's 20-row cap on the past
leg, chosen for what a conversation can read back rather than as a clinic policy.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Phase-Gated Scope Discipline | PASS | ROADMAP Phase 1d's first bullet verbatim — "rescheduling and cancellation of an existing appointment, through the same tool seam and the same database-level guards that protect booking". Both halves of that sentence are honoured literally: no new seam (the 005 registry), no new guard mechanism (the same exclusion constraints, now partial). Escalation is the second bullet and is excluded by FR-042. No new service, database, dependency, or platform layer. **No deviations — Complexity Tracking is empty, and that is a result, not an omission.** |
| II. AI Core Is the Centerpiece | PASS | The agent gains its first *mutating* capability beyond create, and with it the first outcome the system must admit it does not know (FR-023). Everything added to the scheduler exists to give those tools something true to act on. |
| III. Deliberate, Minimal Service Boundaries | PASS | No new boundary. The existing one gains two RPCs and keeps its shape: evaluated refusals as typed data, transport failure as status codes, 2s/2-attempt budget. The two new integrity rules land **in the datastore** as partial constraints rather than in application code (research #2/#3), which is what the principle demands. Failure handling is designed, not deferred: the unknown-outcome path is a first-class result type, a first-class tool status, and a first-class log event. |
| IV. Structured Outputs & Decoupled Tool Interfaces | PASS | Both new capabilities are registry tools with closed schemas; the node imports no stub and no protobuf type. `session_id`/`patient_id`/`local_now` stay ambient. The one deliberate exception — the model supplies the guard fields — is argued and its alternatives recorded (research #12), because only the model knows what it read out to the patient. `classify_intent` is unchanged, so the cheap-model routing step is untouched. |
| V. Grounded Retrieval with Mandatory Abstention | PASS | The FAQ pipeline is not touched. `compose_answer` gains four `BookingOutcome` values to be constrained by, and the FAQ half's citations still travel structurally. Escalation-on-abstention remains part 2. |
| VI. Documentation as a First-Class Deliverable | PASS | research.md records 15 decisions with rationale and rejected alternatives; three contract deltas define the wire, tool and log surfaces against 005's originals rather than restating them. The change carries its own edits: README entries for the retained-cancellation model and the partial-constraint technique, the ROADMAP's 1d bullet split into the two parts this phase actually ships, and the note that 005's FR-064 key lifetime is amended by FR-011 (already recorded in spec.md's Dependencies). |
| VII. Clean Architecture, SOLID & Design Patterns | PASS | Nothing new is invented: repositories keep the session-as-parameter shape, the servicer keeps its thin mapping role, the chat client stays the only module importing `shared_proto`, and the placement rules for a changed appointment reuse `domain/availability.validate_start` — the same single implementation 005 shares between offering and booking, which is what makes FR-006 true by construction rather than by two implementations agreeing. The node keeps its name and its log events; the reasoning, and the cost of the alternative, is research #15. |
| VIII. Test-Driven Development (NON-NEGOTIABLE) | PASS (procedural gate) | The contracts fix the testable surface — two RPCs with three outcomes, twelve reasons in one precedence, four tool-result shapes, five log events, and data-model.md's enforcement table — for `/speckit-tasks` to sequence tests-before-implementation against. The datastore rules are testable first and matter most: a partial exclusion constraint that was written without its `WHERE` passes every single-threaded application test and fails SC-011 in production. |

**Post-Phase 1 re-check**: re-evaluated against data-model.md, the three contract deltas, and
quickstart.md. The design adds one migration, two RPCs, two tools, four Python types, and zero
dependencies. Nothing moved a status. The one judgement worth re-stating after design is Principle
IV's: the guard fields are model-supplied, which is a weakening of "the model can influence nothing
that matters" — accepted because the alternative shapes either disable the guard (handler re-reads
the current state, which always matches itself) or require a stored pending confirmation the spec
explicitly makes conversational.

## Project Structure

### Documentation (this feature)

```text
specs/006-reschedule-and-cancel/
├── plan.md                  # This file (/speckit-plan command output)
├── research.md              # Phase 0 output — 15 decisions
├── data-model.md            # Phase 1 output — one column, four constraints, the enforcement table
├── quickstart.md            # Phase 1 output — 7 scenarios, incl. the re-send and the race
├── contracts/               # Phase 1 output — all three are DELTAS against 005's contracts
│   ├── scheduling.proto     #   2 RPCs added, 1 replaced, Appointment + status, 12 reasons
│   ├── agent-tools.md       #   2 tools added, 2 modified, the new loop rules
│   └── log-events.md        #   5 change records, and where each is emitted
├── checklists/
│   └── requirements.md      # pre-existing, 16/16
└── tasks.md                 # Phase 2 output (/speckit-tasks — NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
packages/shared-models/
├── src/shared_models/scheduling.py          # MODIFIED: + AppointmentStatus, + ChangeFailureReason
│                                            #   (twelve; the eight reuse BookingFailureReason's
│                                            #   exact string values)
└── tests/                                   # + the member-by-member overlap pin (research #10)

packages/shared-proto/
├── protos/scheduling/v1/scheduling.proto    # MODIFIED per contracts/scheduling.proto
└── src/shared_proto/scheduling/v1/*_pb2*.py # REGENERATED (+ the README's manual import fixup)

services/scheduler/
├── alembic/versions/*_add_appointment_status.py   # NEW: the column, both partial EXCLUDEs, the
│                                                  #   partial unique index, the listing index
├── src/scheduler/
│   ├── domain/models.py                     # MODIFIED: Appointment.status; both ExcludeConstraints
│   │                                        #   gain where=; UniqueConstraint -> Index(unique=True,
│   │                                        #   postgresql_where=…); + the composite index
│   ├── domain/availability.py               # UNCHANGED — validate_start() is reused verbatim for a
│   │                                        #   changed appointment's placement rules (FR-006)
│   └── repositories/appointment_repository.py  # MODIFIED, the bulk of the service work:
│                                            #   + reschedule() and cancel(), each ONE conditional
│                                            #     UPDATE with the two-armed guard in its WHERE and
│                                            #     the old/new self-join in its RETURNING (#5, #6)
│                                            #   + classify_change_failure(), run only on rowcount 0,
│                                            #     resolving the four reasons in precedence (#10)
│                                            #   + list_for_patient(), two legs, LIMIT 21 on the past
│                                            #   ~ busy_intervals(): + status predicate,
│                                            #     + excluded_appointment_id (#7)
│                                            #   ~ get_by_idempotency_key(): + status predicate (#3)
│                                            #   ~ _REFUSAL_BY_CONSTRAINT: the key entry now names
│                                            #     the partial INDEX, not the dropped constraint
│                                            #   - list_upcoming(): replaced by list_for_patient()
├── src/scheduler/grpc/servicer.py           # MODIFIED: + RescheduleAppointment, + CancelAppointment,
│                                            #   ListUpcomingAppointments -> ListAppointments
├── src/scheduler/grpc/converters.py         # MODIFIED: AppointmentStatus both ways, ChangeFailure,
│                                            #   the two filter enums, the two-leg response
└── tests/                                   # NEW: test_appointment_status_constraints (partial
                                             #   EXCLUDEs + partial unique index, against a real DB),
                                             #   test_reschedule, test_cancel, test_change_precedence,
                                             #   test_list_appointments (the four corners + the cap),
                                             #   test_cascade_takes_cancelled;
                                             #   MODIFIED: test_availability (exclusion),
                                             #   test_idempotency (release + rebook)

services/chat/
├── src/chat/clients/scheduling.py           # MODIFIED: + reschedule_appointment(),
│                                            #   + cancel_appointment(), list_upcoming_appointments()
│                                            #   -> list_appointments(); + ChangeApplied / ChangeNoOp
│                                            #   / ChangeRefusal / AppointmentListing;
│                                            #   AppointmentInfo gains status. Still the ONLY module
│                                            #   importing shared_proto
├── src/chat/agent/tools/scheduling_tools.py # MODIFIED: + reschedule_appointment,
│                                            #   + cancel_appointment handlers; list_my_appointments
│                                            #   gains the two axes and returns id/status/two legs;
│                                            #   check_availability gains excluded_appointment_id;
│                                            #   + the explanation table for the twelve reasons;
│                                            #   _outcome_unknown() now returns status "unknown" (#13)
├── src/chat/agent/handle_booking.py         # MODIFIED: BookingOutcome + RESCHEDULED / CANCELLED /
│                                            #   UNCHANGED / OUTCOME_UNKNOWN; _outcome_from()'s
│                                            #   precedence extended; the system prompt gains the
│                                            #   confirmation, disclosure and refusal rules
│                                            #   (contracts/agent-tools.md). Name unchanged (#15)
├── src/chat/agent/compose_answer.py         # MODIFIED (small): constrained by the four new outcomes
│                                            #   so a merged reply cannot claim a change that did not
│                                            #   complete (FR-028, SC-007)
└── tests/                                   # NEW: test_change_tools (the four result shapes, guard
                                             #   passthrough, the reason->explanation table),
                                             #   test_handle_booking_changes (outcome precedence, the
                                             #   confirmation rules under scripted responses);
                                             #   MODIFIED: test_scheduling_client, test_compose_answer

services/frontend/                            # UNCHANGED. A change is a conversation; no new event
                                              #   field, no new route, no new component.

tests/integration/                            # + reschedule and cancel round trips, cancel-then-
                                              #   rebook (the released key), two racing changes, and
                                              #   a change re-sent after it landed
docs/ROADMAP.md, README.md                    # MODIFIED (see below)
```

**Structure Decision**: no new module, package, or layer. Every change lands in a file that already
exists for that purpose, which is the strongest evidence that 005's boundaries were drawn in the
right places — the one genuinely new behaviour, a conditional write with its guard in the predicate,
belongs to `appointment_repository` because that is where 005 already put the decision that overlap is
the datastore's to make. The chat side keeps its inversion intact: handlers depend on the client's
domain result types, the node depends on the handlers' results, and nothing above the client imports
a protobuf type.

**Documentation changes carried by this feature** (Constitution VI — same change, not follow-up):
`docs/ROADMAP.md`'s Phase 1d bullet, split to record that rescheduling and cancellation ship here and
escalation follows in part 2; README entries for the two choices a reader would otherwise have to
reverse-engineer from a migration — that cancellation retains the record rather than deleting it, and
that the consequences of retaining it are carried by **partial** constraints rather than by
application filters; and the note that 005's FR-064 key lifetime is amended by FR-011 (already in
spec.md's Dependencies, and now visible from the README's scheduling section). `docs/testing-strategy.md`
needs no change: no tier is added and no harness convention changes.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations. Every gate passed without a deviation, so this table is intentionally empty — unlike
005, which recorded two departures from the ROADMAP that this feature inherits rather than repeats.

The two judgement calls that came closest to needing an entry are recorded where they belong instead:
the model-supplied guard fields (research #12, and the Principle IV re-check above), and keeping the
`handle_booking` node name while it grows a second responsibility (research #15).
