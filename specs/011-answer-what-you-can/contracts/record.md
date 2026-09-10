# Contract: The record, after the verdict moves onto the request

What a turn stores, what it streams, and what the console reads — once `faq_verdict` and
`citations` are gone from all three. Covers FR-001–FR-005, FR-040–FR-044a.

---

## 1. `RequestOutcome`

```jsonc
{
  "position": 0,                       // 0-based, the request's place in the message
  "question": "What should I bring to a first visit?",   // the classifier's restatement
  "answer": "Please bring your ID and…",  // null for an abstention
  "verdict": "answered",               // one of FaqVerdict's six values
  "citations": [                       // empty for an abstention
    {"entry_id": 12, "chunk_index": 0, "chunk_text": "…"}
  ]
}
```

Invariants (also in `data-model.md` §1): `answer` is null exactly when the verdict is an abstention;
`citations` is empty exactly then; positions are unique and ascending; a chunk may appear under two
outcomes.

---

## 2. The stored message

| Field | Before | After |
|---|---|---|
| `faq_verdict` | `"answered"` \| … \| null | **gone** |
| `citations` | `[Citation]` \| null | **gone** |
| `request_outcomes` | — | `[RequestOutcome]` \| null |

`null` means no FAQ half ran; `[]` is never written (FR-004). Everything else on the row —
`content`, `reply_to_message_ids`, `attention_mark` — is unchanged, and `content` remains the reply
the patient was shown, stored once (FR-040b).

---

## 3. `ChatDoneEvent` (the terminal NDJSON event)

```jsonc
{
  "type": "done",
  "request_outcomes": [ /* RequestOutcome, … */ ] | null,
  "message": "…" | null,          // unchanged meaning: a reply to render instead of the tokens
  "answer_source": "merged"       // unchanged, and stays on the wire (research #9)
}
```

`faq_verdict` and `citations` are removed. `request_outcomes` is null on exactly the turns that
stored null: a booking-only reply, a hand-off, a small-talk reply.

Emitted, unchanged in *who* emits it, from three places — the streaming FAQ path, the collapse path
in `compose_answer_node`, and the merged path in `compose_answer()`. All three now carry the same
list, which is the point: one shape, whatever produced the reply.

The patient pane still renders none of it (FR-042). It stays in the payload because one session owns
both panes and can read every corpus entry on the FAQ screen — omitting it would protect nothing
while splitting one record into two shapes free to drift.

---

## 4. `MessageOut` (history and console reads)

Loses `faq_verdict` and `citations`, gains `request_outcomes: list[RequestOutcome] | None`. Only ever
non-null on an assistant message. `attention_mark` is unchanged and still only ever set on a patient
message.

No new endpoint, no new query parameter, and no console response envelope changes: the outcomes ride
on the message that already travels.

---

## 5. What must not exist after this change

- No turn-level verdict, in storage, on the wire, or in the log (FR-002).
- No turn-level citation list (FR-003 makes citations a property of a request).
- No code path that can read the old two-column shape (FR-044a) — including a "if `request_outcomes`
  is null, fall back to `faq_verdict`" branch, which is the dual-read this forbids.
- No `summarize_verdict`, and no equivalent reduction under another name.

---

## 6. Migration

`DROP faq_verdict`, `DROP citations`, `ADD request_outcomes JSONB NULL`. No backfill; the downgrade
restores schema, not data. Legal only because FR-044 deleted every session first and FR-072 requires
that verified — in the chat store, the scheduler's store, and Qdrant — before it runs anywhere.

---

## 7. Test obligations

| # | Assertion |
|---|---|
| R1 | A two-request turn with one answered and one abstained stores two outcomes, in position order, with the abstained one carrying `answer: null` and `citations: []`. |
| R2 | A booking-only turn, a hand-off and a small-talk turn each store `request_outcomes = null`, never `[]`. |
| R3 | A single-request FAQ turn stores exactly one outcome, whose `answer` is the text the patient was shown. |
| R4 | The same chunk answering two requests appears under both outcomes, once each. |
| R5 | `ChatDoneEvent` carries the same list the message stores, for all three emitting paths. |
| R6 | `MessageOut` round-trips the stored JSONB back into `RequestOutcome`s, including `answer: null`. |
| R7 | Nothing in `services/chat/src` or `services/frontend/src` references `faq_verdict` or a message-level `citations` after the change (a grep-level assertion in review, not a runtime test). |
