# Quickstart: Rescheduling and Cancellation (Phase 1d, part 1)

**Feature**: `006-reschedule-and-cancel` | **Date**: 2026-08-29 | **Plan**: [plan.md](./plan.md)

Validates the four user stories end to end, plus the two failure modes that are easy to get wrong and
invisible in a happy path: a change re-sent after it already landed, and two changes racing for one
appointment.

Everything 005's quickstart set up still applies. This feature adds no service, no database, no
environment variable, and no frontend surface — a change is a conversation, so most of what follows is
typed into the chat window.

---

## Prerequisites

```bash
make db-up
uv sync
make run-scheduler-dev    # alembic upgrade head runs the new migration
make run-chat-dev
make run-frontend-dev
```

Confirm the migration landed — this is the whole schema change, and every requirement below depends
on the two `WHERE` clauses:

```bash
docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_scheduler -c "\d appointments"
```

**Expected**: a `status` column defaulting to `'standing'`; both exclusion constraints printed with
`WHERE (status::text = 'standing'::text)`; a partial unique **index**
`ix_appointments_idempotency_key_standing`; and **no** `appointments_idempotency_key_unique`
constraint. If the exclusion constraints have no `WHERE`, a cancelled slot will not become bookable
and Scenario 1 fails at its last step.

---

## Scenario 1 — Cancel an appointment (US1, P1)

1. Book an appointment as in 005's Scenario 1, then in the same chat: **"Actually, cancel that."**
2. The assistant states the start date-time, the practitioner's full name and their specialty, and
   asks you to confirm (FR-025). **Nothing is cancelled yet.**
3. Say **"yes"**.

```bash
docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_scheduler -c \
  "SELECT id, status, starts_at, idempotency_key FROM appointments;"
```

**Expected**: the row is still there, `status = 'cancelled'`, with the same id, practitioner and
times it always had (FR-009).

Then, still in the chat:

- **"What have I got booked?"** → nothing. Not "you have a cancelled appointment" (FR-014).
- **"What have I cancelled?"** → the appointment, described and identified as cancelled (FR-015).
- Ask for that practitioner's availability covering the freed hour → **the slot is offered again**
  (FR-010, SC-011). This is the check that fails if the exclusion constraints kept their old form.
- Book that exact slot again and confirm → it succeeds, and the query above now shows **two rows**:
  the cancelled one and a new one with a **new id** (FR-011). The released key is what allows this.

**Negative checks** (each must leave `status` unchanged):
- Ask to cancel and then say **"no"** or change the subject → the assistant says the appointment still
  stands, and nothing changes (FR-029).
- Ask to cancel with three appointments booked → all three are listed by start, practitioner and
  specialty, and you are asked which. It never picks one (FR-030).
- Ask to cancel with nothing booked → "you have nothing booked", with no list to choose from (FR-031).
- Ask to **un-cancel** → refused; the assistant offers to book the time again instead, and only if it
  is still free (spec Edge Cases).

---

## Scenario 2 — Move an appointment (US2, P2)

1. Book at 09:00, then: **"Can you move that to 10:00?"**
2. The assistant offers only times that practitioner genuinely has free, and the read-back states
   **both** the current start and the proposed one (FR-026).
3. Confirm.

```bash
docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_scheduler -c \
  "SELECT id, status, starts_at, ends_at FROM appointments WHERE status = 'standing';"
```

**Expected**: exactly **one** row, with the id it already had, now at 10:00 (FR-001, SC-006).

- Ask for that practitioner's availability again → 09:00 is free, 10:00 is not.
- **The self-blocking check** (FR-007): ask to move it again and watch the offered times — **10:00 is
  among them**, because the appointment being moved does not count as blocking its own slot. Confirm
  a move to 10:00: the result is reported as done, and the log shows `appointment.unchanged`, not a
  second `appointment.rescheduled` (FR-040).
- Book a second appointment with a *different* practitioner at 14:00, then ask to move the first one
  to 14:00 → neither offered nor accepted; you cannot be in two places at once (FR-006).

---

## Scenario 3 — See a different practitioner (US3, P3)

With one appointment booked with the seeded GP:

1. **"I'd rather see a dentist."** → the session's dentists and their free times are offered.
2. Pick one. The read-back names **both** practitioners with **both** specialties, and makes clear
   this is the same appointment changing rather than a second one (FR-027).
3. **If the dentist's appointment length differs from the GP's, the confirmation must say so** —
   "it will be an hour rather than fifteen minutes" (FR-025). This is the check most likely to be
   missed; a swap that silently quadruples the visit is the failure it exists to prevent.
