# Comparison - 01M3WD842TTD1Q9FX8TDRTMFAB -> 01M4WQE1K49R809BA0DNX4X69S

## Runs

- Base: 01M3WD842TTD1Q9FX8TDRTMFAB [traced] (evals/baselines/01M3WD842TTD1Q9FX8TDRTMFAB), started 2026-10-01T18:57:22.522363+00:00, complete
- New: 01M4WQE1K49R809BA0DNX4X69S [traced] (/tmp/claude-1000/-home-andrey-visit-doc/f814b892-f188-418f-8249-b1f3ba0beed2/scratchpad/probe/run_hybrid), started 2026-10-01T18:57:22.522363+00:00, complete

## Conditions

- retrieval: dense -> hybrid_rrf
- rrf_k: None -> 60

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
| similarity_hit_at_1 | 0.953 (82 / 86) | 0.860 (74 / 86) | moved away from its target; down 0.093 | G-j-03 [0], G-j-06 [0], G-j-14 [0], G-j-16 [0], G-m-06 [2], G-m-08 [0], G-q-08 [0], G-t-02 [0], G-t-07 [0], G-w-05 [0] |
| similarity_hit_at_3 | 0.977 (84 / 86) | 0.953 (82 / 86) | moved away from its target; down 0.023 | G-j-03 [0], G-j-06 [0], G-m-04 [1], G-m-06 [2], G-o-02 [1], G-o-06 [1] |
| similarity_hit_at_5 | 1.000 (86 / 86) | 0.965 (83 / 86) | moved away from its target; down 0.035 | G-j-06 [0], G-m-06 [2], G-o-06 [1] |
| similarity_mrr | 0.968 (83.283 / 86) | 0.910 (78.296 / 86) | moved away from its target; down 0.058 | G-j-03 [0], G-j-06 [0], G-j-14 [0], G-j-16 [0], G-m-04 [1], G-m-06 [2], G-m-08 [0], G-o-02 [1], G-o-06 [1], G-q-08 [0], G-t-02 [0], G-t-07 [0], G-w-05 [0] |
| rerank_hit_at_1 | 1.000 (86 / 86) | 1.000 (83 / 83) | unchanged; denominator moved | G-j-06 [0], G-m-06 [2], G-o-06 [1] |
| rerank_hit_at_3 | 1.000 (86 / 86) | 1.000 (83 / 83) | unchanged; denominator moved | G-j-06 [0], G-m-06 [2], G-o-06 [1] |
| rerank_hit_at_5 | 1.000 (86 / 86) | 1.000 (83 / 83) | unchanged; denominator moved | G-j-06 [0], G-m-06 [2], G-o-06 [1] |
| rerank_mrr | 1.000 (86.000 / 86) | 1.000 (83.000 / 83) | unchanged; denominator moved | G-j-06 [0], G-m-06 [2], G-o-06 [1] |
| similarity_gate_survival | 1.000 (86 / 86) | 0.965 (83 / 86) | moved away from its target; down 0.035 | G-j-06 [0], G-m-06 [2], G-o-06 [1] |

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

### retrieval_rank (16)

- G-j-03 [0]: similarity rank 1 -> similarity rank 5 (degraded against its label) - affects similarity_hit_at_1, similarity_hit_at_3, similarity_mrr
- G-j-06 [0]: rerank rank 1 -> rerank set aside: not_reached_reranker (changed, in no direction) - affects rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr, similarity_gate_survival, similarity_hit_at_1, similarity_hit_at_3, similarity_hit_at_5, similarity_mrr
- G-j-06 [0]: similarity rank 1 -> similarity rank 16 (degraded against its label) - affects rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr, similarity_gate_survival, similarity_hit_at_1, similarity_hit_at_3, similarity_hit_at_5, similarity_mrr
- G-j-14 [0]: similarity rank 1 -> similarity rank 2 (degraded against its label) - affects similarity_hit_at_1, similarity_mrr
- G-j-16 [0]: similarity rank 1 -> similarity rank 2 (degraded against its label) - affects similarity_hit_at_1, similarity_mrr
- G-m-04 [1]: similarity rank 4 -> similarity rank 2 (improved against its label) - affects similarity_hit_at_3, similarity_mrr
- G-m-06 [2]: rerank rank 1 -> rerank set aside: not_reached_reranker (changed, in no direction) - affects rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr, similarity_gate_survival, similarity_hit_at_1, similarity_hit_at_3, similarity_hit_at_5, similarity_mrr
- G-m-06 [2]: similarity rank 1 -> similarity rank 10 (degraded against its label) - affects rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr, similarity_gate_survival, similarity_hit_at_1, similarity_hit_at_3, similarity_hit_at_5, similarity_mrr
- G-m-08 [0]: similarity rank 1 -> similarity rank 2 (degraded against its label) - affects similarity_hit_at_1, similarity_mrr
- G-o-02 [1]: similarity rank 5 -> similarity rank 2 (improved against its label) - affects similarity_hit_at_3, similarity_mrr
- G-o-06 [1]: rerank rank 1 -> rerank set aside: not_reached_reranker (changed, in no direction) - affects rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr, similarity_gate_survival, similarity_hit_at_3, similarity_hit_at_5, similarity_mrr
- G-o-06 [1]: similarity rank 3 -> similarity rank 10 (degraded against its label) - affects rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr, similarity_gate_survival, similarity_hit_at_3, similarity_hit_at_5, similarity_mrr
- G-q-08 [0]: similarity rank 1 -> similarity rank 2 (degraded against its label) - affects similarity_hit_at_1, similarity_mrr
- G-t-02 [0]: similarity rank 1 -> similarity rank 2 (degraded against its label) - affects similarity_hit_at_1, similarity_mrr
- G-t-07 [0]: similarity rank 1 -> similarity rank 3 (degraded against its label) - affects similarity_hit_at_1, similarity_mrr
- G-w-05 [0]: similarity rank 2 -> similarity rank 1 (improved against its label) - affects similarity_hit_at_1, similarity_mrr


## Cases that moved under an unchanged metric

- G-j-06 [0]: rerank rank 1 -> rerank set aside: not_reached_reranker, under rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr
- G-j-06 [0]: similarity rank 1 -> similarity rank 16, under rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr
- G-m-06 [2]: rerank rank 1 -> rerank set aside: not_reached_reranker, under rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr
- G-m-06 [2]: similarity rank 1 -> similarity rank 10, under rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr
- G-o-06 [1]: rerank rank 1 -> rerank set aside: not_reached_reranker, under rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr
- G-o-06 [1]: similarity rank 3 -> similarity rank 10, under rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr
