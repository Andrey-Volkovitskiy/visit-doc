# Phase 0 Research: Rescheduling and Cancellation (Phase 1d, part 1)

**Feature**: `006-reschedule-and-cancel` | **Date**: 2026-08-29 | **Spec**: [spec.md](./spec.md)

Fifteen decisions. Every one of them is downstream of a single structural fact: spec 005 built a
store in which an appointment either exists or does not, and this feature introduces an appointment
that exists but no longer counts. Nine of the fifteen are consequences of that one change.

The spec's seventeen recorded clarifications are treated as settled input, not re-litigated here.
Where a decision below *implements* one, it names it.

---

## #1 — Cancellation is a status column, not a delete and not a timestamp

**Decision**: `appointments` gains `status VARCHAR(16) NOT NULL DEFAULT 'standing'` with a
`CHECK (status IN ('standing','cancelled'))`, backed by `shared_models.scheduling.AppointmentStatus`.
No `cancelled_at` column is added.

**Rationale**: FR-009 requires the record to survive with its identifier, practitioner, and times
intact, so a delete is out. Between an explicit status and a nullable `cancelled_at` whose NULL-ness
*implies* status, the explicit column wins on FR-012: every read must now **state** which statuses it
means, and `WHERE status = 'standing'` says that in the vocabulary of the rule. `WHERE cancelled_at
IS NULL` says it as a null test, and a reviewer checking that every read carries the predicate has to
translate each one back into the domain rule before deciding whether it is right.

`cancelled_at` is omitted because nothing reads it. The spec's own checklist flagged this as the one
question it deferred to this step, and the answer is that FR-037 already puts the moment of a
cancellation in the log, tied to its turn. A column no requirement consumes is a field that will be
populated, never read, and eventually trusted by something that should not have.

**Alternatives rejected**: *Soft-delete via `deleted_at`* — the same shape, but it names the row's
fate rather than the appointment's, and this appointment is not deleted; it is a cancellation the
patient may later ask about (FR-013). *A separate `cancelled_appointments` table* — moves the row on
cancellation, which loses the identifier's continuity (FR-009) and forces every "which appointment is
this?" lookup to read two tables.

---

## #2 — Both overlap exclusion constraints become partial

**Decision**: `appointments_patient_no_overlap` and `appointments_practitioner_no_overlap` are
dropped and recreated with `WHERE (status = 'standing')`.

**Rationale**: FR-010 says a cancelled appointment stops occupying its slot. The row keeps its
`tsrange`, so an unconditional exclusion constraint would go on rejecting any booking of that time —
SC-011 ("a cancelled slot is bookable again immediately") would fail at the datastore, and no amount
of application filtering could rescue it. A partial constraint is the whole fix, and it keeps
Constitution III's rule intact: the integrity invariant stays where the datastore can enforce it
against concurrent writers, rather than being demoted to a predicate some future read might forget.

This is also what makes FR-007 free on the write path. An exclusion constraint compares **distinct
rows**, so updating an appointment's `tsrange` is never checked against its own previous value: an
appointment cannot block its own move, including a move to the time it already holds, without a line
of code saying so. Only the *offer* path needs the exclusion spelled out — see #7.

**Alternatives rejected**: *Delete the row and keep an audit copy* — reintroduces #1's rejected
shapes. *Keep the constraint unconditional and null out `starts_at`/`ends_at` on cancellation* —
destroys exactly the facts FR-009 requires the record to keep, and `ends_at > starts_at` is
`NOT NULL`-checked.

---

## #3 — The idempotency key is released by a partial unique index

**Decision**: the `appointments_idempotency_key_unique` UNIQUE constraint is dropped and replaced by
a partial unique **index** on `(idempotency_key) WHERE status = 'standing'`.

**Rationale**: FR-011 says a booking key lives as long as the appointment it created *stands*, not as
long as its record exists. That sentence is a predicate, and this is the predicate. Cancelling the
appointment removes it from the index, so the key is free the instant the status changes — no
cleanup job, no second table, nothing to keep in step.

PostgreSQL has no partial UNIQUE *constraint*; a partial unique **index** is the only form that
takes a `WHERE`. That is a real difference and not a spelling: the column loses its
`UniqueConstraint` in `__table_args__` and gains an `Index(..., unique=True, postgresql_where=...)`,
and the insert-conflict handler in `appointment_repository` must match on the **index** name rather
than the constraint name it matches today.

