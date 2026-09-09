# Contract: Segmentation

What the one classification call returns after this phase, what the prompt must make it hold to, and
what counts as an invalid result. The call itself is unchanged: same cheap model, same single round
trip, same bounded history, same JSON Outputs mechanism (FR-002, FR-071).

## Response shape

```jsonc
{
  "segments": [
    { "intent": "faq_question", "text": "What is the clinic's address?" },
    { "intent": "booking",      "text": "What dentist slots are free tomorrow?" }
  ],
  "cap_bound": false
}
```

- `segments`: 1–3 items, in the order the requests appear in the patient's message (FR-003).
- `intent`: one member of the existing label set. **No label is added or removed by this phase.**
- `text`: a non-empty standalone restatement of that one request.
- `cap_bound`: true when the message carried more independently answerable requests than the cap
  allows and the segmenter had to combine some of them (FR-006a, FR-007). **Reported by the
  segmenter, not inferred**: three segments is not evidence the cap bound — a message with exactly
  three requests fits — and inferring it from the count would mark every full turn as truncated. It
  defaults to false, and a model that omits it is saying nothing was combined.

The turn's intent set is `[s.intent for s in segments]` — a derived view, not a second field
(FR-001, [data-model.md §2](../data-model.md)).

## Request schema

| Constraint | Where | Why |
|---|---|---|
| **No** `minItems`/`maxItems` | The JSON Outputs request schema | The API rejects array bounds in a JSON Outputs schema (`For 'array' type, property 'maxItems' is not supported`), and Pydantic emits both from the field's own bounds — so both are stripped when the request schema is built. A regression here is a 400 on every classification call, on every turn, which is why a test asserts their absence. |
| The cap, in the prompt | Segmentation rule 5 | Where the cap is actually stated to the model (FR-006). |
| `CLASSIFICATION_FAILED` excluded from the `intent` enum | Same | Unchanged from today — that value is orchestration's to assign, never the model's. |
| `cap_bound` optional, default `false` | Both | A field the segmenter sets when it combined requests; never derived from the segment count.
| `max_length=3`, `min_length=1` | The Pydantic model | Backstop. A schema-level regression must fail loudly here rather than silently violate the contract — the same construction spec 009 used for the excluded enum value. |

## The rules the prompt must carry

Two limits hold over all of them, and the prompt states them before the list: **never return more
than three segments**, and **never leave out something the visitor asked for** — every request must
be inside one of the segments returned, even where that means one segment carrying two of them. The
second is rule 5's principle stated generally: it is what stops an awkward clause being dropped
instead of split.

1. **Segments are requests.** A greeting, a thank-you or a reaction beside a real request produces no
   segment of its own and is not folded into a neighbour's text (FR-005b). A message that requests
   nothing at all is one segment, labelled `small_talk` (FR-005c).
2. **Each segment stands alone.** Pronouns, ellipsis and shared subjects are resolved against the
   message and the history: "Do you have parking, and is it free?" yields a second segment reading
   *"Is parking free?"*, never *"is it free?"* (FR-004, FR-004a).
2a. **A follow-up clause is its own request.** A clause that refines, contrasts with, or asks the
   other side of the first request needs the same restating as any other segment: "what should I
   bring, and is that different for a returning patient?" becomes *"what should I bring?"* and
   *"what should a returning patient bring?"*; "do you take Medicare? what if you do not?" becomes
   *"do you take Medicare?"* and *"what happens if my insurance is not accepted?"*. Dropping such a
   clause loses a question the patient asked, which is the failure this rule was written for: it was
   added after the first live measurement, where two messages of exactly this shape came back as one
   segment with the second clause gone (`../evaluation/procedure.md`, runs 1–3).
3. **Restate, never add.** A segment may not introduce a constraint, specialty, date, practitioner or
   symptom that the message and history do not carry. Turning "what should I bring?" into "what
   should I bring to a first cardiology visit?" invents the constraint that decides what is retrieved
   (FR-004b).
4. **Split conservatively.** One request is one segment. A message splits only where its parts are
   independently answerable — different answers, not merely different sentences (FR-005, FR-005a).
   Length, punctuation and repetition are not split points. Under-splitting is today's behaviour;
   over-splitting is a new failure.
5. **Combine above the cap.** A message carrying more than three independently answerable requests
   yields three segments, with the least separable combined. Nothing the patient asked may be absent
   from every segment (FR-006a).
6. **Read against the conversation.** Unchanged from Phase 1f — the same words are a request in one
   context and a pleasantry in another, and the classifier already receives the bounded history it
   needs to tell them apart (FR-006 of spec 009).

## Invalid results

All three raise `ClassificationFailedError` and take the **existing** fallback: the turn proceeds
down the FAQ path with one synthetic segment carrying the whole trailing message, exactly as a failed
classification does today (FR-008, and spec 009's FR-009 preserved).

| Case | Why it is invalid |
|---|---|
| More than three segments | The cap is the contract; trimming would silently drop a request the patient made. This is where the cap is enforced, since the schema cannot express it. |
| A segment whose `text` is empty or whitespace | A blank question is not a request, and would be retrieved for as one. |
| A segment whose `intent` is `CLASSIFICATION_FAILED` | Orchestration's value only — re-checked on the parsed result, as today. |

A failure is **not** evidence about what the message was: it must not yield a small-talk reply and
must not take the not-authorized escalation route.

## What a turn logs about this

`intent.classified` carries the segmentation itself — position, intent and text per segment, plus
whether the cap bound. It is the one event carrying segment text, and the event every per-segment
retrieval event is read against. See [log-events.md](./log-events.md).

## Verification

- Behaviour: offline, against a stubbed classifier returning a fixed segmentation (FR-082).
- Quality (rules 1–5, 2a included): by hand, once, against the committed labelled sets, with the result recorded
  beside them (FR-080, FR-081). Targets: SC-003 (≥90% expected segmentation on multi-request
  messages), SC-004 (≥95% single-segment on single-request messages), SC-005 (0 invented
  constraints), SC-006 (0 turns above the cap).
