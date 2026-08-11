# Quickstart: Validating LangGraph + Intent Classification

Runnable validation for the three user stories in [spec.md](./spec.md). Assumes the `chat` service
is running locally (`make run-chat`) against a local PostgreSQL and Qdrant, migrations applied, and
at least one FAQ entry seeded (see spec 001's
[quickstart.md](../001-grounded-faq-chat/quickstart.md) Scenario 1). Unlike specs 001/003, this
feature adds no request/response field and no new endpoint — `POST /chat`'s contract is byte-for-byte
unchanged (data-model.md, research.md #1); what's new is only observable in the server's structured
logs (`intent.classified`, [contracts/log-events.md](./contracts/log-events.md)). Run the server in
a terminal you can watch (or `make run-chat 2>&1 | tee /tmp/visitdoc-chat.log`) so the log lines
below are visible as you send requests.

## Scenario 1 — User Story 1 (P1): FAQ answers are unaffected by the LangGraph swap

```bash
rm -f /tmp/visitdoc-cookies.txt

curl -s -N -c /tmp/visitdoc-cookies.txt -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What are your working hours?"}'
```

**Expected**: identical behavior to before this feature — a grounded, streamed answer (or an
abstention if ungrounded), citations included on a grounded answer. This is the regression check:
the FAQ-answering pipeline now runs inside a LangGraph graph (research.md #1), and this scenario
should be indistinguishable from spec 003's own Scenario 1 in every observable way. Confirms SC-001.

In the server log, confirm the new `intent.classified` line appears for the same `turn_id` as the
existing `message.persisted`/`turn.completed` lines, and appears **after** `turn.message_received`
— which itself now fires earlier than it used to (research.md #8), ahead of classification rather
than from inside FAQ generation:

```bash
grep -E "turn\.message_received|intent\.classified" /tmp/visitdoc-chat.log | tail -2
```

**Expected**: two lines, `turn.message_received` first, `intent.classified` second, same `turn_id`.
`intent.classified` shows `intents: ["faq_question"]` — a single, correct classification for a pure
FAQ question, with no `message`/`content` field on it (contracts/log-events.md §2 — privacy
requirement) — unlike `turn.message_received`, which still carries the full message text as it
always has (research.md #6 — that part is unchanged; only its timing moved).

## Scenario 2 — User Story 2 (P2): a mixed-intent message still gets one coherent FAQ-path reply

```bash
curl -s -N -b /tmp/visitdoc-cookies.txt -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I'\''d like to book a visit to a cardiologist on Friday — what should I bring?"}'
```

**Expected**: one coherent response through the existing FAQ path (FR-004) — it may answer the "what
should I bring" half if grounded content exists for it, or abstain, but it must never claim to have
booked, held, or scheduled anything (no booking capability exists yet this phase).

```bash
grep intent.classified /tmp/visitdoc-chat.log | tail -1
```

**Expected**: `intents` contains **both** `"faq_question"` and `"booking"` — the multi-label proof
(FR-001). Confirms SC-003's mixed-intent case.

```bash
curl -s -N -b /tmp/visitdoc-cookies.txt -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I need to talk to someone about a billing problem"}'
```

**Expected**: a coherent FAQ-path response (likely an abstention, since billing isn't FAQ content) —
not an error, not a fabricated hand-off.

```bash
grep intent.classified /tmp/visitdoc-chat.log | tail -1
```

**Expected**: `intents: ["call_staff"]`.

## Scenario 3 — the money scenario: a cancelled message's classification is abandoned, not recorded (research.md #2)

Send two messages in rapid succession on the same chat, so the first message's FAQ reply gets
cancelled by the second (spec 003 FR-015) — and confirm the **cancelled** message gets **no**
`intent.classified` log line at all, while the **surviving** message gets exactly one, reflecting
both messages' content via FR-006's context window.

```bash
rm -f /tmp/visitdoc-cookies3.txt

curl -s -N -c /tmp/visitdoc-cookies3.txt -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What are your working hours"}' > /tmp/visitdoc-msg1.ndjson &
MSG1_PID=$!

sleep 0.2

curl -s -N -b /tmp/visitdoc-cookies3.txt -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "actually, can I just book a slot on Tuesday instead?"}' > /tmp/visitdoc-msg2.ndjson

wait $MSG1_PID
```

**Expected** (same cancellation mechanics as spec 003's quickstart Scenario 6 — see there for the
NDJSON-level detail): message 1's stream ends with `{"type": "cancelled"}`; message 2 gets the real,
grounded reply.

```bash
grep intent.classified /tmp/visitdoc-chat.log | tail -2
```

**Expected**: exactly **one** `intent.classified` line since the last check, for message 2's
`turn_id` — none for message 1's. This is the concrete, observable proof of research.md #2's
lifecycle-sharing decision: classification is as much a casualty of cancel-and-restart as the FAQ
reply itself, never independently surviving it. Its `intents` includes **both** `"faq_question"`
(from message 1's "working hours," folded in via FR-006's context window even though message 1's own
attempt was cancelled) **and** `"booking"` (from message 2 itself) — the burst's content isn't lost,
it's consolidated onto the one message whose turn actually completes, exactly like the FAQ reply
itself already is (spec 003). Confirms FR-005/SC-002 (scoped to completed turns) under exactly the
burst condition spec.md's Edge Cases section calls out.

```bash
grep turn.message_received /tmp/visitdoc-chat.log | tail -2
```

**Expected**: **two** `turn.message_received` lines since the last check — one per patient message,
including message 1's, whose turn was cancelled. Unlike `intent.classified`, this event isn't gated
on the turn completing (research.md #8): it already fired for message 1 before cancellation was even
possible, so a reader sees "this message was received and unified to X" for both messages, even
though only message 2 went on to get a real `intent.classified`/`turn.completed` line.

*(Exact timing is inherently racy over `curl`, same caveat as spec 003's quickstart — the
deterministic version of this check is `test_chat_api.py`'s mocked-timing regression test, per
plan.md's Project Structure.)*

## Scenario 4 — User Story 3 (P3): reviewing classifications without re-running the conversation

After Scenarios 1-3 have run, a maintainer can review what was classified purely from the logs
already captured — no need to resend anything:

```bash
grep intent.classified /tmp/visitdoc-chat.log
```

**Expected**: every patient message whose turn actually completed above has exactly one
corresponding line, each showing its `turn_id` and recorded `intents` — sufficient to spot-check
classification quality (e.g., confirming Scenario 2's mixed-intent message really did get both
labels) without needing a UI or re-running any conversation. Scenario 3's cancelled message 1 has no
line, by design (research.md #2) — its content is still visible, folded into message 2's own
`intents`. Confirms SC-002.

## Scenario 5 — a failed classification call still doesn't block the FAQ reply (FR-007)

Not directly triggerable via `curl` without simulating a downstream failure (e.g., an invalid
`ANTHROPIC_API_KEY` scoped just to the classification call, or a mocked timeout) — covered instead
by `test_classify_intent.py`/`test_chat_api.py` (mocking the classification call to raise), per
plan.md's Project Structure. Conceptually: send a message, have the classification call fail, then
confirm (a) the FAQ reply still streams normally as if nothing happened, and (b) the log shows
`intent.classified` with `intents: ["classification_failed"]` for that `turn_id` — never silently
recorded as `"faq_question"` or omitted entirely.

## End-to-end demo

Scenarios 1 → 2 → 3 in sequence — a plain FAQ question working exactly as before, a mixed-intent
message producing multiple recorded labels while still answering gracefully, then a rapid burst
proving a cancelled message's classification is abandoned (not wasted-but-orphaned, not silently
recorded) while its content still reaches the surviving message's own classification — demonstrate
the full feature to someone with no prior knowledge of the system's internals. Scenario 4
(`grep`-ing the accumulated log) is the natural close, showing the review story the feature exists
to set up for Phase 1d.
