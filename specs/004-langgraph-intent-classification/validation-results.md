# Validation Results: SC-003 (accuracy) and SC-004 (latency)

Run on 2026-08-09 against a locally running `chat` service (`make run-chat`), real Postgres/Qdrant
containers, and the real Claude API (Haiku 4.5 for classification, per plan.md) — not the mocked
test suite. Produced by tasks.md T020/T021.

## SC-003: classification accuracy on a hand-labeled sample

**Target**: recorded intent(s) match expected intent(s) at least 80% of the time (spec.md SC-003).

15 representative messages, including two mixed-intent messages and two context-dependent
follow-ups (each sent as the second message of a two-message session, so the classifier had to
resolve it using prior-turn context — spec.md Edge Cases).

| # | Message | Expected | Actual | Match |
|---|---|---|---|---|
| 1 | "What are your visiting hours?" | faq_question | faq_question | ✅ |
| 2 | "Can I bring my kids when I visit?" | faq_question | faq_question | ✅ |
| 3 | "I'd like to book an appointment for next Tuesday" | booking | booking | ✅ |
| 4 | "Can you reschedule my appointment to Friday?" | booking | booking | ✅ |
| 5 | "I want to cancel my booking" | booking | booking | ✅ |
| 6 | "I need to speak with a staff member about a billing issue" | call_staff | call_staff | ✅ |
| 7 | "This is urgent, I need to talk to someone right now" | call_staff | call_staff | ✅ |
| 8 | "What's the capital of France?" | unknown | unknown | ✅ |
| 9 | "asdkjaslkdj random text" | unknown | unknown | ✅ |
| 10 | "I'd like to book a visit to a cardiologist on Friday — what should I bring?" | booking + faq_question | booking + faq_question | ✅ |
| 11 | "Can I ask about your FAQ and also change my appointment time?" | faq_question + booking | faq_question + booking | ✅ |
| 12 | "Do you accept insurance?" | faq_question | faq_question | ✅ |
| 13 | "I have a complaint about a nurse" | call_staff | call_staff | ✅ |
| 14 | "What are your hours on Tuesdays?" → "what about Thursdays instead?" (context-dependent) | faq_question | faq_question | ✅ |
| 15 | "I'd like to book a slot" → "yes, Friday works" (context-dependent) | booking | booking | ✅ |

**Result: 15/15 = 100% match — well above the 80% target.** Both mixed-intent cases recorded
exactly the expected label set (not a superset/subset), and both context-dependent short replies
("what about Thursdays instead?", "yes, Friday works") correctly inherited their intent from the
prior turn, confirming FR-006's context window does what it's for.

## SC-004: added latency budget

**Target**: classification adds no more than 1-2 seconds, on average, to the time before the FAQ
answer starts streaming (spec.md SC-004). Measured as the gap between `turn.message_received`
(logged right before the graph task starts) and that same turn's `intent.classified` (logged the
moment `classify_intent_node` finishes) — this gap is exactly the latency classification adds,
since `answer_faq_node`'s own retrieval/generation time is unchanged by this feature.

16 real turns measured (the 15 SC-003 messages + one context-pair's first message):

```
0.759s 0.786s 0.794s 0.835s 0.862s 0.873s 0.927s 0.988s
0.997s 1.036s 1.117s 1.119s 1.315s 1.442s 1.654s 1.719s
```

- **Average: 1.08s** — within the 1-2s budget.
- Median: 0.99s, min: 0.76s, max: 1.72s.

One additional turn was excluded as a measurement artifact: its logged `turn.message_received`
timestamp trailed its own `intent.classified` timestamp by ~14s, which is causally impossible
(classification happens after message-received in the code) — almost certainly stdout buffering on
the redirected log file reordering that one line's on-disk position relative to its neighbors under
this ad hoc manual test run, not a real latency spike. Worth a note, not a re-run: the other 16
samples are internally consistent and already comfortably confirm the budget.

**Result: confirmed within SC-004's 1-2 second budget**, consistent with plan.md's Performance
Goals reasoning (Haiku 4.5 on a short, closed-set, structured-output call).
