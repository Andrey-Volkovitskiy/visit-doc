# Quickstart: Answer What You Can (Phase 1h)

A manual walk-through of the behaviour this phase adds and the three cases it must not disturb.
Nine scenarios, run by hand against a live stack — the unit tier proves all of it offline against
stubbed retrieval, and this is where a person watches a real composing call keep an abstention
intact next to a real answer.

Patient turns here spend real Claude and Voyage calls, which is expected for manual verification
(see `docs/testing-strategy.md`).

## Prerequisites

```bash
make services-up          # chat :8000, scheduler :8001, frontend :5173, pids/logs under .run/
make services-status
make migrate              # this phase ships a migration — do not skip it
```

Two windows help: one driving `scripts/dev-chat.sh`, one following the log.

```bash
scripts/dev-chat.sh session          # mint a session + its first chat; prints the chat id
CHAT=<the id it printed>
tail -f .run/chat.log | grep -E 'intent.classified|faq\.verdict|turn.completed|escalation'
```

Every session starts holding the default corpus. **The scenarios below need one question the corpus
answers and one it does not.** The default corpus covers referrals, what to bring, telehealth,
insurance plans, out-of-pocket costs, payment, hours and location (including parking), and how early
to arrive — and says nothing about imaging or prescriptions, so "do you do MRI scans?" and "can I
get a prescription refill here?" are the reliable gaps. If that changes, pick any subject the
listing below lacks, or add a known-answerable entry:

```bash
scripts/dev-chat.sh faq "Our lab returns routine blood work within two business days."
```

(Do not seed a parking-cost entry — scenario 3 needs that one to stay a gap.)

## Reading the record

The turn's outcomes are on the stored reply, not on a verdict field — there is no verdict field:

```bash
docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_chat -t -c \
  "select jsonb_pretty(request_outcomes) from messages
    where request_outcomes is not null order by created_at desc limit 1;"
```

And in the log, one entry per request rather than one value for the turn:

```bash
grep turn.completed .run/chat.log | tail -1 | python3 -c 'import json,sys
e = json.loads(sys.stdin.read())
print("outcome:", e["outcome"], "| segments:", e["segment_count"])
for o in e.get("request_outcomes", []):
    print(" ", o["position"], o["verdict"], len(o["citations"]), "chunks")'
```

---

## 1. The headline: one question answered, one named as a gap  *(US1, SC-001, SC-002)*

```bash
scripts/dev-chat.sh say $CHAT "What are your opening hours, and do you do MRI scans?"
```

**Expect** one reply that gives the opening hours *and* says plainly that the MRI question is not in
the clinic's knowledge base and has been forwarded to staff. Before this phase the same message
produced the abstention alone.

Check the record: two outcomes, position 0 `answered` with citations, position 1 an abstention with
`answer: null` and no citations. Check the log: `outcome` is `merged`, and `abstention_message` is
**absent** — this turn answered something.

## 2. The gap is not softened  *(US2, FR-021)*

Read the reply from scenario 1 again, looking only at the MRI half. It must not say "I'll
check", "someone will get back to you shortly with that" beyond the fixed three things, or imply the
answer is elsewhere in the reply. Re-run the same message two or three times: this is a model-obeyed
constraint, so a single pass is not evidence.

## 3. The answer does not stretch to cover the gap  *(US2, FR-022, SC-003)*

```bash
scripts/dev-chat.sh say $CHAT "Do you have parking, and how much does parking cost?"
```

Two questions about one subject, where the corpus answers the first — the hours-and-location entry
names the garage three minutes away — and says nothing about what it costs. **Expect** the parking
answer intact, and the cost named as unanswered, not inferred from the availability answer.

## 4. The gap is named in the assistant's own words  *(FR-012a, SC-002a)*

Compare what you typed with what came back. The reply must let you tell *which* of your questions
went unanswered, and must **not** quote the classifier's restatement verbatim. Confirm the
restatement is what the log carries on `intent.classified` and what the console shows staff — the
two audiences get the same fact in different forms, deliberately.

## 5. Staff see the question that failed  *(US3, SC-012)*

Open the staff pane at <http://localhost:5173>, pick the conversation **by name**. The reply from
scenario 1 shows one block per request: the answered one with its citations, the unanswered one with
its question verbatim and a line saying it went to staff. The patient message above it carries the
`corpus_could_not_answer` mark, exactly as before.

Then check the patient pane: no citations, no verdicts, no question labels (FR-042).

## 6. Two gaps, one escalation  *(FR-030, FR-031, SC-006)*

```bash
scripts/dev-chat.sh say $CHAT "Do you do MRI scans, can I get a prescription refill here, and what are your opening hours?"
```

**Expect** one reply serving the hours, one gap naming both other questions, **one** escalation, and
three outcomes stored. Confirm in the log that only one `escalation.raised` line appears for the
turn.

## 7. Everything abstains — nothing changed  *(FR-013, SC-010)*

```bash
scripts/dev-chat.sh say $CHAT "Do you do MRI scans, and can I get a prescription refill here?"
```

**Expect** the constant abstention message, byte for byte, with **no composing call**. In the log,
`outcome` is `faq`, `abstention_message` is present, and there is no `compose` span. This is the one
reply the design keeps model-free, and it must stay that way.

## 8. A single request pays nothing  *(US5, FR-061, SC-009)*

```bash
scripts/dev-chat.sh say $CHAT "What are your opening hours?"
```

**Expect** a streamed reply, one outcome, no composing call — identical to before this phase. Then
repeat with a booking message and a "thanks", and confirm each stores `request_outcomes = null` and
behaves exactly as it did.

## 9. A degraded answer is marked on its own request  *(FR-043)*

Force a reranking failure (point `RERANK_MODEL` at a value the provider rejects, or block the
rerank host) and
send scenario 1's message again. **Expect** the degraded marker on the answered block only, the gap
untouched, and no extra escalation — a dependency outage is not a corpus gap.

---

## When you are done

```bash
make services-down        # never pkill -f "chat.main"
```

## What this walk-through cannot tell you

Whether the composer's three constraints hold *in general*. Nine scenarios run once are anecdotes
about a model. The committed pairs under `evaluation/` and the procedure beside them are what FR-071
requires, and the stored parts plus the stored reply (FR-040a, FR-040b) are what make SC-004a
checkable on traffic nobody was watching.
