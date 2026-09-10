# Contract: Log Events

This extends spec 008's field contract
(`specs/008-reranked-retrieval-pipeline/contracts/log-events.md`), in the same change that changes
the fields (FR-063). Everything there still holds; this document says only what Phase 1g adds. All
events go through the existing structlog chain and inherit `turn_id`/`node` from the bound context.

> **Superseded in part by Phase 1h** —
> [`specs/011-answer-what-you-can/contracts/log-events.md`](../../011-answer-what-you-can/contracts/log-events.md).
> The six per-request retrieval events below, and the `segment` join key, are unchanged. What
> changed is `turn.completed`: `faq_verdict`, the turn-level `citations` list and `answer_source`
> are gone, `segment_verdicts` is superseded by `request_outcomes` (which carries it plus the
> evidence), `outcome` names the turn's shape rather than a verdict, and `abstention_message` is set
> only when every request abstained. Read that document beside this one before writing a query.

## The join key

**A request is identified by its position in the patient's message** — `segment`, a 0-based integer.
Its *text* is carried once, on `intent.classified`, and never repeated on the retrieval events
(FR-060). A reader joins the two on `segment` within a turn.

Position is bound with `structlog.contextvars` for the whole of a segment's task, the same mechanism
that already puts `turn_id` and `node` on every event, and it is task-scoped — so concurrent segment
tasks cannot see each other's binding (research D10).

## `intent.classified` — INFO (FR-061)

| Field | Type | Notes |
|---|---|---|
| `intents` | list | Unchanged — the derived label list, so existing readers keep working |
| `segments` | list | **New.** One entry per segment: `{position, intent, text}`, in message order |
| `cap_bound` | bool | **New.** True when the segmenter had to combine requests to fit the cap (FR-007) |

The one event carrying segment text, and the one every per-segment event is read against.

## Phase 1e's six events — each gains `segment`

| Event | Change |
|---|---|
| `faq.retrieval_completed` | `segment` added; every other field unchanged |
| `faq.similarity_gate` | `segment` added |
| `faq.reranking_completed` | `segment` added |
| `faq.reranking_unavailable` | `segment` added — a degraded segment is named, not the turn |
| `faq.rerank_gate` | `segment` added |
| `faq.verdict` | `segment` added; this is the **segment's own** verdict, not the turn's summary |

`turn.retrieval_skipped_empty_corpus` gains `segment` on the same basis.

**Which events a turn raises is unchanged**, per segment: a segment whose session publishes no live
revisions raises neither `faq.retrieval_completed` nor `faq.similarity_gate`, and `faq.rerank_gate` is
absent wherever reranking did not run. Emitting them empty would put "nothing to search" and "the
floor rejected everything" back together after the verdict had told them apart.

**Two concurrent segments interleave in the log.** That is expected, and `segment` is what makes it
readable — the ordering of lines carries no meaning, the field does.

## `turn.completed` — INFO (FR-062)

| Field | Type | Notes |
|---|---|---|
| `faq_verdict` | str \| null | Unchanged — the turn's **summary** verdict (data-model.md §5.1) |
| `segment_count` | int | **New.** 1–3 |
| `segment_verdicts` | list | **New.** `{position, verdict}` per FAQ segment, in segment order |
| `answer_source`, `citations`, `booking_outcome`, `notice_included`, … | | Unchanged |

`segment_verdicts` is what makes the summary lossless *as a record* even where the summary field
itself is lossy: two segments that abstained at different gates are both recoverable here (FR-044).

## Node spans

`node.completed` for the FAQ node gains `segment_count` and `segment_answers` — `{position,
question, verdict, answer_text}` per request — and keeps its existing per-turn fields. `answer_text`
on the span itself is the half's own single text, and is null once the half answered more than one
request; `segment_answers` is what carries the words in that case. Without it, a merged turn records
only what the composing model wrote, and a bad merge cannot be told from a bad half.

The node's span stays one span for the whole FAQ half: the fan-out is inside it, and a span per
segment would claim a graph node per segment that does not exist.

## Where a reply ran out of room

Three warnings, each raised where a generation call stopped because it hit its own cap rather than
because it was finished. `small_talk.truncated` (spec 009) already covered the fourth such call;
together they complete the set, so no path can clip a reply the log does not name.

| Event | Level | Fields | Raised when |
|---|---|---|---|
| `faq.truncated` | warning | `max_tokens`, `answer_chars` | **New.** One request's answer hit the FAQ cap. Carries `segment` from the bound context, so it names which request |
| `compose.truncated` | warning | `max_tokens`, `answer_chars` | **New.** The merged reply hit the merge's cap |
| `booking.truncated` | warning | `iteration`, `max_tokens`, `text_chars`, `tool_names` | **New.** One iteration of the booking loop hit its cap — the iteration is named because a truncated `tool_use` block reaches the handler as an argument error |

None of the three fails the turn and none calls a person: the tokens have already reached the
patient, so there is nothing left to fail cleanly, and an answer that ran long is not one the
corpus could not answer. The record is what is owed — a clipped reply otherwise reads as a short
complete one.

`compose.truncated` matters most of the three: the merge is the only step whose input is other
steps' output, so its cap has to hold every part at once (`MAX_SEGMENTS` question answers plus a
booking reply, each written under its own cap), and the halves it merged cannot report a cut that
happened after they finished.

The **classifier** is the one call that raises no event of its own for this, because it does not
reach the patient: a response that ran out of room fails classification, and the turn falls back to
the unsplit message exactly as it does for any other invalid one. What the cap buys there is the
`error_detail` on `intent.classification_failed`, which now names the cap rather than reporting the
truncated body as malformed JSON — the two need opposite fixes and were previously indistinguishable.

## What Phase 2 gets from this

- Segmentation accuracy, computed from `intent.classified.segments` against the labelled sets.
- Per-question retrieval behaviour: which chunks and scores belonged to which request (SC-010).
- The share of turns where the cap bound, which is what the cap of 3 is argued up or down against
  (FR-007).
- The inputs for Phase 1h's per-request verdict, already recorded before the verdict moves.
