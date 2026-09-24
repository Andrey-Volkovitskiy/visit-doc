# Research: What staff can see of the schedule

Every decision below was taken against the code at `d21a9ff` (015 shipped). File references are
to that tree.

## R1 — The practitioner week is a scheduler **REST** read, not a gRPC one

**Decision**: Add `GET /practitioners/{practitioner_id}/appointments?ends_after=…&starts_before=…`
to the scheduler's REST admin surface (`services/scheduler/src/scheduler/api/practitioners.py`).
The console reaches it through a new `GET /console/practitioners/{practitioner_id}/appointments`,
over the same `scheduler_rest.forward` transport every other console-to-scheduler call uses.

**Rationale**:
- **`ListAppointments` cannot answer the question.**
  - It is scoped by session and **patient**, and never by practitioner
    (`appointment_repository.py:253-309`).
  - Its future leg has no upper bound; only the 90-day booking horizon limits it.
  - Using it would mean either listing every patient's appointments and filtering them, or
    changing an RPC the agent depends on.
  - FR-002 forbids the first. The second would widen the agent's contract to serve a staff screen.
- **The console already speaks REST to the scheduler.** The practitioner CRUD, `/specialties`, the
  `X-Session-Id` scoping and the 401/503/504 mapping are all REST (`api/console.py:340-457`). A
  read of "this practitioner's appointments" sits naturally under `/practitioners/{id}` beside the
  practitioner itself.
- **gRPC is the agent's channel.** Putting a staff read there would put two unrelated consumers
  behind one contract.
- **It is still one query with every predicate in its `WHERE`** (R2), which is what the roadmap's
  "over that RPC" line was actually asking for. The roadmap text is updated to say REST (FR-024).

**Alternatives considered**:
- *Extend `ListAppointments` with an optional practitioner and an upper bound.* This makes
  `patient_id` optional on a call whose NOT_FOUND semantics are defined by the patient. A missing
  patient would then mean two things, which violates the "one value, one meaning" principle.
- *A new `ListPractitionerAppointments` RPC.* Viable, but it needs proto changes, regenerated stubs
  with their manual import fixup, a servicer, converters and a new chat client function. The
  result is a read that no agent code uses, on a transport chosen for the agent's synchronous
  needs.

## R2 — The window is computed in chat and sent to the scheduler as two bounds

**Decision**:
- The browser sends `local_now`, the same offset-free local datetime `POST /chat` sends, as a query
  parameter.
- The chat route validates it with the same rule as `ChatRequest._reject_timezone_aware`, lifted
  into one shared validator. It then computes:
  - `ends_after = local_now`
  - `starts_before = datetime.combine(local_now.date() + timedelta(days=7), time.min)`
- The scheduler takes both bounds as given. Its query is:
  `session_id = :s AND practitioner_id = :p AND status = 'standing' AND ends_at > :ends_after AND starts_at < :starts_before ORDER BY starts_at`.
- The practitioner is first resolved with `practitioner_repository.get(session, id, session_id)`.
  A miss is a 404, exactly as `PATCH`/`DELETE` do.

**Rationale**:
- The scheduler has no clock and stays window-agnostic, answering for a range. It does not need to
  know that "a week" is the product's notion.
- The seven-day rule lives in one pure chat-side function, `practitioner_week_bounds(local_now)`,
  which is unit-testable with no stack. Midnight crossing (spec Edge Cases) falls out of it,
  because the next read carries the new `local_now`.
- `ends_at > now` keeps an appointment that is under way (spec clarification). `starts_at < end of
  day 7` includes all of the seventh day.

**Index**: add `ix_appointments_practitioner_status_starts (practitioner_id, status, starts_at)`,
mirroring the patient index that serves `ListAppointments`. It is not needed for correctness, but
it keeps the new read on an index rather than on a practitioner scan plus filter.

**Alternatives considered**:
- *Browser sends both bounds.* This puts the seven-day rule in the SPA and lets any caller ask for
  any range through the console. The console endpoint should expose the product's read, not a
  general range query.
- *Server clock.* Chat has none, and the app has no timezone (Phase 1c). One server-side "now"
  would be the first in the booking domain.

## R3 — `scheduler_rest.forward` gains a query argument; the path guard is untouched

**Decision**: `forward(..., query: Mapping[str, str] | None = None)`. The query is attached with
`yarl.URL.with_query`, which encodes it, after `_reject_anything_that_is_not_a_path` has checked
the path. The allow-list guard keeps refusing `?` in the path itself.

**Rationale**: The guard exists so that a caller cannot reshape the request by interpolating into
the path. A query built by `yarl` from a mapping cannot do that, and the route never interpolates
into the query string.

## R4 — A read gets read wording for its failures

**Decision**:
- The console route does not reuse `_proxy`'s 503/504 details, which describe a **change**
  ("nothing was changed", "the change may not have been applied").
