# Contract: the MCP connector's two tools

Endpoint: `POST {BASE}/mcp` (streamable HTTP), bearer token required, scope `console:read`. The
session each tool answers for is the grant's session, read from the verified token. No tool takes
a session, chat or grant argument (FR-014).

Both tools are read-only and annotated so (`readOnlyHint: true`), and there are no others (FR-015).

## `count_conversations_needing_attention`

Description given to the model:
> Returns how many of the clinic's conversations are waiting for a staff member right now: the
> ones the Staff Console marks as needing attention. Returns a count only, never which
> conversations or what they say.

Input: `{}`.

Structured result:
```json
{"needing_attention": 2}
```
Text content: `"2 conversations need staff attention."` (singular for 1, and "No conversations
need staff attention." for 0).

Equal, by construction, to the number of rows the console listing returns with `emphasized = true`
at the same moment (FR-016, SC-002).

## `count_recent_booking_changes`

Description given to the model:
> Returns, for the last `minutes` minutes, how many appointments were booked, cancelled and
> rescheduled, one count per kind, and how many changes have an outcome nobody knows yet (the
> scheduler's answer never arrived). Convert hours to minutes yourself: the last 2 hours is
> `minutes: 120`. Refused and unchanged attempts are not counted, because they changed nothing.
> The result includes a sentence summarizing the counts; answer with that sentence as it is.
> Returns counts only.

Input schema:
```json
{"type": "object",
 "properties": {"minutes": {"type": "integer", "minimum": 1, "maximum": 10080}},
 "required": ["minutes"], "additionalProperties": false}
```

Structured result:
```json
{"window_minutes": 60, "booked": 3, "cancelled": 1, "rescheduled": 2, "outcome_unknown": 0}
```

Text content, from `describe_recent_changes()` (research R11):

| Counts (booked, cancelled, rescheduled, unknown) | Text |
|---|---|
| 3, 1, 2, 0 | `Three appointments were booked, one cancelled and two rescheduled.` |
| 1, 0, 2, 0 | `One appointment was booked and two rescheduled.` |
| 0, 1, 0, 0 | `One appointment was cancelled.` |
| 12, 0, 0, 0 | `12 appointments were booked.` |
| 0, 0, 0, 0 | `No appointments were booked, cancelled or rescheduled.` |
| 3, 1, 2, 2 | `Three appointments were booked, one cancelled and two rescheduled. Two changes have an unknown outcome — check the Staff Console.` |
| 0, 0, 0, 1 | `No appointments were booked, cancelled or rescheduled. One change has an unknown outcome — check the Staff Console.` |

Claude writes its own reply, so this sentence is what it is asked to relay, not what it is
guaranteed to say.

`minutes` outside 1–10080, or not an integer, is a tool error: `"minutes must be a whole number
from 1 to 10080 (7 days)."` It is never clamped to a different window (FR-018).

Counting rules are in `data-model.md` → `RecentBookingChanges`.

## Invariants the tests hold

- Neither result carries a patient name, practitioner name, message text, or any chat, message,
  appointment or grant id (FR-019).
- A token for session A never yields session B's counts (SC-006).
- A revoked grant's token gets `401` on its next call (FR-021, SC-004).
