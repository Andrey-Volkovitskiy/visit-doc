# Data Model: Answer What You Can (Phase 1h)

What the phase adds, what it removes, and the invariants each one has to hold. Nothing here is new
*information*: every field below was already produced per request by 1g's pipeline and thrown away
at the collapse. What changes is that it survives to the record.

---

## 1. `RequestOutcome` — new, and the only place a verdict lives

A validated wire model in `chat.domain.schemas`, beside `Citation`.

| Field | Type | Meaning |
|---|---|---|
| `position` | int | The request's place in the patient's message, 0-based. The join key every per-request log event already carries (010 FR-060). |
| `question` | str | The request **as the classifier restated it** — what was retrieved for and answered, not the patient's own wording (FR-005). |
| `answer` | str \| None | What the FAQ half generated for this request, before any merge. **None** for an abstention (FR-040a). |
| `verdict` | `FaqVerdict` | One of the existing six values. Describes this request's retrieval and nothing else. |
| `citations` | list[`Citation`] | The chunks *this* request's answer stands on, deduplicated within the request (FR-003). Empty for an abstention. |

### Invariants

1. **`answer` is None exactly when `verdict.answered` is False.** Not an empty string: an empty
   answer would be a second way of saying "abstained" that a reader could disagree with the verdict
   about. An answered request always has text, because generation is what produced the verdict.
2. **`citations` is empty exactly when `verdict.answered` is False.** An abstention cites nothing;
   an answered request cites the survivors that were placed in its prompt, which is at least one.
3. **`position` is unique within a message and the list is stored in ascending position order.**
   Order is the message's order (010 FR-003), and it is what the reply, the console and the derived
   unserved list all read.
4. **A chunk may appear under two outcomes.** That is two provenances, not a duplicate — it
   supported two answers (FR-003, superseding 010 FR-034).

---

## 2. `Message` — one column replaces two

```
messages
  ...
- faq_verdict         String(32)  NULL     -- REMOVED
- citations           JSONB       NULL     -- REMOVED
+ request_outcomes    JSONB       NULL     -- NEW: list[RequestOutcome], ordered by position
  reply_to_message_ids JSONB      NULL     -- unchanged
  attention_mark      String(32)  NULL     -- unchanged
```

**NULL means "no FAQ half ran"** (FR-004) — a booking-only reply, a hand-off, a small-talk reply, a
patient message, a staff message. It is deliberately distinct from `[]`, which nothing writes: a
turn whose FAQ half ran answered or abstained on at least one request, because the half refuses to
be entered with no request at all.

Plain JSONB for the same reason `citations` was: it is read only with its message, never joined on,
and a chat's messages are deleted together anyway (`chat_id`'s CASCADE). See research #1 for the
table alternative and why it is Phase 2's if anything.

`MessageOut` reads it through `from_attributes`, and Pydantic validates the stored dicts back into
`RequestOutcome`s on the way out — the same coercion `citations` relied on.

---

## 3. `FaqSegmentAnswer` and `FaqResult` — internal, and what each now means

`FaqSegmentAnswer` (frozen dataclass, `agent/compose_answer.py`) is unchanged in shape: `position`,
`question`, `answer_text`, `verdict`, `citations`, `scored_chunks`. It gains one projection to
`RequestOutcome`, which drops `scored_chunks` — those are the log's, and `Citation` deliberately
carries no score.

`FaqResult` changes meaning in three places:

| Member | Was | Becomes |
|---|---|---|
| `verdict` | the turn's summary, from `summarize_verdict` | **removed** — there is no such value (FR-002) |
| `citations` | every answered request's survivors, deduplicated across the turn; empty when the half abstained | **removed** — citations live on the outcomes |
| `answer_text` | the half's single text, or the abstention message | the half's single text when it contributes exactly one part; None otherwise |
| `part_count` | 1 if abstained, else one per request | one per **answered** request, plus 1 if any request abstained |
| `segment_answers` | every request's answer | unchanged — and now the thing everything else is derived from |

`summarize_verdict` is deleted (research #3). `deduplicate_chunks` survives and is applied per
request (research #11).

---

## 4. What the turn no longer has

Four values disappear, and it is worth naming them because each one was read somewhere:

| Value | Where it was read | What reads instead |
|---|---|---|
| `Message.faq_verdict` | console thread, patient pane, history reads, tests | each outcome's `verdict` |
| `Message.citations` | staff console citation list | each outcome's `citations` |
| `ChatDoneEvent.faq_verdict` / `.citations` | the frontend's streaming path | `ChatDoneEvent.request_outcomes` |
| `turn.completed`'s `faq_verdict` (and `answer_source`, research #9) | log queries counting abstentions | the event's per-request outcomes; `outcome` for the shape |

---

## 5. Migration

One revision — `575865d33df1`
(`services/chat/alembic/versions/575865d33df1_move_the_verdict_and_citations_onto_.py`), on top of
`7c76ca5716e8`:

- **upgrade**: `DROP COLUMN faq_verdict`, `DROP COLUMN citations`, `ADD COLUMN request_outcomes JSONB NULL`.
- **downgrade**: the reverse, restoring the two columns as NULL and dropping the new one.

No backfill, no dual-read, and the downgrade restores **no data** (FR-044a, research #10). This is
safe because FR-044 was carried out first: on 2026-09-10 every session was deleted through the
maintenance sweep — 48 sessions, 412 chats, 905 messages, 432 FAQ entries, 412 patients, 96
practitioners — and all three stores were verified empty. FR-072 requires that same verification
before the migration runs anywhere else.

---

## 6. State transitions

None. A request outcome is written once, with the reply it belongs to, and never updated — the same
lifecycle `citations` had. Nothing in the console, the escalation, or a staff takeover mutates it.
The turn's marks and escalation state continue to live on the *patient* message and the conversation
row, untouched by this phase.
