# Contract: Booking acts on the thread

## Wire: `GET /chats/{chat_id}/messages`

Each `MessageOut` gains one field, and every other field is unchanged:

```json
"booking_acts": null
```

or

```json
"booking_acts": [
  {
    "operation": "reschedule",
    "outcome": "done",
    "refusal_reason": null,
    "practitioner_full_name": "Andreas Vesalius",
    "starts_at": "2027-01-12T10:00:00",
    "ends_at": "2027-01-12T11:00:00",
    "previous_practitioner_full_name": "Andreas Vesalius",
    "previous_starts_at": "2027-01-12T09:00:00"
  }
]
```

| Field | Values | Rule |
|---|---|---|
| `booking_acts` | `null` or a non-empty list | `null` means no change to the schedule was attempted for this message. `[]` is never sent. A list appears only on `sender = "patient"` messages. |
| `operation` | `book`, `reschedule`, `cancel` | |
| `outcome` | `done`, `unchanged`, `refused`, `not_sent`, `unknown`, `null` | `null` means no outcome was ever recorded. **A reader MUST treat it exactly as `unknown`** (FR-012b). |
| `refusal_reason` | a scheduler reason code, or `null` | Non-null exactly when `outcome = "refused"`. |
| `practitioner_full_name` | a string, or `null` | `null` means the name was not known when the act ran. |
| `starts_at` / `ends_at` | naive local datetimes | `ends_at` is `null` unless the scheduler reported it. For a reschedule these are the **new** times. |
| `previous_*` | set only for `reschedule` | Where it moved from. |

The list is ordered by attempt. A message never loses or changes an act once written, whatever
later happens to the appointment, the practitioner, or the conversation (FR-015).

## Wire: `GET /console/conversations`

Each row gains one field:

| Field | Type | Rule |
|---|---|---|
| `booking_acts_version` | integer ≥ 0 | 0 for a conversation with no acts. It increases by one when an act is recorded and by one when it is settled, and never changes otherwise. It is not a clock, and a reader MUST compare it only for equality. |

**Reader rule (the staff thread):** re-read the thread whenever the pair
`(last_message_at, booking_acts_version)` differs from the last pair handled. A change in either
alone is enough.

## Recording rule (tool registry)

These are the behaviours a test of the registry checks, one per row:

| Situation | Handler called? | Row written | Tool result |
|---|---|---|---|
| Arguments invalid (`ToolArgumentError`), patient record exists | no | none | raises, exactly as today |
| Arguments invalid, no patient record | no | none | `_NO_PATIENT_RESULT`, exactly as today (FR-021a, T046) |
| No patient record | no | `outcome = not_sent` | `_NO_PATIENT_RESULT`, as today |
| Recorder `begin` fails | **no** | none | `{"status": "unavailable", …}`, logged `booking_act.record_failed` |
| Handler returns `booked` / `changed` | yes | settled `done`, names and times from the result | unchanged |
| Handler returns `unchanged` | yes | settled `unchanged` | unchanged |
| Handler returns `refused` | yes | settled `refused` with `reason` | unchanged |
| Handler returns `unavailable` | yes | settled `not_sent` | unchanged |
| Handler returns `unknown` | yes | settled `unknown` | unchanged |
| Handler raises | yes | left `outcome = NULL` | raises, exactly as today |
| Turn cancelled mid-call | yes | left `outcome = NULL` | never returns |
| `settle` fails | yes | left `outcome = NULL`, logged `booking_act.settle_failed` | unchanged |
| A read tool (`list_*`, `check_availability`) | yes | none | unchanged |

The first four columns of this table are the whole contract. **The agent never reads the
recorder**: the port has no read method, and no prompt, history or tool result carries acts
(FR-021a).

## Log events (new)

| Event | Level | Fields |
|---|---|---|
| `booking_act.record_failed` | error | `tool_name`, `operation`, `stage` (`begin` or `not_sent`), `error_type`, `error_detail` |
| `booking_act.settle_failed` | error | `tool_name`, `operation`, `act_id`, `outcome`, `error_type`, `error_detail` |

No existing event changes. `turn.completed`, `booking.tool_result` and the trace's `tool:<name>`
observation keep their payloads.
