# Noise band 01M3W93XYN42QWH1TCYXN3E5FC

The band is the spread of five observations of one unchanged build. It is an observed range, not a confidence interval and not a significance test.

This band is evidence of how much these numbers move on their own. It is not a threshold: no run is required to beat it, and nothing here says what a run should score.

## Measured from

- Runs, in the order taken: 01M3W4D3ZPHGWSV5DPQJB5RZC3, 01M3W7073Q0S48QZA1MNE60XV3, 01M3W7H8ZWW05GNVS38KQYRGWS, 01M3W81ZE83FPNJ7G4N1DPBK0H, 01M3W8KBMZD55CYE8F2NE3BDZN
- Cases: 146
- Corpus: 3af64fc850bdcc445694739bfa21baf33b9052ff6e1f124206b564028a236d0f
- Clock: 2026-03-02T08:00:00
- Measured at: 2026-10-01T17:45:10.894324+00:00
- classification_model: claude-haiku-4-5-20251001
- generation_model: claude-sonnet-5
- small_talk_model: claude-haiku-4-5-20251001
- embedding_model: voyage-4-lite
- rerank_model: rerank-3
- retrieval_pool_size: 25
- similarity_floor: -1.0
- similarity_cap: 5
- unreranked_similarity_floor: 0.25
- rerank_floor: 0.58
- rerank_cap: 3
- max_segments: 3
- context_turns: 5

## Observed range per metric

| metric | low | high | the five values |
|---|---|---|---|
| request_count_accuracy | 1.000 | 1.000 | 1.000, 1.000, 1.000, 1.000, 1.000 |
| intent_accuracy | 0.976 | 0.976 | 0.976, 0.976, 0.976, 0.976, 0.976 |
| exact_segmentation_match | 0.973 | 0.973 | 0.973, 0.973, 0.973, 0.973, 0.973 |
| similarity_hit_at_1 | 0.952 | 0.952 | 0.952, 0.952, 0.952, 0.952, 0.952 |
| similarity_hit_at_3 | 0.976 | 0.976 | 0.976, 0.976, 0.976, 0.976, 0.976 |
| similarity_hit_at_5 | 1.000 | 1.000 | 1.000, 1.000, 1.000, 1.000, 1.000 |
| similarity_mrr | 0.967 | 0.967 | 0.967, 0.967, 0.967, 0.967, 0.967 |
| rerank_hit_at_1 | 1.000 | 1.000 | 1.000, 1.000, 1.000, 1.000, 1.000 |
| rerank_hit_at_3 | 1.000 | 1.000 | 1.000, 1.000, 1.000, 1.000, 1.000 |
| rerank_hit_at_5 | 1.000 | 1.000 | 1.000, 1.000, 1.000, 1.000, 1.000 |
| rerank_mrr | 1.000 | 1.000 | 1.000, 1.000, 1.000, 1.000, 1.000 |
| similarity_gate_survival | 1.000 | 1.000 | 1.000, 1.000, 1.000, 1.000, 1.000 |
| unserved_answerable_share | 0.046 | 0.069 | 0.057, 0.046, 0.057, 0.069, 0.046 |
| wrong_abstention_share | 0.000 | 0.080 | 0.042, 0.000, 0.042, 0.080, 0.000 |
| verdict_distribution.answered | 0.764 | 0.783 | 0.774, 0.783, 0.774, 0.764, 0.783 |
| verdict_distribution.answered_unreranked | 0.000 | 0.000 | 0.000, 0.000, 0.000, 0.000, 0.000 |
| verdict_distribution.abstained_empty_corpus | 0.000 | 0.000 | 0.000, 0.000, 0.000, 0.000, 0.000 |
| verdict_distribution.abstained_empty_pool | 0.000 | 0.000 | 0.000, 0.000, 0.000, 0.000, 0.000 |
| verdict_distribution.abstained_similarity_floor | 0.000 | 0.000 | 0.000, 0.000, 0.000, 0.000, 0.000 |
| verdict_distribution.abstained_rerank_floor | 0.151 | 0.151 | 0.151, 0.151, 0.151, 0.151, 0.151 |
| verdict_distribution.abstained_generation | 0.066 | 0.085 | 0.075, 0.066, 0.075, 0.085, 0.066 |
| tool_selection_correctness | 1.000 | 1.000 | 1.000, 1.000, 1.000, 1.000, 1.000 |
| end_to_end_task_success | 1.000 | 1.000 | 1.000, 1.000, 1.000, 1.000, 1.000 |

## Cases that varied on an unchanged build

- G-r-01 [0] (verdict): abstained_generation in 1 of 5, answered in 4 of 5
- G-w-05 [0] (verdict): abstained_generation in 3 of 5, answered in 2 of 5