The replay lookup changes with it: `get_by_idempotency_key` must filter `status = 'standing'`, or a
cancelled appointment would be replayed to a caller rebooking that slot — returning a cancelled
appointment as a fresh booking, which is the worst outcome available in this feature.

**Alternatives rejected**: *Set `idempotency_key = NULL` on cancellation* — the column is
`NOT NULL`, and making it nullable would destroy the record of which key created the appointment,
which is the one thing the column is for. *Delete the key into a `released_keys` table* — a second
store to keep consistent with the first, for a fact the first already knows.

---

## #4 — Every read states its statuses; the delete cascade deliberately does not

**Decision**: an audited list of every existing read gains an explicit status predicate. The FK
cascades are left exactly as they are.

**Rationale**: FR-012 is the requirement the spec's own checklist called "most likely to be
under-served", and it fails silently in the dangerous direction — an omitted filter resurrects a
cancelled appointment into an availability calculation or a patient's list. So the audit is
enumerated here rather than left to a reviewer's memory. Four reads block or reveal appointments:

| Read | Today | Becomes |
|---|---|---|
| `appointment_repository.busy_intervals` | every appointment of this practitioner or patient | `+ status = 'standing'`, `+ id != excluded_appointment_id` (#7) |
| `appointment_repository.get_by_idempotency_key` | any row holding the key | `+ status = 'standing'` (#3) |
| `appointment_repository.list_upcoming` | starts after `local_now` | replaced by the two-axis, two-leg read (#11) |
| both exclusion constraints | every row | `WHERE status = 'standing'` (#2) |

The **cascade is the deliberate exception**, and this is the trap: `ON DELETE CASCADE` from
`patients` and `practitioners` is not a read, and it must take cancelled appointments too. The spec
settles this in both directions — deleting a chat removes the patient's appointments "cancelled ones
included", and deleting a practitioner does the same from the other side. Scoping a cascade to
standing rows would strand cancelled appointments behind a party that no longer exists. Since the
cascade is a foreign key rather than a query, the correct behaviour is the one already in place; the
work here is a test that pins it, not a change.

**Alternatives rejected**: *A global SQLAlchemy filter or a `standing_appointments` view that hides
cancelled rows by default* — reads that must see cancelled rows (the patient's own listing) would
then have to opt out, and an opt-out is exactly the thing a reviewer cannot see missing.

---

## #5 — A change is one conditional `UPDATE`; its `WHERE` clause *is* the staleness guard

**Decision**: reschedule and cancel are each a single `UPDATE … WHERE` whose predicate carries the
identity, the session, the eligibility rules, and the guard together. Nothing is read first. When the
statement matches no row, a **classification read** runs — scoped to the session — for the sole
purpose of naming which of the four reasons to report.

```sql
UPDATE appointments SET starts_at = :new_start, ends_at = :new_end,
                        practitioner_id = :new_practitioner
 WHERE id = :appointment_id
   AND session_id = :session_id          -- FR-018: scope is a predicate, never a later check
   AND status = 'standing'               -- FR-005
   AND starts_at > :local_now            -- FR-005, against the client's clock
   AND (   (starts_at = :expected_start AND practitioner_id = :expected_practitioner)  -- described
        OR (starts_at = :new_start      AND practitioner_id = :new_practitioner) )     -- target
```

A **cancellation** is the same statement with `SET status = 'cancelled'` and no destination — which
is why its guard has only the first arm:

```sql
UPDATE appointments SET status = 'cancelled'
 WHERE id = :appointment_id
   AND session_id = :session_id
   AND status = 'standing'
   AND starts_at > :local_now
   AND starts_at = :expected_start AND practitioner_id = :expected_practitioner  -- described only
```

The second arm has nothing to say here: a cancellation asks for a status, and `status = 'standing'`
already excludes the row that is in the state being asked for. FR-021's "or the target state" is
therefore discharged for a cancellation by the **classification read** rather than by the predicate —
a re-sent cancellation matches nothing, classification finds the row cancelled, and `already_cancelled`
is reported as `no_change` rather than as a failure (#9). Same guarantee, different mechanism; the
two are worth keeping distinct, because reading the reschedule predicate as "the" shape of both is how
a cancel ends up with a target arm that can only ever match a row the `status` predicate already
rejected.

**Rationale**: this is the spec's own clarification made literal — "the match MUST be a predicate on
the write itself … rather than a check performed before it". The window a check-then-write leaves is
not theoretical: two changes to one appointment in the same session (a fast second message, a
superseded turn) can both pass a preceding check, and the pairing the datastore cannot catch — a
cancellation racing a move, which collides with no other appointment — would let the second silently
overwrite the first after its patient was told it succeeded.

The two-armed guard is the FR-021 clarification: the described state, **or** the target state. The
second arm is what makes a re-sent change safe (#13, FR-023) — a retry of a move that already landed
arrives quoting the old start, and without it would be refused as stale, reporting a conflict for a
change that succeeded (the false conflict SC-008 forbids).

The classification read is safe precisely because it decides nothing. It runs only after a statement
that wrote nothing, and its output is a *reason string*, never an outcome. It cannot reintroduce the
race it is reporting on.

**Alternatives rejected**: *`SELECT … FOR UPDATE` then write* — correct, and it is the spec's
rejected Option C: a row lock to hold open a window the `WHERE` clause can simply not have. *Optimistic
locking on a `version` column* — a revision counter refuses with "something changed", where the guard
on the described facts lets the assistant say *what* changed, in the patient's own terms (FR-022).

---

## #6 — Old and new values come out of the same statement

**Decision**: the update joins a **locked** pre-image, so one round trip yields both sides of the
change and both describe the same version of the row:

```sql
WITH old AS (
  SELECT id, starts_at, practitioner_id, idempotency_key, status FROM appointments
   WHERE id = :appointment_id AND session_id = :session_id AND patient_id = :patient_id
   FOR UPDATE
)
UPDATE appointments SET … FROM old
 WHERE appointments.id = old.id AND <the predicate from #5, read off `old`>
RETURNING old.starts_at AS old_start, old.practitioner_id AS old_practitioner
```

**Rationale**: FR-036 wants the start before and the start after; FR-038 wants the practitioner on
each side. `RETURNING` alone gives only the new row. Reading the old values first would put a second
statement inside the very window #5 exists to close, and — worse — would produce a log entry
describing a "before" state that a concurrent change may have replaced, which is a false record
rather than a missing one.

**`FOR UPDATE` is not decoration, and a plain `FROM appointments AS old` self-join is wrong here** —
this was the shape originally chosen, and it was measured failing. The `old` leg of such a join
carries no row mark, so when a concurrent commit forces the update to re-check its predicate
PostgreSQL re-reads the *target* row but keeps `old` from the original snapshot. Two identical moves
— the ordinary shape of the caller's own retry after `DEADLINE_EXCEEDED` (FR-023) — then both match
the guard's target arm, and the loser reads a stale pre-image: it reports a move it never performed
and writes a second `appointment.rescheduled` for one transition, which is exactly the over-counted
move SC-009 forbids. Reproduced 7 times in 8 before the lock was added, and 0 in 8 after.

The lock does not reintroduce #5's rejected `SELECT … FOR UPDATE` *then* write: there is no window,
because the read and the decision are one statement. What #5 rejects is a check that has already
returned by the time the write runs.

It also decides the no-transition case for free: a reschedule whose `old` and new values are equal
transitioned nothing and is recorded as such (#9, FR-040), and the comparison is made on values the
database returned rather than on what the caller believed.

**Alternatives rejected**: *A PostgreSQL trigger writing an audit row* — moves a log entry into the
datastore, which the spec's Assumptions explicitly exclude for this phase. *Two statements in one
transaction* — correct under `REPEATABLE READ`, but it buys nothing over the self-join and adds an
isolation level this codebase does not otherwise set.

---

## #7 — An appointment must not block its own change: free on the write path, explicit on the offer path

**Decision**: `CheckAvailabilityRequest` gains an optional `excluded_appointment_id`. When present,
`busy_intervals` omits that appointment from the practitioner's *and* the patient's commitments. The
write path gets no such parameter.

**Rationale**: FR-007 has two halves that need opposite treatment. On the **write** path the
exclusion constraints compare distinct rows, so an `UPDATE` is never checked against the row's own
previous interval — the rule holds by construction (#2). On the **offer** path, `busy_intervals` is
an ordinary query that would happily subtract the appointment being moved from its own list of
options, so 09:00 would be missing from the times offered for the 09:00 appointment, and a patient
could never move an appointment onto a time overlapping the one it currently occupies.

The parameter is deliberately an *appointment id*, not a boolean or a time range: the scheduler
scopes it to the session like every other id, so passing another session's appointment id excludes
nothing rather than revealing that it exists.

**Alternatives rejected**: *Subtract the appointment client-side after availability returns* — the
chat service would then be re-implementing a slot rule the scheduler owns, which is precisely the
duplication `domain/availability.py` was centralised to prevent in 005 (FR-025/SC-009). *Cancel first,
then offer times* — makes the two halves come apart, which is the outcome FR-002 was chosen to avoid.

---

## #8 — Two RPCs, neither carrying an idempotency key

**Decision**: `RescheduleAppointment` and `CancelAppointment`. Both carry the guard fields; neither
carries an idempotency key. A practitioner change is `RescheduleAppointment` with
`new_practitioner_id` set.

**Rationale**: FR-020 is explicit — a key exists to stop a *second row* coming into being, and
neither operation can create one. FR-019's target-state shape is what makes replay safe instead, and
a key derived from that target state would actively introduce the replay bug the spec rules out
(09:00 → 10:00 → 09:00 → 10:00 would derive the first move's key on the third).

Two RPCs rather than one `ChangeAppointment` with a mode flag: the two requests genuinely differ in
shape — a reschedule carries a destination, a cancellation has none — and a single message would
need `new_starts_at` to be meaningfully empty for one mode, which is the "one value, two meanings"
smell this codebase treats as a defect. A practitioner change stays inside `RescheduleAppointment`
because the spec settled it as one write on one appointment (FR-002/FR-003): practitioner, start and
end move together or not at all, and separating them would recreate the two-halves problem.

**Alternatives rejected**: *`UpdateAppointment` with a field mask* — a general mutation surface for a
domain with exactly two legal transitions. *Cancel-plus-book for a practitioner change* — the
original feature description, overridden by the spec's first clarification.

---

## #9 — Three outcomes on the wire, and `already_cancelled` is a refusal only for a reschedule

**Decision**: both change responses carry `oneof { Appointment appointment; NoChange no_change;
ChangeFailure failure; }`. A cancellation of an already-cancelled appointment returns `no_change`,
not `failure`.

**Rationale**: FR-019 says a repeated attempt reports the same outcome as the first; FR-017 says
"already cancelled" must be distinguishable from "not found" and must **not** be reported as a failed
cancellation; FR-040 says a request that completed without transitioning anything is its own record
kind. One outcome cannot carry all three meanings, so there are three:

| Situation | Reschedule | Cancel |
|---|---|---|
| the write moved the row | `appointment` | `appointment` (now cancelled) |
| already in the state asked for | `no_change` | `no_change` |
| standing, but cancelled is not a state a reschedule may target | `failure(ALREADY_CANCELLED)` | — |

The asymmetry is not an inconsistency: for a cancellation, "cancelled" *is* the target state, so
reaching it is success; for a reschedule it is an ineligibility (FR-005), because a cancelled
appointment cannot be reinstated by moving it. FR-006 lists `already_cancelled` among the four
reasons, and this is the one reason whose reachability depends on which change asked.

`no_change` carries the appointment as it stands, so the assistant can confirm in the same words it
would have used for a real change (FR-028 is not violated: the appointment *is* in that state).

**Alternatives rejected**: *Return `appointment` for a no-op and let the caller diff* — the caller
would have to reconstruct "did anything change?" from values it did not have before the call, which
is exactly the fact the server already knows (#6). *Report `already_cancelled` as a failure for both*
— contradicts FR-017 and would have the assistant tell a patient their cancellation failed when the
appointment is cancelled.

---

## #10 — `ChangeFailureReason` is its own enum of twelve, pinned to booking's eight by a test

**Decision**: a new `ChangeFailureReason` — in `shared_models` and mirrored on the proto — declaring
all twelve of FR-006's reasons. The eight inherited from booking reuse `BookingFailureReason`'s
**exact string values**, and a unit test asserts that overlap member by member.

**Rationale**: FR-006 fixes the change refusal set at twelve, in one precedence, and FR-065 fixes
booking's at eight and calls that set closed. Extending `BookingFailureReason` in place would break
005's closure — a booking could then report `stale_confirmation`, a reason no booking rule produces.
Handing the caller two enums to union is worse: every call site would branch on which enum it got
before it could look up an explanation.

So the set is declared once for changes, with the overlap made mechanical rather than remembered. The
test is the load-bearing part: it is what stops the two vocabularies drifting into a state where
`practitioner_busy` means one thing to a booking refusal and another to a change refusal, and it
lets the chat service key one explanation table by string value for both.

Precedence, evaluated in this order (the four first — each settles whether the appointment can be
changed at all, before any question of where it may go):

`appointment_not_found` → `already_cancelled` → `already_started` → `stale_confirmation` →
then booking's eight, unchanged: `practitioner_not_found`/`patient_not_found` → `in_past` →
`beyond_horizon` → `outside_schedule` → `off_grid` → `practitioner_busy`/`patient_busy`.

`already_started` is **not** booking's `in_past`. `in_past` is about the new start time being asked
for; `already_started` is about the appointment's *current* start, and an appointment that has begun
must be refused even when the time it is asked to move to is perfectly valid. Two situations, two
values.

**Alternatives rejected**: *One enum shared by both flows* (see above). *A `stale` boolean beside a
booking reason* — a second channel for a refusal that already has one, and FR-006 requires exactly one
reason per refusal.

---

## #11 — `ListUpcomingAppointments` becomes `ListAppointments`, two axes in, two legs out

**Decision**: the RPC is replaced by `ListAppointments`, taking `TimeFilter` and `StatusFilter`
enums and returning **two separately bounded lists**:

```proto
enum TimeFilter   { TIME_FILTER_FUTURE = 0;   TIME_FILTER_PAST = 1;      TIME_FILTER_BOTH = 2; }
enum StatusFilter { STATUS_FILTER_STANDING = 0; STATUS_FILTER_CANCELLED = 1; STATUS_FILTER_BOTH = 2; }

message ListAppointmentsResponse {
  repeated Appointment future = 1;  // ascending, unbounded (the 90-day horizon bounds it)
  repeated Appointment past = 2;    // descending, at most 20
  bool past_truncated = 3;
}
```

**Rationale**: FR-013 makes the axes independent and every combination answerable; FR-016 makes the
past and the future separate legs, bounded and ordered separately. Two repeated fields make that
structural — no caller can accidentally let twenty future appointments crowd out every past one,
because they never share a list to be crowded out of. `past_truncated` is what FR-016's "say that
part of the list is not complete" is read from; it is scoped to the past leg alone, so it cannot be
mistaken for the whole answer being partial.

**The zero values are the narrowest corner, deliberately.** proto3 gives every enum an unavoidable
default, and FR-014 says the unqualified question returns future *and* standing. Binding the zero
values to exactly that means a field left unset can only ever narrow, never widen — a caller that
forgets to set the filters gets the safe answer instead of a patient's cancelled history.

**Alternatives rejected**: *Keep `ListUpcomingAppointments` and add a second RPC for history* — two
RPCs whose union is one question, and FR-013's four combinations would be split across them by an
axis that is not the one the patient thinks in. *One list with a `status` field per item and a single
cap* — the shape the spec rejected in clarification, for the crowding-out reason above.

---

## #12 — The tool seam gains two tools; the guard fields are model-supplied

**Decision**: `reschedule_appointment` and `cancel_appointment` join the registry.
`list_my_appointments` gains the two axis parameters and starts returning the appointment `id` and
`status`. Both change tools require `expected_starts_at` and `expected_practitioner_id`, supplied by
the model, described as "the values you stated to the patient".

**Rationale**: the model must supply them because only the model knows what it read out. The handler
cannot derive them — re-reading the appointment would return its *current* state, which matches
itself by definition and disables the guard completely. The registry cannot cache them either: a
confirmation spans turns, and per-turn ambient state does not survive to the turn the patient says
yes in.

This is worth stating plainly rather than dressing up: **the guard protects a confirmation from the
appointment changing underneath it, not from a model that fabricates what it showed.** That is the
threat FR-021 names, and the one it defends. The residual exposure is bounded by everything else in
the loop — the model's own prior turn is in its context, and FR-025's read-back is asserted by tests
with scripted responses.

`list_my_appointments` must return `id` because a change has to name an appointment, and today the
tool returns none. FR-034's "no internal identifier" is a rule about what reaches the **patient**,
not about what the model may hold — the same distinction that already lets the model handle
practitioner ids. 005's loop rules already forbid mentioning an id, and the prompt rules extend that
to appointment ids.

`session_id`, `patient_id` and `local_now` stay ambient and unnameable, as in 005.

**Alternatives rejected**: *A server-issued opaque confirmation token minted when the appointment is
described* — genuinely stronger, and it is a stored pending-confirmation, which the spec's Key
Entities explicitly make conversational and not stored. *No guard at all, relying on the model to
re-read before acting* — a check the model performs on itself, which is not a check.

---

## #13 — A new tool-result status `unknown`, separate from `unavailable`

**Decision**: tool results gain `status: "unknown"`. `handle_booking`'s `BookingOutcome` gains
`OUTCOME_UNKNOWN`, ranked above `UNAVAILABLE` and below any completed change.

**Rationale**: FR-023 forbids reporting that nothing was changed when the answer never arrived. 005
already has both meanings but separates them only in prose — `_outcome_unknown()` returns
`status: "unavailable"` with a different `explanation`, so the two are one value to every consumer
that reads `status` and two values only to a reader of English. That was tolerable when the only
write was a booking whose derived key made a retry safe; it is not tolerable now, because
`unavailable`'s explanation says "Nothing was booked", and saying that about a change whose outcome
is unknown is the exact sentence FR-023 prohibits.

Promoting the distinction into `status` makes it visible to `_outcome_from`, to the composing step's
constraint, and to the tests — the three places that decide what the patient is told.

Ranking: a completed change outranks everything (a turn refused once and then successful is a
success); `unknown` outranks `unavailable` because "we cannot tell you whether your appointment
moved" is more important than "nothing happened"; `unavailable` outranks `refused` as in 005.

**Alternatives rejected**: *Keep one status and branch on the explanation string* — deriving control
flow from prose is what the outcome enum exists to avoid. *Treat unknown as a failure* — it would let
the assistant say nothing changed.

---

## #14 — Change records are emitted where the fact is known, and are best-effort

**Decision**: state transitions are logged by the **scheduler** (`appointment.rescheduled`,
`appointment.cancelled`, `appointment.unchanged`, `change.refused`); the unknown outcome is logged by
the **chat** service (`change.outcome_unknown`). All are structured log events, written after the
change, and a failure to write one never affects the change.

**Rationale**: FR-039 requires every record to carry the turn identifier, which both services already
have — the chat service binds `turn_id`, and 005 propagates it to the scheduler as `x-turn-id`
metadata and re-binds it there, so a scheduler-side record joins to the conversation on the same key.
Each record is then emitted by the process that actually observed the fact: the scheduler alone knows
the old and new values (#6), and the chat service alone knows that its call budget was exhausted —
the scheduler, by definition, never learns that its answer was lost.

Best-effort is the spec's own clarification and it prevents a specific over-build: SC-009's "100%
recoverable from the logs" could otherwise be read as demanding a record row in the change's
transaction or an outbox, which would add a durable audit store this phase has no other use for and a
new half-done state (change committed, record pending) that nothing consumes. FR-041 now says
recording follows the change rather than gating it.

**Alternatives rejected**: *Emit every record chat-side for symmetry* — the chat service would have
to be told the old values by the response purely so it could log them, which is a wire field existing
for a log line. *An `appointment_changes` table* — excluded by the spec's Assumptions.

---

## #15 — The node keeps its name, and `BookingOutcome` grows rather than being replaced

**Decision**: `handle_booking` stays `handle_booking`; its `booking.*` log events keep their names;
`BookingOutcome` gains `RESCHEDULED`, `CANCELLED`, `UNCHANGED`, `OUTCOME_UNKNOWN`.

**Rationale**: the spec settles the routing question — "a message about changing an appointment is
recognised by the existing booking intent rather than needing a new one" — so `classify_intent` is
untouched and the same specialist handles all three operations. Renaming the node and its five log
events to `handle_scheduling` would be a cosmetic change rippling through the graph, the log-event
contract, spec 004's and 005's log assertions, and every test that names a node, in exchange for a
better word. The node name is a published contract as of 005's `log-events.md`; the accurate move is
to document what it now covers, not to churn it.

The enum grows for the opposite reason: its members are *values with meanings*, and a change that
completed is not a booking that completed. Reusing `BOOKED` for a reschedule would make one value
stand for two situations in the exact place — the composing step's truth constraint — where that
confusion becomes a false statement to a patient.

**Alternatives rejected**: *Rename both now* — churn without behavioural gain, and Phase 1d part 2
adds escalation to the same node, which would make this the second rename in one phase. *A separate
`handle_change` node* — a second tool-use loop over the same tools and the same conversation, which
would then need its own routing decision for "move my appointment and book another", a message the
spec explicitly says is two decisions in one conversation, not two nodes.
