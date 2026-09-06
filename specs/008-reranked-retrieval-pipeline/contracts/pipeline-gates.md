# Contract: The Two Gates and the Verdict

`chat/rag/pipeline.py`. Pure functions — no I/O, no clients, no clock. Every requirement about
floors, caps, ordering, and outcomes lives here, which is what makes the table below testable as a
table.

## `GateResult`

What a gate returns. Frozen dataclass, three fields, all `list[ScoredChunk]`:

| Field | Meaning |
|---|---|
| `kept` | The survivors, in the order that gate scores by |
| `dropped_by_floor` | Rejected for scoring below the floor |
| `dropped_by_cap` | Cleared the floor, lost to the cap |

**Two rejection lists, not one list with a reason field.** A reader tuning a threshold looks at
exactly one of them: dropped-by-floor says the bar is too high, dropped-by-cap says it is too low
(FR-028). Collapsing them into one list would put those two questions back together after the
requirement had separated them.

`kept` is also what the retrieval log's `considered` flag is derived from — from the gate's own
survivors, never from a candidate's position in the pool, since the two agree only while every
candidate inside the cap also clears the floor.

## `apply_similarity_gate(pool, *, floor, cap) -> GateResult`

- Keeps chunks with `similarity_score >= floor` (**inclusive**, FR-009).
- Of those, keeps the `cap` highest by `similarity_score`.
- Floor first, then cap (FR-009).
- `kept` is in descending `similarity_score` order.
- `pool` empty → every list empty. Never raises.

## `apply_rerank_gate(scored, *, floor, cap) -> GateResult`

- Input chunks all carry a non-`None` `rerank_score`. One without a score is unscored, not passing,
  so it is dropped by the floor.
- Keeps `rerank_score >= floor` (inclusive), then the `cap` highest.
- `kept` is in descending `rerank_score` order — **the reranked order wins** for the prompt context
  and the citation order (spec Edge Cases).
- `scored` empty → every list empty. Never raises.

## `decide(observed, considered, reranked, *, corpus_empty) -> PipelineOutcome`

The single place a verdict is assigned. `reranked` is `None` when reranking did not run or failed.

| # | corpus_empty | observed | considered | reranked | Verdict | survivors |
|---|---|---|---|---|---|---|
| 1 | `True` | `[]` | `[]` | `None` | `abstained_empty_corpus` | `[]` |
| 2 | `False` | `[]` | `[]` | `None` | `abstained_empty_pool` | `[]` |
| 3 | `False` | non-empty | `[]` | `None` | `abstained_similarity_floor` | `[]` |
| 4 | `False` | non-empty | non-empty | `None` (failed/timed out) | `answered_unreranked` | `considered` |
| 5 | `False` | non-empty | non-empty | `[]` | `abstained_rerank_floor` | `[]` |
| 6 | `False` | non-empty | non-empty | non-empty | `answered` | `reranked` |

Row 4 versus row 5 is the distinction the fallback turns on, and it is why `reranked` is
`None`-vs-`[]` rather than a list that might be empty for either reason: `None` means *no scores were
obtained*, `[]` means *scores were obtained and none cleared the floor*. One value, one meaning — a
single empty list standing for both would make FR-010 and FR-008 indistinguishable, and the
difference between them is whether the patient gets an answer.

Rows 1, 2 and 3 are the same reasoning applied to the retrieval half: all three abstain identically
and are identical to the patient (FR-021a), and only the record differs — but the record is what
names the fix. An empty corpus wants entries; an empty pool means the session publishes live
revisions the search matched no chunk of, so the index is behind the rows and a re-index is the fix;
only row 3 is a floor rejecting something, and only row 3 is evidence about where the floor sits
(FR-021b). Reading row 2 as row 3 would have an operator lowering a floor that never ran.

**Invariant** (asserted in tests, one test per clause):
- `survivors` non-empty **iff** verdict ∈ {`answered`, `answered_unreranked`}
- `len(survivors) <= rerank_cap` when verdict is `answered`
- `len(survivors) <= similarity_cap` when verdict is `answered_unreranked`
- `set(survivors) ⊆ set(considered) ⊆ set(observed)`
- every survivor of an `answered` verdict has a non-`None` `rerank_score`
- every survivor of an `answered_unreranked` verdict has a `None` `rerank_score`

## Boundary cases the tests must cover

| Case | Expected |
|---|---|
| Score exactly equal to a floor | Passes (inclusive, FR-009) |
| More above the floor than the cap allows | Highest `cap` kept; the rest recorded as dropped-by-cap, not dropped-by-floor (FR-028) |
| Pool smaller than the pool size (corpus is small) | Not an error; `observed` is simply short |
| Exactly one chunk clears the similarity floor | Still goes to reranking — a shortlist of one is where a bi-encoder is least trustworthy (spec Edge Cases) |
| Reranker returns a different order than similarity did | Reranked order wins |
| Discarded chunk shorter than the truncation length | Logged whole, not marked truncated (FR-027b) |
