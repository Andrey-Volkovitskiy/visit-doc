# Contract: Log Events

This is the phase's deliverable for US4 and the input contract Phase 2 will compute metrics from, so
field names are part of the contract, not an implementation detail. All events go through the
existing structlog chain (`shared-logging`) and inherit `turn_id`/`node` from the bound context —
no event repeats them.

**Not every turn raises every event.** A turn whose session publishes no live revisions issues no
search and runs no gate, so it raises neither `faq.retrieval_completed` nor `faq.similarity_gate` —
`turn.retrieval_skipped_empty_corpus` records that case instead. Emitting them with empty payloads
would put "nothing to search" and "the floor rejected everything" back together in the log after the
verdict had told them apart. `faq.rerank_gate` is likewise absent whenever reranking did not run.

## `faq.retrieval_completed` — INFO (FR-027, FR-027a, FR-027b)

| Field | Type | Notes |
|---|---|---|
| `pool_size` | int | Configured pool size |
| `pool_returned` | int | What the search actually returned; may be smaller |
| `candidates` | list | Descending `similarity_score` order |

Each candidate: `entry_id`, `chunk_index`, `similarity_score`, `considered` (bool), `chunk_text`,
`text_truncated` (bool).

`considered` means **the similarity gate kept it** — read from the gate's survivors, not from the
candidate's position in the pool. The two agree only while every candidate inside the cap also
clears the floor; when fewer do, a positional flag reports chunks as considered that the floor threw
out, and prints their full text as though it had reached the prompt.

- `considered: true` → full `chunk_text`, `text_truncated: false`.
- `considered: false` → `chunk_text` cut to the first **200** characters; `text_truncated` is `true`
  **only if it was actually cut** (FR-027b — a chunk shorter than the limit is logged whole and must
  not be marked truncated).

## `faq.similarity_gate` — INFO (FR-028)

| Field | Type |
|---|---|
| `floor`, `cap`, `pool_returned` | float, int, int |
| `kept` | list of `{entry_id, chunk_index, similarity_score}` |
| `dropped_by_floor` | same shape |
| `dropped_by_cap` | same shape |

`pool_returned` is what the gate actually saw, and carries the same name and meaning it has on
`faq.retrieval_completed` above. It is deliberately not called `pool_size`, which on that event
names the *configured* ceiling: one field name reading as two different quantities across sibling
events is exactly what a metrics consumer cannot see.

Two separate drop lists, never one with a reason field: one says the bar is too high and the other
says it is too low, and a reader tuning the floor is only ever looking at one of them.

## `faq.reranking_completed` — INFO (FR-029)

`model`, `candidate_count`, `duration_ms`, and `scores`: `{entry_id, chunk_index, similarity_score,
rerank_score}` per candidate. Carrying both scores here is what makes a disagreement between the two
stages visible, which is the phase's whole thesis.

## `faq.reranking_unavailable` — **ERROR** (FR-031, FR-010)

`reason` (one of `timeout`, `transport`, `refused`, `rate_limited`, `unusable_response`,
`unexpected`), `error`, `timeout_seconds`, `candidate_count`.

`unexpected` covers anything the provider did not raise as one of its own errors — a bug here, a
test guard, something in the event loop. It exists because the alternative is worse: filing an
unknown failure under a cause it was not turns a "look at this" into "the vendor is down", and this
field is read as a metric. A run of `unexpected` means the classifier met something nobody
anticipated, which is exactly what an operator should be told.

The reason is classified on the exception's **type**, never on words in its message. A substring
test is not a classifier — "rate" appears inside "strategy", "auth" inside "author" — so a message
that merely mentions a file path would decide the reason. The only ERROR-level event this phase adds. A run of
these beside a run of `answered_unreranked` verdicts is the signal that distinguishes a degraded
dependency from a corpus problem (spec Edge Cases).

## `faq.rerank_gate` — INFO (FR-030)

`floor`, `cap`, and the same three lists as `faq.similarity_gate`, with `rerank_score` in place of
`similarity_score`. No pool field: this gate is fed the previous gate's survivors, not a search
result. Not emitted when reranking did not run.

## `faq.verdict` — INFO (FR-032)

`verdict`, `survivor_count`, and — for an abstention — `blocked_gate` (`empty_corpus`, `empty_pool`,
`similarity_floor`, `rerank_floor`), plus `best_similarity_score` and `best_rerank_score`. One per
floor, not one score for the turn: the two floors are tuned separately, so a single number could not
say which bar was too high. Each is the best its whole stage saw — the pool before the similarity
floor, every scored candidate before the rerank floor — which is what separates "nothing was close"
from "something was close and the floor was too high", the question FR-028's two drop lists answer
for a gate and this answers for the turn. `best_similarity_score` is `None` when nothing was
retrieved at all; `best_rerank_score` is `None` whenever no chunk carries a rerank score — the
reranker did not run, or it failed — never `0.0`, which is the cross-encoder judging a chunk
irrelevant.

## `turn.completed` — INFO, existing event, changed (FR-025, FR-025a)

- `grounded` → **removed**
- `faq_verdict` → added
- `abstention_message` → still set on any abstention (unchanged behavior, four triggers now)
- `citations` → each entry now carries **both** `similarity_score` and `rerank_score`;
  `rerank_score` is **absent/null** on an `answered_unreranked` turn — never `0.0`, never a copy of
  the similarity score (FR-025a).
- `outcome` → the existing single-specialist summary gains the abstention verdicts in place of the
  bare `"abstained"`.

**SC-004b**: this one line must stand alone — verdict, survivors, and both scores — with no other
event required.

## Removed

`turn.groundedness_verdict` (carried the boolean) is deleted, superseded by `faq.verdict`. `faq.retrieved`
is superseded by `faq.retrieval_completed`, which carries strictly more.

## Redaction (FR-033)

No new exemption. Chunk text is clinic FAQ content and already flows through today's
`turn.retrieval_completed`; it passes through the same processor chain, including secret redaction,
with no per-event opt-out.
