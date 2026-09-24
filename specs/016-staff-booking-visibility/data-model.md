# Data Model: What staff can see of the schedule

There are two new things: a chat-side **booking act**, which is stored, and a scheduler-side
**practitioner week**, which is read on demand and never stored. Every existing table and column
keeps its meaning.

## Chat: `booking_acts` (new table)

One attempt by the assistant to change the schedule. The row is inserted **before** the scheduler
is called and settled at most once afterwards (research R5, R6).

| Column | Type | Null | Meaning |
|---|---|---|---|
| `id` | String(26) ULID | no | Primary key. |
| `seq` | BigInteger, `GENERATED ALWAYS AS IDENTITY` | no | Attempt order across the whole table. It orders a message's acts. Clocks are not used for ordering, following `messages.seq`'s precedent. |
| `session_id` | String(26) | no | The owning session, denormalized so every read and update carries it as a predicate. |
| `chat_id` | String(26), FK `chats.id` `ON DELETE CASCADE` | no | The conversation. |
| `message_id` | String(26), FK `messages.id` `ON DELETE CASCADE` | no | The **patient** message the turn was answering. |
| `operation` | String(16) | no | `book`, `reschedule` or `cancel`. What was attempted, fixed at insert. |
| `outcome` | String(16) | **yes** | `done`, `unchanged`, `refused`, `not_sent` or `unknown`. **NULL means never settled** (see below). |
| `refusal_reason` | String(48) | yes | The scheduler's reason code. Set exactly when `outcome = 'refused'`. |
| `appointment_id` | String(26) | yes | Known at insert for reschedule and cancel (an argument). For a booking it is known only once `done`. |
| `practitioner_id` | String(26) | no | The practitioner the appointment is (or would be) with. For a reschedule this is the **new** one. |
| `practitioner_full_name` | String(NAME_LENGTH) | yes | The name as known when the act ran: from the turn's roster at insert, overwritten by the scheduler's own on settle. NULL means not known at the time. |
| `starts_at` | DateTime (naive local) | no | The appointment's start. For a reschedule this is the **new** start. |
| `ends_at` | DateTime (naive local) | yes | Known only from a result (`done` or `unchanged`). |
| `previous_practitioner_id` | String(26) | yes | Reschedule only: the practitioner it moved from (the `expected_practitioner_id` guard). |
| `previous_practitioner_full_name` | String(NAME_LENGTH) | yes | Reschedule only, as above, for the name. |
| `previous_starts_at` | DateTime (naive local) | yes | Reschedule only: the start it moved from (the `expected_starts_at` guard). |
| `created_at` | timestamptz, `server_default now()` | no | Diagnostic only, never used for ordering. |
| `settled_at` | timestamptz | yes | Set together with `outcome`. |

**Check constraints**

- `operation IN ('book','reschedule','cancel')`.
- `outcome IS NULL OR outcome IN ('done','unchanged','refused','not_sent','unknown')`.
- `(outcome = 'refused') = (refusal_reason IS NOT NULL)`, with a NULL outcome counting as not
  refused.
- `(outcome IS NULL) = (settled_at IS NULL)`.
- `(operation = 'reschedule') = (previous_starts_at IS NOT NULL AND previous_practitioner_id IS NOT NULL)`.

**Indexes**

- `ix_booking_acts_chat_seq (chat_id, seq)`, which serves the thread read.
- `ix_booking_acts_message (message_id)`.

### What the columns take from each tool's arguments

| Operation | `practitioner_id` | `starts_at` | `appointment_id` | `previous_*` |
|---|---|---|---|---|
| `book` | `practitioner_id` | `starts_at` | set on `done` | none |
| `reschedule` | `new_practitioner_id`, or `expected_practitioner_id` when absent | `new_starts_at` | `appointment_id` | `expected_practitioner_id`, `expected_starts_at` |
| `cancel` | `expected_practitioner_id` | `expected_starts_at` | `appointment_id` | none |

### Lifecycle

```text
          plan_act ok
               │
   no patient ─┼──────────────► INSERT outcome='not_sent'        (terminal)
               │
               ▼
        INSERT outcome=NULL ── insert fails ──► handler NOT called; tool answers unavailable
               │
          handler runs
               │
   ┌───────────┼──────────────────────────────┐
   │ returns   │ raises / turn cancelled /    │
   ▼           │ settle write fails           ▼
 UPDATE … SET outcome=…, settled_at=now()   row stays outcome=NULL ⇒ read as unknown
 WHERE id=:id AND session_id=:s AND outcome IS NULL
```

