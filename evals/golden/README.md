# The golden set

The labelled dataset Phase 2a exists to produce (`docs/ROADMAP.md`). 135 patient messages carrying
190 labelled requests, consolidated from the four phases that each shipped labelled data with a
written procedure and deliberately no runner.

**It is data. There is no runner here, and nothing imports it yet.** Computing metrics over it is
Phase 2b and gating CI on those metrics is 2c; building either inside 2a would start the next phase
inside this one, which is the mistake 1e, 1f, 1g and 1h each declined to make.

## Why it lives here and not under `specs/`

The four seed sets belong to `specs/` and should stay there: they are frozen records of a shipped
phase, excluded from ruff and mypy along with the rest of `specs/**`. This set is the opposite kind
of thing. 2b reads it, 2c gates every build on it, and it is re-labelled whenever the corpus moves —
it is living, checked, and outlives the spec that introduces it. `.claude/CLAUDE.md` already draws
that line for code ("Do not put application code under `specs/`"); the same reasoning puts a living
dataset outside it.

## Files

| File | What it is |
|---|---|
| `cases.json` | The set. One array of cases; `schema.json` is authoritative for the shape. |
| `schema.json` | JSON Schema for a case. All 135 cases validate against it. |
| `corpus.json` | The pinned FAQ corpus every `cites` label names, with a `sha256` over the entry texts. |
| `PROVENANCE.md` | Where each case came from, what re-labelling the corpus changes forced, and what did not survive consolidation. |

## The case shape

```json
{
  "id": "G120",
  "family": "partial-serving",
  "message": "What are your clinic hours, and do you validate parking?",
  "requests": [
    {"intent": "faq_question", "gist": "clinic hours", "answerable": true, "cites": ["hours-location"]},
    {"intent": "faq_question", "gist": "parking validation", "answerable": false, "cites": []}
  ],
  "source": "011/setA#A1"
}
```

**The label attaches to a request.** `requests` is the expected segmentation: how many, in what
order, and each one's intent. Array order *is* position — there is no `position` field, because a
second way of saying the same thing is a second thing that can be wrong.

What is scored and what is not:

- **`intent` is scored.** It must be one of `chat.domain.schemas.IntentLabel`'s eight values.
- **`gist` is not scored.** A segment has many valid restatements; exact-matching the classifier's
  wording would measure phrasing rather than segmentation. It is there so a human reading the file
  knows which request is which.
- **`answerable` is two-valued, not six.** `FaqVerdict` has four abstentions, but they are
  identical in behaviour and the schema's own docstring forbids branching on which one it is — and
  no hand-labeller can tell a similarity-floor stop from a rerank-floor stop without running the
  pipeline. So the label is `FaqVerdict.answered` and nothing finer.
- **`cites` is a set, not a ranking.** It names the corpus entries the answer must rest on.
  Non-empty exactly when `answerable` is true, which is the same invariant `RequestOutcome`
  enforces in code.
- **`tools` is a required subset, not a sequence.** A `check_availability` that precedes a
  `book_appointment` is not a miss; a `book_appointment` that never happens is.

## Derived, not stored

Nothing in a case says which escalation a turn should raise, because the requests already say it.
Deriving it at read time is what keeps the two from disagreeing:

| A request with this intent | contributes this `EscalationReason` |
|---|---|
| `urgent_condition` | `URGENT_CONDITION` |
| `distress` | `DISTRESS` |
| `call_staff` | `PATIENT_ASKED_FOR_PERSON` |
| `booking_for_another` | `BOOKING_FOR_ANOTHER_PERSON` |
| `unknown` | `NOT_AUTHORIZED` |
| `faq_question` with `answerable: false` | `CORPUS_COULD_NOT_ANSWER` |
| anything else | none |

A turn raises **every** cause its requests contribute; the *mark* is the first of them in
`escalation._PRECEDENCE` order, and whether the assistant falls silent is `_SILENCING`'s separate
question. `ASSISTANT_FAILED` is the one cause no case can carry: it is a property of a broken run,
not of a message, and a golden set cannot label it.

## The corpus pin

Every `cites` label is meaningless without the text it names, and the corpus is editable —
`DEFAULT_FAQ_ENTRIES` changed three times between 1h shipping and this set being written, and two
of those changes moved labels (see `PROVENANCE.md`). `corpus.json` therefore pins a snapshot with a
`sha256` over the entry texts. **When that hash stops matching `DEFAULT_FAQ_ENTRIES`, the labels on
the changed entries are stale until someone re-checks them** — re-take the snapshot and work through
the entries that moved, the way `PROVENANCE.md` records it being done last time.

## What is in it

| | |
|---|---|
| Cases | 135 |
| Labelled requests | 190 |
| One request / two / three | 84 / 47 / 4 |
| FAQ requests answerable / gap | 93 / 24 |
| Corpus entries cited | 9 of 9 |
| Intents covered | 8 of 8 |
| Escalation causes covered | 6 of 7 (`assistant_failed` is not labelable) |

Fourteen families, each a thing that can go wrong: `single-faq-answered`, `single-faq-gap`,
`off-topic-and-clinical`, `small-talk`, `pleasantry-plus-request`, `not-authorized`,
`safety-and-authority`, `compound-faq`, `mixed-faq-booking`, `segmentation-edges`,
`single-request-that-looks-multi`, `overriding-segment`, `partial-serving`, `changed-corpus`.

The count exceeds the 50–100 the roadmap names. That band was written before the seed was counted;
consolidating four phases' data produced 136, and one was then dropped by decision (see
PROVENANCE.md), leaving 135. Either the band moves or the set is trimmed further — it is a decision,
not an accident, and it is recorded here rather than resolved silently.

## A label that needs a measurement, not a reading

- **G020** (`Do you accept Blue Cross for dental implants specifically?`) is labelled a gap, and it
  is one of the three cases 1e's calibration relied on to prove the *rerank* floor does work: it
  clears the similarity floor and must be stopped later. The plans entry's text changed since that
  was measured, so this label is the most likely of any in the set to be wrong. Re-measure it before
  trusting it.
