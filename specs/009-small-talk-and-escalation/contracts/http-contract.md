# Contract: HTTP and Client-Visible Changes

Three string-level widenings and nothing else. No endpoint is added, removed, or re-shaped; no
request body changes; no status code changes.

## `MessageOut.attention_mark` (`GET /chats/{id}`, `GET /console/conversations/{id}`)

The literal union gains four members:

```
"patient_asked_for_person" | "corpus_could_not_answer" | "assistant_failed" | "unanswered"
  | "urgent_condition" | "distress" | "booking_for_another_person" | "not_authorized"
```

Still only ever set on a patient message; still `null` for every other sender.

## `ChatDoneEvent.answer_source` (the NDJSON turn stream)

Gains `small_talk`. `hand_off` widens in meaning (a fixed notice that a person now has this) without
changing its wire value. For every reply this phase introduces, `faq_verdict` is `null` and
`citations` is `[]` — the existing shape for a reply that was never retrieved against.

## `ConsoleConversationOut.escalation_reason`

Already a free string; it now carries the four new values. No client currently renders it, and this
phase does not start: FR-041's requirement is satisfied by the *message* mark, which the staff
thread already draws.

## Frontend

| File | Change |
|---|---|
| `services/frontend/src/lib/chatStream.ts` | `AttentionMark` union gains the four values |
| `services/frontend/src/lib/consoleApi.ts` | `ATTENTION_MARK_LABEL` gains four entries: "Urgent condition", "Patient in distress", "Booking for someone else", "Not something the assistant may do" |

The label map is exhaustive over the union by type, so a missing entry is a compile error rather
than a blank badge — which is what keeps FR-041 from silently degrading to "needs attention".

## Backward compatibility

An older client receiving one of the new mark strings fails its literal check. The two ship in the
same change, so this is not reachable in deployment; it is recorded because it is the only
compatibility surface the phase has.
