# Implementation Plan: What staff can see of the schedule (Phase 3a leftovers)

**Branch**: `worktree-appts-to-frontend` (feature dir `016-staff-booking-visibility`) | **Date**: 2026-09-24 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/016-staff-booking-visibility/spec.md`

## Summary

This feature replaces 015's two stubs with real data.

1. **A practitioner's week on the roster.**
   - Each practitioner block gets a Show/Hide bookings toggle. Opening it reads a new console
     endpoint, `GET /console/practitioners/{id}/appointments?local_now=…`.
   - That endpoint computes the seven-day window and forwards to a new scheduler **REST** read,
     `GET /practitioners/{id}/appointments?ends_after=&starts_before=`. The scheduler's single
     query carries session, practitioner, standing status and both bounds in its `WHERE`.
   - An open list re-reads on the console's existing 2-second poll tick.
2. **Booking acts on the patient message.**
   - A new chat table, `booking_acts`, holds one row per attempt to book, reschedule or cancel. The
     row is written **before** the scheduler is called and settled afterwards.
   - The tool registry enforces this for every tool with `writes=True`, through a `plan_act`
     declaration and a `BookingActRecorder` port on `ToolContext`.
   - `GET /chats/{id}/messages` returns the acts as `MessageOut.booking_acts`. The staff console's
     evidence marker renders them, and an unsettled or unknown act puts the marker into the
     needs-a-person state.
   - The console conversation list carries a `booking_acts_version` per conversation. The open staff
     thread re-reads when it changes, so a settle that no message accompanies still reaches the
     screen (research R8b).

The agent never reads the record.

## Technical Context

**Language/Version**: Python 3.12 (chat, scheduler, harness); TypeScript 7 / React 19 (frontend)

**Primary Dependencies**:
- FastAPI and Pydantic v2
- SQLAlchemy 2 async and Alembic
- aiohttp and yarl (the chat→scheduler REST transport)
- Vite, vitest and Testing Library

No new dependency.

**Storage**:
- PostgreSQL `visitdoc_chat`: new `booking_acts` table.
- PostgreSQL `visitdoc_scheduler`: one new index, no new table.

**Testing**:
- pytest (`--import-mode=importlib`) per member, with real test databases.
- vitest with jsdom for the SPA.
- `tests/integration` for one real chat↔scheduler read.

**Target Platform**: Local Linux (WSL2) dev stack. The browser is a Chromium-class desktop browser.

**Project Type**: Web application: two FastAPI services, one React SPA, a shared-packages monorepo.

**Performance Goals**:
- A booking appears in an open list in ≤ 5 s (SC-004).
- The write-ahead insert adds one local-DB round trip per write-tool call, negligible beside a gRPC
  round trip and a model call.

**Constraints**:
- There is no server clock. The window comes from the browser's `local_now`.
- No scheduler reads come from closed lists, and none come more often than the poll tick.
- There is no change to any model input (FR-021a) and none to the gRPC contract.

**Scale/Scope**:
- One open list is bounded by one practitioner's slots in seven days, on the order of 70.
- A message holds a handful of acts at most.
- Roughly 3 backend modules are touched per service, plus 3 frontend components.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|---|---|
| I. Phase-gated scope | **Pass.** These are exactly the two items `docs/ROADMAP.md` Phase 3a lists as leftovers. No new service, broker or platform layer. |
| II. AI core is the centerpiece | **Pass.** The booking-act record makes the agent's tool use auditable, and no model behaviour changes. The feature is small against the AI-core investment and does not displace it. |
| III. Deliberate, minimal service boundaries | **Pass.** The existing chat↔scheduler REST seam is reused. The session/practitioner/window predicate is enforced in the scheduler's datastore query. Failure handling is designed: 503/504 with read wording, retry by next tick, and write-ahead with the "unknown, never nothing" rule. |
| IV. Structured outputs and decoupled tools | **Pass.** Recording is a registry concern declared per tool (`plan_act`) behind a port, and handlers and the agent are unaware of it. |
| V. Grounded retrieval and abstention | **Pass / N/A.** `request_outcomes` is untouched (FR-014), and the retrieval pipeline is untouched. |
| VI. Documentation | **Pass, with planned deliverables:** a ROADMAP Phase 3a update, a README tradeoff entry, a CLAUDE.md key-decision entry, and the frontend hook table (FR-022, FR-024). |
| VII. Clean architecture | **Pass.** The recorder is a port with DB and discarding adapters, and the registry depends on the abstraction. The window rule is one pure function. See Complexity Tracking for the one new mechanism. |
| VIII. TDD | **Pass, and binding on tasks.** Each contract file maps to failing tests written first: the scheduler repository/API, the transport query, the console route, the recorder repository, the registry recording table, `MessageOut`, `OutcomeDisclosure`, `PractitionerAdmin`. |

**Post-design re-check**: still passing. The design added no service, no dependency, and no change
to model inputs.

## Project Structure

### Documentation (this feature)

```text
specs/016-staff-booking-visibility/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── practitioner-week.md
│   ├── booking-acts.md
│   └── console-ui.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
services/scheduler/
├── src/scheduler/api/practitioners.py              # + GET /practitioners/{id}/appointments
├── src/scheduler/repositories/appointment_repository.py  # + list_for_practitioner(session, session_id, practitioner_id, ends_after, starts_before)
├── src/scheduler/domain/schemas.py                 # + PractitionerAppointmentOut, PractitionerWeekOut
├── src/scheduler/domain/models.py                  # + ix_appointments_practitioner_status_starts
├── alembic/versions/<new>_practitioner_week_index.py
└── tests/ test_appointment_repository.py, test_practitioners_api.py, test_migrations.py (extended)

