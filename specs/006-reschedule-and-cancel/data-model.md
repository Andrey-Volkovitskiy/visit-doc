# Phase 1 Data Model: Rescheduling and Cancellation (Phase 1d, part 1)

**Feature**: `006-reschedule-and-cancel` | **Date**: 2026-08-29 | **Spec**: [spec.md](./spec.md)

One column is added and four constraints change. Everything else in this document is a consequence of
that column existing: an appointment can now be present in the store while counting for nothing, and
every rule that used to read "an appointment exists" has to say which appointments it means.

No new table is created. The change record of FR-036–FR-041 is a structured log event, not a row —
the spec's Assumptions exclude an audit store from this phase (research #14).

---

## Scheduler datastore (`visitdoc_scheduler`) — one changed table

### `appointments` — MODIFIED

| Column | Type | Change | Notes |
|---|---|---|---|
| `status` | `VARCHAR(16) NOT NULL DEFAULT 'standing'` | **NEW** | `CHECK (status IN ('standing','cancelled'))`. The single fact separating "this is happening" from "this was called off" (FR-009, research #1). |

No other column changes. `starts_at`, `ends_at`, `practitioner_id` are now mutable — they were only
ever written at insert before — and `updated_at`'s existing `onupdate` starts firing for real.

**There is no `cancelled_at`.** Nothing reads it: FR-037 puts the moment of a cancellation in the
log, tied to its turn. The spec's checklist deferred this question to this document; the answer is a
column that would be written, never read, and eventually trusted (research #1).

### Constraints on `appointments` — three replaced, one added

| Constraint | Before | After | Why |
|---|---|---|---|
| `appointments_patient_no_overlap` | `EXCLUDE USING gist (patient_id WITH =, tsrange(starts_at, ends_at) WITH &&)` | `… WHERE (status = 'standing')` | FR-010: a cancelled appointment must stop being a commitment of its patient. |
| `appointments_practitioner_no_overlap` | same, keyed by `practitioner_id` | `… WHERE (status = 'standing')` | FR-010, and SC-011's "bookable again immediately". |
| `appointments_idempotency_key_unique` | `UNIQUE (idempotency_key)` | **dropped**, replaced by `ix_appointments_idempotency_key_standing`: `CREATE UNIQUE INDEX … ON appointments (idempotency_key) WHERE status = 'standing'` | FR-011: a key lives as long as the appointment **stands**. PostgreSQL has no partial UNIQUE *constraint*, so this becomes an `Index(unique=True, postgresql_where=…)` — and the insert-conflict table in `appointment_repository` must match the **index** name (research #3). |
| `ix_appointments_patient_status_starts` | — | `INDEX (patient_id, status, starts_at)` **NEW** | Both legs of the two-axis listing (FR-013–FR-016) filter on patient and status and order by start. |

`appointments_ordered` (`ends_at > starts_at`) and the two FK cascades are unchanged. The cascades
are load-bearing and deliberately **not** status-scoped: deleting a chat's patient, or a
practitioner, removes cancelled appointments along with standing ones (research #4).

### What the partial exclusion constraints buy, beyond FR-010

An exclusion constraint compares **distinct rows**. Updating an appointment's interval is therefore
never checked against that appointment's own previous interval, so FR-007's "an appointment never
blocks its own change" holds on the write path with no code expressing it — including a move to the
time it already holds. Only the *offer* path needs the exclusion spelled out, as an
`excluded_appointment_id` on `CheckAvailability` (research #7).

---

## Chat datastore (`visitdoc_chat`) — unchanged

No migration. `chats.patient_id` and the message tables are untouched; nothing about a change is
stored chat-side. The pending confirmation lives in the conversation and nowhere else (spec Key
Entities).

---

## Cross-cutting Python types

### `packages/shared-models/src/shared_models/scheduling.py` — additions

```python
class AppointmentStatus(StrEnum):
    STANDING = "standing"      # counts: blocks its slot, holds its key, listed by default
    CANCELLED = "cancelled"    # kept, but a commitment for no one


class ChangeFailureReason(StrEnum):
    # This feature's own four, evaluated first, in this order (FR-006).
    APPOINTMENT_NOT_FOUND = "appointment_not_found"   # incl. another session's id (FR-018)
    ALREADY_CANCELLED = "already_cancelled"           # reschedule only — see below
    ALREADY_STARTED = "already_started"               # the appointment's CURRENT start (FR-005)
    STALE_CONFIRMATION = "stale_confirmation"         # FR-021
    # Booking's eight, unchanged in meaning and in string value (BookingFailureReason).
    PRACTITIONER_NOT_FOUND = "practitioner_not_found"
    PATIENT_NOT_FOUND = "patient_not_found"
    IN_PAST = "in_past"                               # the NEW start — not ALREADY_STARTED
    BEYOND_HORIZON = "beyond_horizon"
    OUTSIDE_SCHEDULE = "outside_schedule"
    OFF_GRID = "off_grid"
    PRACTITIONER_BUSY = "practitioner_busy"
    PATIENT_BUSY = "patient_busy"
```

```python
class TimeFilter(StrEnum):
    FUTURE = "future"          # starts strictly after local_now
    PAST = "past"              # starts at or before local_now, incl. one under way
    BOTH = "both"


class StatusFilter(StrEnum):
    STANDING = "standing"
    CANCELLED = "cancelled"
    BOTH = "both"
```

The two filters are cross-cutting types rather than wire-only, because both ends name them: the
scheduler's `list_for_patient()` takes them as parameters, and `list_my_appointments`'s tool schema
builds its `enum` lists from their values, so the strings a model may send and the strings the
repository branches on are the same declaration. `StatusFilter` deliberately mirrors
`AppointmentStatus` plus a `BOTH` rather than reusing it: a filter is not a status, and one type
serving both would make "the appointments I asked for" and "the state one of them is in" the same
value.

A unit test asserts, member by member, that every `BookingFailureReason` value appears in
`ChangeFailureReason` with the identical string — the mechanical pin that stops the two vocabularies
drifting (research #10). `ALREADY_CANCELLED` is reachable as a *failure* only for a reschedule; for a
cancellation the same state is the target state and the answer is `no_change` (research #9).

### `services/chat/src/chat/clients/scheduling.py` — new result types

Mirroring the existing `BookingSuccess` / `BookingRefusal` pair, one per wire outcome:

```python
@dataclass(frozen=True)
class ChangeApplied:      # the write moved the row
    appointment: AppointmentInfo
    previous_starts_at: datetime
    previous_practitioner_full_name: str

@dataclass(frozen=True)
class ChangeNoOp:         # already in the state asked for (FR-019, FR-040)
    appointment: AppointmentInfo

@dataclass(frozen=True)
class ChangeRefusal:      # one reason, from the twelve
    reason: ChangeFailureReason

@dataclass(frozen=True)
class AppointmentListing:  # FR-016's two legs, never merged into one list
    future: list[AppointmentInfo]   # ascending
    past: list[AppointmentInfo]     # descending, at most 20
    past_truncated: bool
```

`AppointmentInfo` gains `status: AppointmentStatus` (FR-015 requires a cancelled appointment to be
identified as cancelled wherever it appears).

`SchedulingUnavailableError` is unchanged and keeps its 005 meaning — the budget was exhausted. What
*changes* is what a write handler is allowed to conclude from it: nothing (FR-023, research #13).

### `services/chat/src/chat/agent/handle_booking.py` — `BookingOutcome` additions

```python
RESCHEDULED = "rescheduled"        # a move or a practitioner change took effect
CANCELLED = "cancelled"            # a cancellation took effect
UNCHANGED = "unchanged"            # already in the state asked for
OUTCOME_UNKNOWN = "outcome_unknown"  # sent, never answered — NOT "nothing happened"
```

Derivation precedence, extending 005's: any completed change (`BOOKED`/`RESCHEDULED`/`CANCELLED`) →
`UNCHANGED` → `OUTCOME_UNKNOWN` → `UNAVAILABLE` → `REFUSED` → `AWAITING_CONFIRMATION` →
`INFORMATIONAL`. `OUTCOME_UNKNOWN` sits above `UNAVAILABLE` because "we cannot tell you whether your
appointment moved" is the more important thing to say than "nothing happened" — and because saying
the latter when the former is true is the sentence FR-023 forbids.

---

## State transitions

```
                    book_appointment
        (nothing) ──────────────────────> standing
                                            │  │
                       reschedule_appointment│  │ cancel_appointment
                    (starts/ends/practitioner)  │
                                            │  │
                                            └──┘ ──────────────> cancelled
                                          (stays standing)             │
                                                                       │ (no transition out:
                                                                       │  cancellation is final,
                                                                       ▼  FR-005, Assumptions)
                                                                    cancelled
```

| From | Event | To | Guarded by |
|---|---|---|---|
| absent | `BookAppointment` | `standing` | unchanged from 005 |
| `standing` | `RescheduleAppointment` | `standing`, new interval and/or practitioner | the `WHERE` of research #5; both partial exclusion constraints |
| `standing` | `CancelAppointment` | `cancelled` | the same `WHERE`; the key leaves the partial unique index in the same statement |
| `cancelled` | `RescheduleAppointment` | — | refused `ALREADY_CANCELLED`; there is no un-cancel (FR-005) |
| `cancelled` | `CancelAppointment` | — | `no_change`, not a failure (FR-017, FR-019) |
| either | patient/practitioner deleted | absent | FK cascade, status-blind (research #4) |

An appointment whose start has passed has no distinct status: "already started" is a comparison
against the client's `local_now`, not a stored state (FR-005, FR-035). This is why the time axis and
the status axis of FR-013 are genuinely independent — one is computed, the other is stored.

---

## Validation rules, and where each is enforced

| Rule | Spec | Enforced |
|---|---|---|
| A cancelled appointment blocks nothing | FR-010 | **Datastore** — both exclusion constraints' `WHERE status = 'standing'` |
| A booking key outlives only a standing appointment | FR-011 | **Datastore** — partial unique index, plus the status predicate on `get_by_idempotency_key` |
| An appointment never blocks its own change (write) | FR-007 | **Datastore** — an exclusion constraint compares distinct rows |
| An appointment never blocks its own change (offer) | FR-007 | **Query** — `busy_intervals` omits `excluded_appointment_id` |
| The change is scoped to the session | FR-018 | **Query** — a predicate in the `UPDATE`'s `WHERE`, never a later check |
| Only a standing, not-yet-started appointment may change | FR-005 | **Query** — predicates in the same `WHERE` |
| The appointment still matches what the patient was shown | FR-021 | **Query** — the two-armed guard in the same `WHERE` (research #5) |
| A change is all-or-nothing | FR-003 | **Datastore** — one statement, one row |
| A changed appointment obeys every booking placement rule | FR-006 | **Application** — `domain/availability.validate_start`, the one implementation 005 already shares between offering and booking |
| The end is recomputed from the practitioner who will hold it | FR-004 | **Application** — `ends_at = new_start + practitioner.appointment_duration_minutes`, read at the moment of the change |
| Exactly one refusal reason, in a fixed precedence | FR-006 | **Application** — the classification read, ordered as research #10 |
| The unqualified listing is future *and* standing | FR-014 | **Contract** — the proto enums' zero values (research #11) |
| The past leg is capped at 20 and the future leg is not | FR-016 | **Query** — two statements, two orderings, one `LIMIT 21` to detect truncation |
| A cancelled appointment is labelled wherever it appears | FR-015 | **Contract** — `Appointment.status` on the wire and in the tool result |
| A change is confirmed before it is sent | FR-024–FR-030 | **Prompt + loop rules**, asserted by tests with scripted model responses (SC-002) |
| Recording never gates a change | FR-041 | **Application** — the log call follows the commit and is not awaited for correctness |