- The settle's `WHERE outcome IS NULL` makes a second settle a no-op. A row is settled at most
  once, which is the FR-015 snapshot rule.
- NULL and `unknown` are distinct in storage and on the wire:
  - NULL means "no outcome was ever written" (the turn ended, a settle failed, or the act is still
    running);
  - `unknown` means "the tool's own answer was that the outcome is unknown".

  The console renders both with the same words (FR-012b). Keeping them distinct in the record costs
  nothing and keeps each value's meaning single.

## Chat: `MessageOut` gains `booking_acts`

```python
class BookingActOut(BaseModel):
    operation: Literal["book", "reschedule", "cancel"]
    outcome: Literal["done", "unchanged", "refused", "not_sent", "unknown"] | None
    refusal_reason: str | None
    practitioner_full_name: str | None
    starts_at: datetime  # naive local
    ends_at: datetime | None
    previous_practitioner_full_name: str | None
    previous_starts_at: datetime | None


class MessageOut(BaseModel):
    ...  # unchanged fields
    booking_acts: list[BookingActOut] | None = None
```

- `None` means no act was attempted for this message. The list is never empty (FR-014). That holds
  by construction, because only messages that appear in the grouped query receive a list.
- The list is in `seq` order.
- Ids (`appointment_id`, practitioner ids) stay in storage and are not sent. The console has no use
  for them, and a staff view built on the record should not start resolving them live, which would
  break the snapshot.

## Chat: `ConsoleConversationOut` gains `booking_acts_version`

```python
class ConsoleConversationOut(BaseModel):
    ...  # unchanged fields
    booking_acts_version: int  # count(acts) + count(settled acts); 0 when none
```

It is computed in the listing's own query, grouped per chat, with the session predicate. It
strictly increases on every insert or settle of that chat's acts, and never changes otherwise
(research R8b).

## Chat-side port: `BookingActRecorder`

This is the port through which the tool registry reaches storage. It lives in the agent layer, so
the registry depends on the abstraction rather than on SQLAlchemy.

| Method | Meaning |
|---|---|
| `learn_practitioners(roster)` | Remember `id → full_name` from the turn's roster read. It is called once by the booking node. |
| `name_of(practitioner_id) -> str \| None` | Used by `plan_act`. |
| `begin(planned) -> ActHandle` | Insert the unsettled row. It raises on failure, and the registry then refuses to send. |
| `record_not_sent(planned)` | Insert an already-settled `not_sent` row, used in the no-patient case. |
| `settle(handle, settlement)` | The conditional UPDATE above. |

The implementations are:

- **`DatabaseBookingActRecorder`**: bound to `(session_id, chat_id, message_id)` in `turn.launch`.
  Each call uses its own short `session_factory()` transaction and commits immediately, so the row
  is durable before the gRPC call leaves. It never takes the chat's advisory lock, because a staff
  post may hold that lock while cancelling this very turn.
- **`DiscardingBookingActRecorder`**: the `ToolContext` default, used in tests and in any context
  built without a turn.

`PlannedAct` holds `operation` plus the target columns above. `Settlement` holds `outcome`,
`refusal_reason`, and whatever the result reported: `appointment_id`, names, `starts_at`, `ends_at`
and `previous_*`.

## Scheduler: practitioner week (read model, not stored)

`GET /practitioners/{id}/appointments?ends_after=&starts_before=` returns:

```json
{
  "appointments": [
    {"id": "01K…", "patient_full_name": "Leo Tolstoy",
     "starts_at": "2026-09-24T14:00:00", "ends_at": "2026-09-24T15:00:00"}
  ]
}
```

The query is `appointments JOIN patients ON patients.id = appointments.patient_id AND patients.session_id = :s`,
with `WHERE appointments.session_id = :s AND practitioner_id = :p AND status = 'standing' AND ends_at > :ends_after AND starts_at < :starts_before ORDER BY starts_at, id`.
A practitioner not in the session gives 404 before the query runs.

**New index**: `ix_appointments_practitioner_status_starts (practitioner_id, status, starts_at)`.

## Frontend types (`src/lib/chatStream.ts`, `src/lib/consoleApi.ts`)

- `BookingAct`: mirrors `BookingActOut`. `Message` gains `booking_acts: BookingAct[] | null`, documented as "only ever set on a patient message on which the assistant attempted a change".
- `PractitionerAppointment { id, patient_full_name, starts_at, ends_at }`, and
  `fetchPractitionerWeek(practitionerId, localNow)`.