services/chat/
├── src/chat/clients/scheduler_rest.py              # forward(..., query=)
├── src/chat/api/console.py                         # + GET /console/practitioners/{id}/appointments; _proxy detail params
├── src/chat/domain/practitioner_week.py            # practitioner_week_bounds(local_now) — pure
├── src/chat/domain/schemas.py                      # shared LocalNow validator; BookingActOut; MessageOut.booking_acts
├── src/chat/domain/models.py                       # + BookingAct model
├── src/chat/repositories/booking_act_repository.py # begin / insert_not_sent / settle / list_for_chat (all session-scoped)
├── src/chat/agent/booking_acts.py                  # PlannedAct, Settlement, BookingActRecorder port, settlement_from(), Discarding + Database adapters
├── src/chat/agent/tools/registry.py                # Tool.plan_act; ToolContext.acts; recording in _run
├── src/chat/agent/tools/scheduling_tools.py        # plan_act for book/reschedule/cancel
├── src/chat/agent/handle_booking.py                # acts.learn_practitioners(roster) after _read_roster
├── src/chat/api/turn.py                            # build DatabaseBookingActRecorder in launch()
├── src/chat/api/chats.py                           # attach acts in get_chat_messages
├── src/chat/repositories/chat_repository.py        # list_conversations_for_console: + booking_acts_version
├── alembic/versions/<new>_booking_acts.py
└── tests/ new: test_practitioner_week_bounds.py, test_booking_act_repository.py,
               test_booking_acts.py, test_registry_booking_acts.py
           extended: test_practitioner_proxy.py, test_chats_api.py, test_turn_api.py,
               test_scheduling_tools.py, test_handle_booking.py, test_migrations.py

services/frontend/
├── src/lib/chatStream.ts                           # BookingAct type; Message.booking_acts
├── src/lib/consoleApi.ts                           # PractitionerAppointment; fetchPractitionerWeek
├── src/components/OutcomeDisclosure.tsx            # bookingActs prop, state rule, act lines; stub removed
├── src/components/MessageView.tsx                  # pass booking_acts through
├── src/lib/useThreadReads.ts                       # re-read on (lastMessageAt, bookingActsVersion)
├── src/components/StaffThread.tsx                  # pass bookingActsVersion from the poll row
├── src/components/PractitionerWeek.tsx             # new: one open list, tick-driven reads
├── src/components/PractitionerAdmin.tsx            # toggle per block; stub removed from edit view; pollTick prop
├── src/App.tsx                                     # pass poll.tick to PractitionerAdmin
├── .claude/CLAUDE.md                               # hook table
└── tests/ OutcomeDisclosure.test.tsx, MessageView.test.tsx, PractitionerAdmin.test.tsx,
           PractitionerWeek.test.tsx (new), consoleApi.test.ts, chatStream.test.ts

