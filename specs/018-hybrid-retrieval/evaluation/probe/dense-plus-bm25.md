# Comparison - 01M3WD842TTD1Q9FX8TDRTMFAB -> 01M4WWYG0HASMFNNA04TF92MBT

## Runs

- Base: 01M3WD842TTD1Q9FX8TDRTMFAB [traced] (evals/baselines/01M3WD842TTD1Q9FX8TDRTMFAB), started 2026-10-01T18:57:22.522363+00:00, complete
- New: 01M4WWYG0HASMFNNA04TF92MBT [traced] (/tmp/claude-1000/-home-andrey-visit-doc/f814b892-f188-418f-8249-b1f3ba0beed2/scratchpad/probe/run_supplement), started 2026-10-01T18:57:22.522363+00:00, complete

## Conditions

- retrieval: dense -> dense_plus_bm25
- lexical_cap: None -> 3

## Coverage

Both runs recorded the same 146 cases.

## Band

No band was supplied: movements are reported, not judged. Nothing here says whether a movement is larger than this set moves on its own.

## Alignment and exclusions

| totals | aligned | unaligned | excluded | labelled |
|---|---|---|---|---|
| base | 168 | 0 | 0 | 168 |
| new | 168 | 0 | 0 | 168 |

Alignment held on both sides.

- handed_off_turn: 22 -> 22

## Metrics

### classification

| metric | base | new | movement | cases |
|---|---|---|---|---|
| request_count_accuracy | 1.000 (146 / 146) | 1.000 (146 / 146) | unchanged | none |
| intent_accuracy | 0.994 (167 / 168) | 0.994 (167 / 168) | unchanged | none |
| exact_segmentation_match | 0.993 (145 / 146) | 0.993 (145 / 146) | unchanged | none |

### retrieval

| metric | base | new | movement | cases |
|---|---|---|---|---|
| similarity_hit_at_1 | 0.953 (82 / 86) | 0.953 (82 / 86) | unchanged | none |
| similarity_hit_at_3 | 0.977 (84 / 86) | 0.977 (84 / 86) | unchanged | none |
| similarity_hit_at_5 | 1.000 (86 / 86) | 1.000 (86 / 86) | unchanged | none |
| similarity_mrr | 0.968 (83.283 / 86) | 0.968 (83.283 / 86) | unchanged | none |
| rerank_hit_at_1 | 1.000 (86 / 86) | 1.000 (86 / 86) | unchanged | none |
| rerank_hit_at_3 | 1.000 (86 / 86) | 1.000 (86 / 86) | unchanged | none |
| rerank_hit_at_5 | 1.000 (86 / 86) | 1.000 (86 / 86) | unchanged | none |
| rerank_mrr | 1.000 (86.000 / 86) | 1.000 (86.000 / 86) | unchanged | none |
| similarity_gate_survival | 1.000 (86 / 86) | 1.000 (86 / 86) | unchanged | none |

### serving

| metric | base | new | movement | cases |
|---|---|---|---|---|
| unserved_answerable_share | 0.011 (1 / 87) | 0.011 (1 / 87) | unchanged | none |
| wrong_abstention_share | 0.000 (0 / 23) | 0.000 (0 / 23) | unchanged | none |
| verdict_distribution.answered | 0.789 (86 / 109) | 0.789 (86 / 109) | unchanged | none |
| verdict_distribution.answered_unreranked | 0.000 (0 / 109) | 0.000 (0 / 109) | unchanged | none |
| verdict_distribution.abstained_empty_corpus | 0.000 (0 / 109) | 0.000 (0 / 109) | unchanged | none |
| verdict_distribution.abstained_empty_pool | 0.000 (0 / 109) | 0.000 (0 / 109) | unchanged | none |
| verdict_distribution.abstained_similarity_floor | 0.000 (0 / 109) | 0.000 (0 / 109) | unchanged | none |
| verdict_distribution.abstained_rerank_floor | 0.147 (16 / 109) | 0.147 (16 / 109) | unchanged | none |
| verdict_distribution.abstained_generation | 0.064 (7 / 109) | 0.064 (7 / 109) | unchanged | none |

### booking

| metric | base | new | movement | cases |
|---|---|---|---|---|
| tool_selection_correctness | 1.000 (24 / 24) | 1.000 (24 / 24) | unchanged | none |
| end_to_end_task_success | 1.000 (24 / 24) | 1.000 (24 / 24) | unchanged | none |


## Case movements

No case moved.

## Cases that moved under an unchanged metric

None: every case that moved moved a metric with it.