4. Confirm.

**Expected**: one standing row, same id, new `practitioner_id`, and `ends_at - starts_at` equal to the
**new** practitioner's duration (FR-004). The old practitioner's slot is offered again.

**Negative check**: ask for a specialty nobody in the session has → the assistant says so, names the
specialties that do exist, and leaves the appointment alone (FR-032's "no alternative to invent").

---

## Scenario 4 — The change records (US4, P4)

Perform one cancellation, one move, and one practitioner swap, then read the scheduler's output:

```bash
# in the scheduler's shell, or through your log pipeline
grep -E 'appointment\.(rescheduled|cancelled|unchanged)|change\.(refused|key_released)' <log>
```

**Expected**, per [contracts/log-events.md](./contracts/log-events.md):

- one `appointment.rescheduled` carrying `old_starts_at` **and** `new_starts_at`;
- one `appointment.cancelled` carrying `old_starts_at` and **no new-start field at all** — not an
  empty one (FR-037);
- the swap's record carrying `old_practitioner_id` and `new_practitioner_id`, which is what stops a
  same-time swap reading as a change that did nothing (FR-038);
- every one of them carrying the same `turn_id` as the chat-side lines for that turn (FR-039).

**Negative checks**: decline a confirmation, and force a refusal (ask to move an appointment to a
Sunday) → **no** `appointment.*` record exists for either; the refusal appears as `change.refused`
with its single reason (FR-040, SC-010).

---

## Scenario 5 — The listing grid (FR-013–FR-016)

Arrange one appointment in each corner: future standing, future cancelled, past standing, past
cancelled. (The past two are easiest to make by inserting directly, or by moving the client's
`local_now` forward — the client supplies it, so the browser's clock decides.)

| Ask | Expect |
|---|---|
| "What do I have booked?" | future standing only — one appointment (FR-014) |
| "What did I have in the past?" | past **standing** only — not the cancelled one (FR-015) |
| "What have I cancelled?" | both cancelled ones, past and future, each labelled cancelled |
| "Show me everything" | the future leg in full first, then the past leg, capped at 20 (FR-016) |

**The crowding check**: create more than 20 past appointments plus several future ones, then ask for
everything. The future leg must still be **complete**, and the assistant must say that the *past part*
of the list is not complete — not that the whole answer is partial (FR-016).

---

## Scenario 6 — A re-sent change is not a second change (SC-017)

The sequence the spec covers end to end in User Story 2 scenarios 7–11:

1. Move 09:00 → 10:00, confirm.
2. Move back to 09:00, confirm. The read-back must say **10:00** is the current start — not 09:00.
3. Move to 10:00 again, confirm.

**Expected**: three moves took effect in order; the appointment ends at 10:00 with the id it started
with; **exactly three** `appointment.rescheduled` records. Nothing recognised the third move as a
replay of the first — the failure that would leave the appointment at 09:00 while reporting success.

Then force the re-send directly, which the chat path cannot easily reproduce:

```bash
# call RescheduleAppointment twice with identical arguments, quoting the PRE-move state both times
```

**Expected**: the first returns `appointment`, the second returns `no_change` — **not**
`stale_confirmation`. The second arm of the guard (research #5) is what makes that true; without it a
retry of a change that landed reports a conflict for something that succeeded (SC-008).

---

## Scenario 7 — Two changes racing (SC-016)

Issue a cancellation and a move for the same appointment concurrently. This pairing is the one the
datastore cannot catch on its own — a cancellation collides with no other appointment.

**Expected**: exactly one takes effect; the other is refused `stale_confirmation`; the appointment is
never left in a state neither patient was told about, and no completed change is overwritten by one
that arrived after it (FR-021, SC-016).

If instead the loser succeeds, the staleness guard has been implemented as a check *before* the write
rather than as its `WHERE` clause — the exact defect research #5 exists to prevent.

---

## Automated suites

```bash
make test-unit           # scheduler + chat + shared-models
make test-integration    # the chat client against a real servicer and a real scheduler database
make lint && make typecheck
```

The unit tier is where the datastore rules are pinned, and they are testable before any service code
exists (Constitution VIII): both partial exclusion constraints, the partial unique index, the status
predicate on every read audited in [data-model.md](./data-model.md), the cascade that deliberately
takes cancelled rows too, the twelve-value precedence, and the `ChangeFailureReason` /
`BookingFailureReason` overlap test.