tests/integration/test_practitioner_week_roundtrip.py
docs/ROADMAP.md, README.md, .claude/CLAUDE.md
```

**Structure Decision**: The existing monorepo layout is used. Every change lands in a module that
already owns that concern. There are two new modules on the chat side:
- `agent/booking_acts.py`, the port and its adapters, kept beside `agent/escalation.py`, which is
  its closest analogue: an ambient per-turn collector;
- `repositories/booking_act_repository.py`, one function per statement with `AsyncSession` as a
  parameter, per the repository convention.

There is one new component on the frontend: `PractitionerWeek`.

## Implementation order (for tasks)

This is the build order by layer. `tasks.md` sequences the same work by story priority: US1 (booking
acts) is the MVP, even though it is listed second here. The two stories share no code, so either
order holds.

1. **Scheduler read.** Repository function, then API route, then index migration. Tests first.
2. **Chat transport and console route.** `forward(query=)`, then `practitioner_week_bounds`, then
   the route, then the integration round trip.
3. **Frontend week.** `fetchPractitionerWeek`, then `PractitionerWeek`, then the toggle in
   `PractitionerAdmin`, then removing the edit-view stub and wiring `pollTick`. *Story 2 ships
   independently of what follows.*
4. **Booking-act storage.** Model and migration, then the repository (scoped update with
   `outcome IS NULL`).
5. **Recording.** The port and adapters, then `settlement_from`, then `Tool.plan_act` and the
   registry `_run`, then the three `plan_act`s, then the roster hook, then wiring in `turn.launch`.
   Every row of the recording table becomes a test.
6. **Wire.** `BookingActOut`, then `MessageOut.booking_acts`, then attaching acts in
   `get_chat_messages`, then a check that the harness thread parse and committed-run re-score still
   pass.
7. **Frontend acts.** Types, then `OutcomeDisclosure` (the state rule, act lines, stub removed),
   then `MessageView`.
8. **Docs.** ROADMAP 3a, README tradeoff, CLAUDE.md key decision, the frontend hook table.

## Complexity Tracking

| Item | Why needed | Simpler alternative rejected because |
|---|---|---|
| **A new mechanism: write-ahead booking-act rows, settled once.** Per the "a new mechanism is the risky fix" rule, its invariants are written down here, each with a test. | FR-012a and SC-001: no act may be missing from the record, including acts from turns that fail, are cancelled, or are taken over. | *Recording after the act* (clarification Q4) leaves a failed record write indistinguishable from "nothing attempted". *A JSONB array on `messages`* races under the booking loop's concurrent `gather`. |
| **Retiring two test hooks** against the frontend's "hooks may not be removed" rule | Both hooks name an absence (`*-stub`) that this feature removes. A hook kept alive would have to point at nothing. | *Keeping them on the new elements* would name live data after a stub. That is a hook that describes appearance-at-a-time rather than what the element is. |

**Invariants of the new mechanism** (each is a test):

1. **Row before request.** For every call that reaches the handler of a `writes=True` tool, a row
   exists before the handler starts. Tested with a handler that asserts the row's presence.
2. **No row, no request.** If `begin` fails, the handler is never called and the result is
   `unavailable`.
3. **Settled at most once.** A second settle on the same row changes nothing, because the `WHERE`
   carries `outcome IS NULL`.
4. **Unsettled reads as unknown.** A `null` outcome renders identically to `unknown` and makes the
   marker needs-a-person.
5. **Scoped.** No read or update on `booking_acts` omits `session_id`. A session-B id resolves to
   nothing.
6. **Invalid arguments leave no trace.** A `ToolArgumentError` from `plan_act` writes no row.
7. **Agent-blind.** The port has no read method. A test asserts that `BookingActRecorder` exposes
   only `learn_practitioners`, `name_of`, `begin`, `record_not_sent` and `settle`, and that no
   prompt builder imports `booking_acts`.
8. **The version moves exactly with the record.** `booking_acts_version` increases on every
   insert and settle of a chat's acts, and on nothing else. Tested by T042 against the listing.
