# Quickstart: One Message, Several Requests (Phase 1g)

A manual walk-through of the three defects this phase fixes and the case it must not disturb. Ten
scenarios, run by hand against a live stack — the unit tier proves the behaviour offline against a
stubbed classifier, and this is where a person watches the real classifier actually split a sentence.

Patient turns here spend real Claude and Voyage calls, which is expected for manual verification (see
`docs/testing-strategy.md`).

## Prerequisites

```bash
make services-up          # chat :8000, scheduler :8001, frontend :5173, pids/logs under .run/
make services-status
make migrate              # only if the dev databases are behind
```

Two windows help: one driving `scripts/dev-chat.sh`, one following the log.

```bash
scripts/dev-chat.sh session          # mint a session + its first chat; prints the chat id
CHAT=<the id it printed>
tail -f .run/chat.log | grep -E 'intent.classified|faq\.|turn.completed'
```

The session starts holding the default corpus, so the FAQ scenarios below work without seeding.
Where a scenario needs an entry the corpus lacks, add one:

```bash
scripts/dev-chat.sh faq "Parking is free for patients for the first two hours."
```

## Reading the log

Every per-request event carries `segment`, a 0-based position; the request's text appears once, on
`intent.classified`. To watch one turn's segmentation:

```bash
grep intent.classified .run/chat.log | tail -1 | python3 -m json.tool
```

To see which chunks belonged to which question:

```bash
grep -E '"event": "faq\.(similarity_gate|rerank_gate|verdict)"' .run/chat.log | tail -6 \
  | python3 -c 'import json,sys
for line in sys.stdin:
    e = json.loads(line)
    print(e["event"], "segment", e.get("segment"), e.get("verdict") or e.get("kept"))'
```

---

## 1. A compound question is answered, not abstained  *(US1, SC-001)*

```bash
scripts/dev-chat.sh say "$CHAT" "What is your address, and what should I bring to a first visit?"
```

**Expect**: one reply answering both halves. `intent.classified` shows **two** segments, both
`faq_question`, each a standalone question. Two `faq.verdict` lines, `segment` 0 and 1, both
`answered`. `turn.completed` carries `segment_count: 2` and a `segment_verdicts` list.

**Before this phase** this message abstained and called a human. That is the defect.

## 2. The two halves keep their own evidence  *(US1, SC-011)*

Same turn as scenario 1.

**Expect**: two `faq.similarity_gate` lines with different `kept` lists — the address chunks under
one `segment`, the preparation chunks under the other. The reply's citations are the union with no
chunk repeated. If one chunk answers both, it appears **once**.

## 3. A question and a booking stop damaging each other  *(US2, SC-002)*

```bash
scripts/dev-chat.sh say "$CHAT" "What is your address, and which dentists have slots tomorrow?"
```

**Expect**: two segments, one `faq_question` and one `booking`. Exactly **one** `faq.verdict`
(`segment` 0) — the FAQ half never saw the slots clause, so it had nothing to abstain on. The reply
answers the address from the corpus and the slots from the scheduler, merged into one message. No
attention mark on the conversation:

```bash
scripts/dev-chat.sh console
```

**Before this phase** the FAQ half abstained on the booking clause and paged a human, and the booking
half invented an address.

## 4. The booking half states no clinic facts  *(US2, SC-002)*

Read the reply from scenario 3 closely. **Expect**: nothing in the appointment half that could only
come from the corpus — no address, price, policy or preparation instruction. The booking specialist
was handed its own segments and told it holds no clinic knowledge.

## 5. One request still costs one path  *(US3, SC-007)*

```bash
scripts/dev-chat.sh say "$CHAT" "I have been meaning to ask for a while now, and I hope this is the right place — what are your opening hours on Saturdays?"
```

**Expect**: **one** segment despite the length and the commas. One `faq.verdict`. `turn.completed`
shows `answer_source: faq`, not `merged`, and there is no composing call in the log. This turn is
today's path byte for byte.

## 6. A repeated request is one request  *(US3, SC-004)*

```bash
scripts/dev-chat.sh say "$CHAT" "What time do you open? When can I come in the morning?"
```

**Expect**: one segment. Two ways of asking one thing have one answer, so they are not independently
answerable and must not retrieve twice.

## 7. Ellipsis is resolved, not copied  *(US1, SC-005)*

```bash
scripts/dev-chat.sh say "$CHAT" "Do you have parking, and is it free?"
```

**Expect**: two segments, the second reading as a standalone question about *parking* — never "is it
free?". Check `intent.classified`; a segment that retrieves nothing on its own is the failure this
rule exists to prevent. Confirm too that neither segment invented a constraint the message never
carried.

## 8. An overriding intent still takes the whole turn  *(US4, SC-012)*

```bash
scripts/dev-chat.sh say "$CHAT" "What should I bring on Friday? Also my chest hurts and I feel faint."
```

**Expect**: the emergency reply and nothing else. **Zero** `faq.` events — nothing was retrieved. The
conversation is silent from the next message, and the console shows the urgent-condition cause on the
patient's message. The what-to-bring question is not answered, deliberately: handing over cleanly
beats answering half a message and then going quiet.

```bash
scripts/dev-chat.sh console
scripts/dev-chat.sh say "$CHAT" "Are you there?"      # stored, marked, unanswered
```

Hand it back before continuing:

```bash
scripts/dev-chat.sh staff "$CHAT" "This is the clinic — I have your message."
```

## 9. A pleasantry beside a request produces no segment  *(US4, SC-004)*

```bash
scripts/dev-chat.sh say "$CHAT" "Hi! Do I need a referral?"
```

**Expect**: **one** segment, the referral question. The greeting produces none and does not appear
inside the segment's text — a greeting in the retrieval query is exactly the noise the standalone
restatement rule removes. The reply is the referral answer, not "Hello!".

Then, for contrast:

```bash
scripts/dev-chat.sh say "$CHAT" "Thanks, that helps!"
```

**Expect**: one `small_talk` segment, one short courteous reply, no retrieval, no mark.

## 10. Two questions, one unanswerable  *(FR-042 — the deliberate limitation)*

Ask one thing the corpus answers and one it does not:

```bash
scripts/dev-chat.sh say "$CHAT" "What are your opening hours, and what is your policy on emotional support animals?"
```

**Expect**: the turn abstains **as a whole** — the opening hours are not delivered — one corpus-gap
escalation is raised, and the console shows one mark. Two `faq.verdict` lines survive in the log with
*different* verdicts, and `turn.completed`'s `segment_verdicts` carries both while `faq_verdict`
carries the first abstaining one.

This is the phase's known limitation, not a defect: serving the answerable half and naming the gap is
Phase 1h, which brings the composer constraint that keeps an abstention from being softened by the
answer beside it.

---

## Tear-down

```bash
make services-down
```

## What this walk-through does not cover

- Segmentation *accuracy* across the labelled sets — that is the committed manual procedure under
  `evaluation/`, measured once and recorded, not a scenario here.
- Anything Phase 1h owns: per-request verdicts, partial serving, and an escalation carrying the
  specific unanswered question.
