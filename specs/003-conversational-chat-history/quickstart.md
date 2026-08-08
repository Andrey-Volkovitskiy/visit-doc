# Quickstart: Validating Conversational Chat History

Runnable validation for the three user stories in [spec.md](./spec.md). Assumes the `chat` service
is running locally (`make run-chat`) against a local PostgreSQL and Qdrant, migrations applied, and
at least one FAQ entry exists covering "working hours" (see spec 001's
[quickstart.md](../001-grounded-faq-chat/quickstart.md) Scenario 1 to seed one, e.g. content
`"Working hours: Monday to Friday, 8am to 5pm."`). Endpoint shapes are defined in
[contracts/openapi.yaml](./contracts/openapi.yaml); entity shapes in [data-model.md](./data-model.md).

Every scenario below uses `curl`'s cookie jar (`-c`/`-b`) to carry the `visitdoc_session_id` cookie
between requests, the same way a browser would. That cookie identifies a `Session`, not a
`Chat` directly (data-model.md, research.md #1) — its value stays the same across a clear
(Scenario 4). A chat is a flat list of sender-tagged messages, not paired turns — nothing
here assumes strict patient/assistant alternation (FR-002, FR-013, FR-014).

## Scenario 1 — User Story 1 (P1): a follow-up that relies on earlier context

```bash
rm -f /tmp/visitdoc-cookies.txt

curl -s -N -c /tmp/visitdoc-cookies.txt -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I'\''m going to come on Tuesday"}'
```

**Expected**: a `Set-Cookie: visitdoc_session_id=...` is issued (first message, no prior cookie) —
visible via `-c`'s written cookie jar. The reply itself may be an abstention (this message isn't a
question the FAQ can ground — see research.md #6, a known/accepted limitation), which is fine; what
matters is the message is now stored.

```bash
curl -s -N -b /tmp/visitdoc-cookies.txt -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "what are your working hours that day?"}'
```

**Expected**: the streamed answer addresses **Tuesday's** hours specifically (not a generic "here
are our hours" answer) — evidence the assistant used the first message as context (FR-003).
Confirms SC-001.

## Scenario 2 — User Story 2 (P2): history survives a reload

```bash
curl -s -b /tmp/visitdoc-cookies.txt http://localhost:8000/chat
```

**Expected**: `200`, a `ChatHistoryResponse` whose `messages` array contains both messages
from Scenario 1 — the patient message (`sender: "patient"`) followed by the assistant's reply
(`sender: "assistant"`, with `citations`) — in order. This is exactly what the frontend calls on
page load to hydrate the chat window after a reload (FR-001, FR-002). Confirms SC-002.

```bash
# No cookie at all — simulates a first-ever visit
curl -s http://localhost:8000/chat
```

**Expected**: `200`, `{"messages": []}` — no error, no cookie set (GET never creates a session or
chat, data-model.md `Session`/`Chat` Lifecycle).

## Scenario 3 — abstention and grounding still apply per message

```bash
curl -s -N -b /tmp/visitdoc-cookies.txt -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "what is the weather like today?"}'
```

**Expected**: `grounded: false`, an abstention message — identical behavior to spec 001's
single-turn abstention, now happening inside a multi-turn chat. Confirms FR-007.

## Scenario 4 — User Story 3 (P3): clear the chat

```bash
# Note the session cookie's value before clearing, to confirm it's unchanged after
grep visitdoc_session_id /tmp/visitdoc-cookies.txt

curl -s -o /dev/null -w "%{http_code}\n" -b /tmp/visitdoc-cookies.txt -c /tmp/visitdoc-cookies.txt \
  -X DELETE http://localhost:8000/chat
```

**Expected**: `204`, with no `Set-Cookie` header — the session cookie's value in the jar is
unchanged (research.md #7). Then confirm the chat is actually gone:

```bash
curl -s -b /tmp/visitdoc-cookies.txt http://localhost:8000/chat
```

**Expected**: `{"messages": []}` — the old chat and its messages are hard-deleted (FR-005),
confirming SC-003. Now confirm no memory leaks across the clear:

```bash
curl -s -N -b /tmp/visitdoc-cookies.txt -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "what are your working hours that day?"}'
```

**Expected**: the assistant does **not** answer as if "Tuesday" was mentioned (no prior messages
exist in this brand-new chat) — confirms FR-006. No `Set-Cookie` is issued this time either
— the same `Session` from before the clear is reused, it just lazily gets a new, empty
`Chat` (data-model.md `Chat` Lifecycle, research.md #7).

```bash
# Clearing again immediately (nothing to clear) is a harmless no-op
curl -s -o /dev/null -w "%{http_code}\n" -b /tmp/visitdoc-cookies.txt -X DELETE http://localhost:8000/chat
```

**Expected**: `204` (not an error) — confirms the edge case in spec.md ("Clear chat" on an
already-empty chat).

## Scenario 5 — a failed reply still leaves the message in history (FR-012)

Not directly triggerable via `curl` without simulating a downstream failure (e.g. stopping Qdrant or
Anthropic mid-call); covered instead by `test_chat_repository.py`/`test_chat_api.py`
(mocking the pipeline to raise) per `plan.md`'s Project Structure. Conceptually: send a message,
have the pipeline raise before completion, then `GET /chat` and confirm the patient message
is present (`sender: "patient"`) with no assistant message immediately following it — and that a
subsequent message's answer still reflects that earlier message's content (it's still fed into
`messages` history, research.md #5).

## Scenario 6 — a burst of messages: cancel-and-restart, merged retrieval (FR-014/FR-015)

Send a second message before the first has finished streaming, on the same chat, where the *second*
message is a fragment with no retrieval signal on its own — confirming both that only one reply is
ever stored, and that retrieval used the merged burst rather than the fragment alone (research.md
#6).

```bash
rm -f /tmp/visitdoc-cookies2.txt

# Start message 1's stream in the background, capturing raw ND-JSON lines. Note message 1 carries
# all the FAQ-relevant signal ("working hours"); message 2 alone has none.
curl -s -N -c /tmp/visitdoc-cookies2.txt -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What are your working hours"}' > /tmp/visitdoc-msg1.ndjson &
MSG1_PID=$!

# Give it just enough time to be accepted and start generating, but not finish
sleep 0.2

curl -s -N -b /tmp/visitdoc-cookies2.txt -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "on Tuesdays specifically?"}' > /tmp/visitdoc-msg2.ndjson

wait $MSG1_PID
```

**Expected**:
- `/tmp/visitdoc-msg1.ndjson` ends with a line `{"type": "cancelled"}` (`ChatCancelledEvent`) rather
  than a `{"type": "done", ...}` line — message 1's in-flight generation was cancelled when message 2
  arrived (FR-015).
- `/tmp/visitdoc-msg2.ndjson` ends with a normal `done` event, `grounded: true`, and an answer that
  addresses **Tuesday's working hours** specifically. This is the retrieval-merge check: message 2's
  own text ("on Tuesdays specifically?") carries no "working hours" signal at all — if retrieval had
  queried on message 2 alone (the pre-revision behavior), it would likely have found nothing relevant
  and abstained. A grounded, on-topic answer here is evidence retrieval queried on the merged burst
  ("What are your working hours\non Tuesdays specifically?"), not the fragment in isolation
  (research.md #6).

```bash
curl -s -b /tmp/visitdoc-cookies2.txt http://localhost:8000/chat
```

**Expected**: `{"messages": [...]}` with exactly three entries — the two patient messages, in the
order they were sent, followed by exactly **one** assistant message (answering message 2, in light
of both) — not two assistant messages, and no assistant message answering message 1 in isolation.
Confirms FR-013/FR-014 (flat, non-alternating log) and FR-015 (cancel-and-restart yields at most one
reply per burst, not one per message).

*(Exact timing is inherently racy over `curl`; if message 1 happens to finish before message 2 is
sent, increase or remove the `sleep` and retry. The unit/integration tests in
`test_generation_registry.py`/`test_chat_api.py` exercise this deterministically by mocking the
pipeline's completion timing instead of relying on wall-clock delay.)*

## End-to-end demo

Scenarios 1 → 2 → 4 in sequence — asking a follow-up that relies on earlier context, reloading to
see the full chat still there, then clearing it and confirming the slate is truly clean —
constitute the full feature demo, runnable by someone with no prior knowledge of the system's
internals. Scenario 6 is a good follow-on to show the "real chat" burst behavior specifically.
