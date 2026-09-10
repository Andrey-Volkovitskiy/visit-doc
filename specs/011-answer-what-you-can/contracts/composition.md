# Contract: Composing a reply that answers some requests and not others

Part counting, the collapse rule, the gap block, and the three constraints. Covers FR-010–FR-014,
FR-020–FR-026, FR-060, FR-061.

---

## 1. How many parts a turn has

**At routing time** (`_expected_parts`, unchanged): one per FAQ request, one per other selected
specialist, plus one if a not-authorized notice is required. This decides whether the specialists
stream or collect, and it is an **upper bound**.

**After the specialists have run** (`_actual_parts`): the FAQ half contributes

```
answered_count + (1 if any request abstained else 0)
```

plus the same non-FAQ parts. With *k* FAQ requests of which *m* abstained that is `k − m + 1` for
`m ≥ 1` and `k` for `m = 0` — never more than `_expected_parts`, which is what keeps the existing
`compose.unexpected_parts` guard meaningful (research #4).

| Turn | Expected | Actual | Path |
|---|---|---|---|
| 1 request, answered | 1 | 1 | streams; no composing call |
| 1 request, abstained | 1 | 1 | streams the constant message; no composing call |
| 2 requests, both answered | 2 | 2 | collect → compose |
| 2 requests, 1 answered | 2 | 2 | collect → compose **(new behaviour)** |
| 2 requests, both abstained | 2 | 1 | collect → **collapse**, constant message, no composing call |
| 1 request abstained + booking | 2 | 2 | collect → compose |

---

## 2. The collapse rule

A turn whose actual parts is 1 while the route expected more emits that single part where the merge
would have been, with **no composing call** (1g FR-051a, this phase's FR-013). After this phase the
one route that reaches it with an FAQ half is the all-abstained turn, whose part is
`_ABSTENTION_MESSAGE` verbatim.

That constant is therefore the only abstention wording a patient ever sees unaccompanied, and it is
never paraphrased: a stopping reply makes no claim about the clinic, which is the only thing
retrieval could have grounded.

---

## 3. The composing prompt

### Answered blocks (unchanged)

One per answered request, each naming the question it answers:

```
Answer to the question "<question>":
<answer text>
```

### The gap block (new, replaces the old single "no confident answer" block)

One block however many requests fell into it (FR-012):

```
NO CONFIDENT ANSWER for these questions:
- "<question of the first unanswered request>"
- "<question of the second…>"
Say plainly that the clinic's knowledge base does not have this information, that these
questions have been forwarded to staff who will follow up, and that you can still help
with anything else in the meantime. Refer to what each unanswered question was about in
your own words — do NOT quote the wording above back to the patient, which is a
machine restatement and not what they wrote.
```

The instruction is in the block as well as in the system prompt because the input slice and the
instruction fail independently — the same reason 1g put the booking specialist's "you hold no clinic
knowledge" rule in its prompt as well as in its input.

### Booking and notice blocks

Unchanged.

---

## 4. The three constraints (system prompt)

Added to the existing list, which keeps every clause it has (FR-025):

1. **Never soften a gap.** A question labelled as having no confident answer has none. Do not
   promise to look into it, do not suggest when staff will reply, do not imply the answer is
   elsewhere in the reply, and do not turn it into a partial answer.
2. **Never extend an answer to cover a gap.** A claim given for one question answers that question
   only. Two questions about the same subject are still two questions.
3. **Name each gap in your own words**, so the patient can tell which of their requests went
   unanswered — never by quoting the restatement.

Each prevents a specific failure: (1) the confabulation the abstention path exists to stop, now with
an answered sentence beside it inviting a softer one; (2) an answer about parking becoming an answer
about parking fees by adjacency; (3) a reply whose gap is so generic the patient cannot tell what was
missed, or so verbatim it reads as a transcription fault.

---

## 5. Failure

A failure of the composing call fails the **whole turn**, unchanged: `TurnPipelineError`, no reply
stored, one `assistant_failed` escalation, nothing partial delivered — however many requests had
already been answered (FR-026). The generation already paid for is a sunk cost. Nothing has checked
the parts against each other, and this is the one path where an unchecked part sits beside a gap.

Truncation is unchanged: the reply is streamed as it stands and recorded as `compose.truncated`. The
token budget is unchanged too — `(MAX_SEGMENTS + 1) × 1024` still bounds the worst legal merge, and
the gap block adds a sentence, not a part.

---

## 6. Test obligations

| # | Assertion |
|---|---|
| C1 | 2 requests, 1 answered → 2 parts, a composing call, and the answered text present in the reply. |
| C2 | 2 requests, both abstained → 1 part, **no** composing call, reply is `_ABSTENTION_MESSAGE` byte for byte. |
| C3 | 1 request, answered → streams, no composing call, identical to today (FR-061). |
| C4 | 3 requests, 1 abstained → one gap block listing one question; 3 requests, 2 abstained → one gap block listing two. |
| C5 | The gap block's questions are the classifier's restatements, in position order. |
| C6 | `_actual_parts` never exceeds `_expected_parts` for every (k, m) with k ≤ 3 — a property test. |
| C7 | A composing failure on a turn with two answered requests stores no reply and raises one failure escalation. |
| C8 | The composing prompt contains all three constraints and every existing one (a prompt-content test, as the booking prompt already has). |
| C9 | *(model-obeyed, `evaluation/`)* On the committed answered/abstained pairs: no softened gap, no extended answer, no quoted restatement, and a reader can name which request went unanswered. |