- `_proxy` is given the two detail strings as parameters, defaulting to today's write wording.
- The appointments route passes read wording: "scheduling is unavailable; the appointments could
  not be read", and "scheduling did not answer; the appointments could not be read".
- There is no server-side retry. The panel's next poll tick is the retry (R8), which FR-006 permits
  for a read.

**Rationale**: A read that times out changed nothing. Telling staff "the change may not have been
applied" about a list would be a false statement.

## R5 — Booking acts are rows in their own table, surfaced as a field on `MessageOut`

**Decision**: A new chat table, `booking_acts`, with one row per attempt:
- keyed by `message_id` (FK to `messages`, `ON DELETE CASCADE`), which is the patient message the
  turn answered;
- carrying `session_id` and `chat_id` so that every query is scoped without a join;
- ordered by a `seq` identity column.

`GET /chats/{chat_id}/messages` loads the chat's acts in one scoped query and attaches them to
their message as `booking_acts: list[BookingActOut] | None`. The field is `None` when the message
has none, and never `[]` (FR-014).

**Rationale**:
- **Write-ahead needs an insert and then an update by id** (FR-012a). A JSONB array on `messages`
  would need a read-modify-write of the array for every settle. The booking loop dispatches a
  model response's tool calls **concurrently** (`handle_booking.py:689`, `asyncio.gather`), so two
  settles racing on one array is a lost-update bug waiting to happen. Rows have no such race.
- **Snapshot semantics (FR-015) come for free.** A row is never touched again after it is settled,
  and nothing joins it back to scheduler data.
- **On the wire it is still "its own shape on the message"**, beside `request_outcomes` and not
  inside it, which is what FR-014 asks for. The table is a storage detail.
- **Cascades follow the message.** Deleting a chat, or deleting a session through `/admin`,
  removes its acts.

**Alternatives considered**:
- *A `messages.booking_acts` JSONB column.* Rejected for the concurrency reason above.
- *A sixth `FaqVerdict` value or a `request_outcomes` entry.* Forbidden by the spec (FR-014),
  and by 011's meaning of that column.

## R6 — Recording is enforced in the tool registry, declared once by each write tool

**Decision**:
- `Tool` gains `plan_act: Callable[[ToolContext, dict], PlannedAct] | None`, set for the three
  tools with `writes=True`. It reads the arguments with the **same** helpers the handler uses
  (`required_id_argument`, `_required_datetime`, …). An argument error raises
  `ToolArgumentError` before anything is recorded (FR-010, clarification Q3).
- `ToolContext` gains `acts: BookingActRecorder`, a port. It defaults to a recorder that discards
  everything, the same pattern `escalation` uses, so every existing test context keeps working.
- `ToolRegistry._run` does the following for a tool with `plan_act`:
  1. `planned = tool.plan_act(context, arguments)`. A `ToolArgumentError` propagates exactly as
     today, and nothing is recorded. The one exception is a chat with no patient record,
     where it is answered `_NO_PATIENT_RESULT`, again as today. Converge found the first
     draft of this order changing that answer, which FR-021a forbids (T046).
  2. **No patient:** `await context.acts.record_not_sent(planned)`, then return
     `_NO_PATIENT_RESULT` (clarification Q3). If that record fails, log
     `booking_act.record_failed` and still return. Nothing reached the scheduler, so no act can be
     missing.
  3. `handle = await context.acts.begin(planned)`. If it fails, log `booking_act.record_failed` and
     return `{"status": "unavailable", …}` **without calling the handler** (FR-012a). The booking
     node's existing `_FAILED_STATUSES` handling then records `assistant_failed`, which is the
     spec's "calls a person" edge case with no new code.
  4. `result = await tool.handler(context, arguments)`. If the handler raises, the exception
     propagates as today, and the row stays unsettled. That renders as unknown (FR-012b), which is
     correct, because `_dispatch` already reports that write as `unknown`.
  5. `await context.acts.settle(handle, settlement_from(result))`. If the settle fails, log
     `booking_act.settle_failed` and return the result unchanged. The row stays unsettled and
     reads as unknown. That is conservative and never false.

`settlement_from` maps the tool statuses to outcomes:

| Tool status | Outcome |
|---|---|
| `booked`, `changed` | `done` |
| `unchanged` | `unchanged` |
| `refused` | `refused`, with its reason |
| `unavailable` | `not_sent` |
| `unknown` | `unknown` |
| anything else | `unknown` |

Names in the result (`practitioner_full_name`, `previous_practitioner_full_name`) overwrite the
planned ones, because the scheduler's are authoritative at the moment of the act.

**Rationale**:
- It is the same shape as `requires_patient`: a write tool declares what it would do, and the
  registry enforces recording in one place. A fourth write tool cannot forget to record, and none
  of the three handlers' bodies changes.
- **Cancellation needs no code.** A `CancelledError` while awaiting the gRPC call skips step 5,
  leaving the row unsettled and therefore unknown. That is FR-012's "the turn ends … before its
  answer was recorded".
