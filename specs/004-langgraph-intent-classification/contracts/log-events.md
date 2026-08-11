# Contract: `intent.classified` Log Event

This feature adds no HTTP endpoint — its externally-observable "interface" is one new structured
log event, flowing through the same processor chain every other event in this codebase already
uses (`core/logging.py`, spec 002). Field definitions are in [data-model.md](../data-model.md); this
document fixes the event's wire shape, matching spec 002's `contracts/log-events.md` precedent for a
purely-internal feature. It also documents this feature's one change to an *existing* event's
timing: `turn.message_received` (spec 001) now fires earlier than it used to, ahead of
`intent.classified` — see §2 and §3 (research.md #8).

## 1. Event shape

```json
{
  "timestamp": "2026-08-09T10:14:02.331Z",
  "level": "info",
  "event": "intent.classified",
  "turn_id": "01J8Z3K9QAF7VXP9T6E9T3RZ9B",
  "intents": ["faq_question", "booking"]
}
```

- `timestamp`, `level`, `event` — same shared processor chain as every event (spec 002
  contracts/log-events.md).
- `turn_id` — the classified message's own id (spec 003 research.md #4), attached automatically by
  the existing `merge_contextvars` processor since classification runs inside the same
  `bind_turn_id()` context the patient message's turn already opened — not a new field the call site
  passes explicitly.
- `intents` — a non-empty array of `IntentLabel` string values (data-model.md). Either:
  - one or more of `"faq_question"`, `"booking"`, `"call_staff"`, `"unknown"` — a genuine,
    successful classification (possibly multi-label, FR-001), **or**
  - the single element `"classification_failed"` — the classification call itself failed or
    returned something invalid (FR-007); assigned by orchestration code, never present alongside a
    real category in the same event.

## 2. Rules that apply to every event of this type

- No raw patient message text ever appears on this event — no `message`/`content` field, unlike
  this codebase's pre-existing `turn.message_received` event (research.md #6). A reviewer who needs
  the actual message text joins back to the primary conversation store by `turn_id` (`message.persisted`'s
  own `message_id`, spec 003).
- Emitted only for a patient message whose turn actually completes — **never** for a message whose
  turn was superseded/cancelled by a follow-up message before classification finished (research.md
  #2). This event's presence/absence is directly coupled to whether an assistant `Message` row ends
  up being written for that turn: both happen, or neither does — they share one cancellable task.
- Governed by the same truncation/redaction rules as every other event (spec 002
  contracts/log-events.md §1) — moot in practice here, since `intents` never contains long strings or
  secret-shaped values, but the shared processor chain applies unconditionally regardless.
- `turn.message_received` (spec 001) always appears **before** `intent.classified` for the same
  `turn_id` — this feature moves it out of `answer_faq()` to fire before the graph invocation begins
  at all, specifically so it precedes classification rather than the FAQ-generation step it used to
  be attached to (research.md #8). Its own fields (`message`, `message_ids_unified`) are unchanged —
  only when it fires moves. A reader can therefore always tell what unified/merged message a turn is
  processing from the moment `turn.message_received` appears, regardless of whether classification
  or generation is the currently-active stage.
- Unlike `intent.classified`, `turn.message_received` is **not** gated on the turn completing — it
  now fires unconditionally, once per incoming patient message, before the cancellable graph task
  even exists (research.md #8). A message whose turn is cancelled a moment later by a follow-up still
  gets a `turn.message_received` line; it just never gets an `intent.classified` or `turn.completed`
  line. These two events answer different questions ("what did the system start processing" vs. "what
  did processing that message actually produce") and are correctly allowed different presence rules.

## 3. Terminal rendering example

```text
[INFO]     10:14:01 message.persisted  turn_id=01J8Z3K9QAF7VXP9T6E9T3RZ9B chat_id=... sender=patient
    content: "I'd like to book a visit to a cardiologist on Friday — what should I bring?"

[INFO]     10:14:01 turn.message_received  turn_id=01J8Z3K9QAF7VXP9T6E9T3RZ9B
    message: "I'd like to book a visit to a cardiologist on Friday — what should I bring?"
    message_ids_unified: ["01J8Z3K9QAF7VXP9T6E9T3RZ9B"]

[INFO]     10:14:02 intent.classified  turn_id=01J8Z3K9QAF7VXP9T6E9T3RZ9B
    intents: ["faq_question", "booking"]

[INFO]     10:14:03 turn.completed  turn_id=01J8Z3K9QAF7VXP9T6E9T3RZ9B outcome=grounded
```

Contract points this example fixes:

- `turn.message_received` now appears **before** `intent.classified`, not after it — this feature
  moved it out of `answer_faq()` to fire ahead of the whole graph invocation, precisely so a reader
  watching a turn that's still mid-classification (no `intent.classified` line yet) can already see
  what unified message that turn is about (research.md #8). Before this feature, it only ever fired
  from inside the single FAQ-answering step, so this ordering question didn't exist.
- `intent.classified` shares `turn_id` with the turn's other log lines (`message.persisted`,
  `turn.message_received`, `turn.completed`), so a reader can find every line for one patient message
  the same way they already do for the rest of a turn (spec 002) — but it is visibly a separate line,
  not merged into `message.persisted` or `turn.message_received`, and it carries no `content` field
  the way `turn.message_received` does.
- `intent.classified` always appears **before** `turn.completed` for the same `turn_id` —
  `classify_intent_node` runs first, sequentially, then `answer_faq_node` (research.md #1); this
  ordering is a structural guarantee, not an incidental one. A reader (or a future log-based eval
  harness, ROADMAP Phase 2) can still correlate by `turn_id` alone rather than relying on order, but
  the order is nonetheless fixed.
- On a **cancelled** turn (spec 003 FR-015), `intent.classified` does **not** appear for that
  `turn_id` — only a `turn.cancelled` line exists for it (spec 003), the same way no
  `message.persisted` line exists for the (never-written) assistant reply. This is the concrete,
  observable proof that classification shares the FAQ pipeline's cancellation, rather than surviving
  it (research.md #2, quickstart.md Scenario 3) — the superseded message's content still shows up
  indirectly, folded into the *next*, surviving message's own `intent.classified` line via FR-006's
  context window. `turn.message_received` **does** still appear for that same cancelled `turn_id`,
  though — it already fired before cancellation was even possible (research.md #8), so a reader sees
  the patient's message was received and what it unified to, just not what (if anything) came of it.