- `plan_act` parsing twice (once there, once in the handler) is deterministic over the same dict,
  so the two reads cannot disagree. That is cheaper than changing every handler's signature.

**Alternatives considered**:
- *Recording inside each handler.* That is three copies of begin/settle, and a fourth tool that
  forgets them silently breaks SC-001.
- *Recording in `handle_booking._dispatch`.* The dispatcher sees the result but not a validated
  target, and it runs above the `requires_patient` short-circuit, so it cannot tell "not sent for
  want of a patient" from any other `unavailable`.

## R7 — Practitioner names come from the turn's roster, then from the scheduler's answer

**Decision**:
- The arguments carry only ids and times: `practitioner_id` and `starts_at` for a booking, and the
  `expected_*` guard values plus `new_*` for a change.
- The booking node already reads the roster at the start of every turn (`_read_roster`). It hands
  that roster to the recorder (`acts.learn_practitioners(roster)`), so `plan_act` can resolve a
  name at plan time.
- An id not in the roster, or a roster that could not be read, leaves the name `NULL`. The console
  then renders "a practitioner not named in the record".
- On settle, any name the scheduler returned overwrites the planned one.

**Rationale**:
- A refused or unknown act has no result to take a name from. The roster is the one place in the
  turn that already holds names, and reading it again per act would add a scheduler call to every
  write.
- `NULL` rather than an id or a guess means exactly one thing: the name was not known when the act
  ran.

## R8 — An open booking list refreshes on the console poll's tick

**Decision**:
- `App` passes `poll.tick` into `PractitionerAdmin`. Each open `PractitionerWeek` re-reads when the
  tick advances.
- A read is skipped while the previous one is still in flight, so there is at most one in-flight
  read per open list.
- Answers carry a sequence number, and a late answer is dropped (the `useConsolePoll` pattern).
- **Show bookings** always starts a fresh read (FR-007b). A closed list reads nothing.

**Rationale**:
- The tick is already the console's rhythm (2s), so SC-004's 5s holds with margin, and the spec's
  bound of "no more often than that rhythm" is met by construction.
- *Triggering only when some conversation's `last_message_at` moves* was considered. A booking
  always coincides with a message write, except for a turn that booked and then failed before
  replying, and that is exactly the case where staff most need the list current. Plain ticking has
  no such hole.

## R8b — A settled act re-reads the open staff thread through a version on the listing

**Decision**:
- `ConsoleConversationOut` gains `booking_acts_version: int`. It is computed in
  `list_conversations_for_console` as `count(booking_acts) + count(booking_acts.settled_at)` for
  the chat, scoped by session, and is 0 for a chat with no acts.
- `useThreadReads` takes it as a second input beside `lastMessageAt`. A re-read is due when the
  **pair** differs from the last pair it handled. `StaffThread` and `App` pass it through from the
  poll row.

**Rationale**:
- Rows are only ever inserted and settled once (data-model.md), and they are deleted only with
  their chat. So the sum strictly increases on every change and on nothing else. It is a version,
  not a timestamp, following `messages.seq`'s precedent of never ordering by a clock.
- It rides the poll the console already runs every 2 seconds. That adds one aggregate to a query
  already grouped per chat, and no new request.
- It is a separate field, not folded into `last_message_at`. That field means "newest message",
  and the staff list shows and orders by it, so bending it to mean "something changed" would give
  one value two meanings.

**Alternatives considered**:
- *`max(settled_at)`*: a clock, so two settles in one microsecond look like one. It also needs
  `created_at` to catch inserts, which makes it two clocks combined into one token.
- *A push channel (SSE or websocket) to the console*: new infrastructure against the "thinnest
  backend" principle, for a signal the existing poll already carries.
- *Accept the staleness* (option A): rejected by the user. The stale case is exactly the failed
  turn a person is paged for.

## R9 — What does **not** change

- **Agent context (FR-021a):** no prompt, history window or tool result gains booking acts. The
  recorder is write-only from the agent's side, with no read method on the port.
- **`turn.completed` and the trace.** `turn.completed` already carries `booking_outcome`, and every
  tool call is already an observation carrying its result (014), which is the same information.
  Two new log events cover the recorder's own failures (`booking_act.record_failed`,
  `booking_act.settle_failed`). They are not duplicated into the trace, because the tool
  observation already shows the result the settle would have stored.
- **The golden harness.** It parses the thread with chat's own `ChatHistoryResponse`
  (`driver/turn.py:207`), so the new optional field is accepted automatically. It then maps to its
  own `StoredMessage` explicitly (`_stored`), which is untouched, so stored `run.json` files keep
  their shape and every committed run re-scores unchanged (FR-023, SC-006).
- **The patient pane.** It reads the same `GET /chats/{id}/messages` and so receives
  `booking_acts`, but it renders none of it. `request_outcomes`' citations set that precedent:
  staff-only data on the shared thread read, rendered only by the console (FR-021).
