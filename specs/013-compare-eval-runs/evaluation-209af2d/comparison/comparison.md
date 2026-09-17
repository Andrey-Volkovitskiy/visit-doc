# Comparison - 01M2NMC5ATQ0G5G6246PW0VP3E -> 01M2QV47S2PM18W25RGYQM7Q8Z

## Runs

- Base: 01M2NMC5ATQ0G5G6246PW0VP3E (/home/andrey/visit-doc/.run/evals/01M2NMC5ATQ0G5G6246PW0VP3E), started 2026-09-16T17:31:32.058255+00:00, complete
- New: 01M2QV47S2PM18W25RGYQM7Q8Z (/home/andrey/visit-doc/.run/evals/01M2QV47S2PM18W25RGYQM7Q8Z), started 2026-09-17T14:08:01.314143+00:00, complete

## Conditions

The two runs' conditions matched in every field, corpus hash included.

## Coverage

Both runs recorded the same 135 cases.

## Band

Band: 01M2QVRDGXFN3W7FT21EP4N0KG.
The band is the spread of five observations of one unchanged build. It is an observed range, not a confidence interval and not a significance test.

## Alignment and exclusions

| totals | aligned | unaligned | excluded | labelled |
|---|---|---|---|---|
| base | 185 | 5 | 0 | 190 |
| new | 186 | 4 | 0 | 190 |

Alignment moved, so a denominator below may have moved with it.

- handed_off_turn: 33 -> 33

## Metrics

### classification

| metric | base | new | movement | cases |
|---|---|---|---|---|
| request_count_accuracy | 0.970 (131 / 135) | 0.978 (132 / 135) | moved towards its target; up 0.007; within the observed range of five runs [0.978, 0.978] | G033 |
| intent_accuracy | 0.973 (180 / 185) | 0.973 (181 / 186) | moved towards its target; up 0.000; denominator moved; within the observed range of five runs [0.973, 0.973] | G033, G130, G130 [1] |
| exact_segmentation_match | 0.941 (127 / 135) | 0.948 (128 / 135) | moved towards its target; up 0.007; within the observed range of five runs [0.948, 0.948] | G130 |

### retrieval

| metric | base | new | movement | cases |
|---|---|---|---|---|
| similarity_hit_at_1 | 0.977 (85 / 87) | 1.000 (87 / 87) | moved towards its target; up 0.023; within the observed range of five runs [1.000, 1.000] | G097 [1], G099 [1] |
| similarity_hit_at_3 | 0.989 (86 / 87) | 1.000 (87 / 87) | moved towards its target; up 0.011; within the observed range of five runs [1.000, 1.000] | G097 [1] |
| similarity_hit_at_5 | 1.000 (87 / 87) | 1.000 (87 / 87) | unchanged; within the observed range of five runs [1.000, 1.000] | none |
| similarity_mrr | 0.986 (85.750 / 87) | 1.000 (87.000 / 87) | moved towards its target; up 0.014; within the observed range of five runs [1.000, 1.000] | G097 [1], G099 [1] |
| rerank_hit_at_1 | 0.977 (84 / 86) | 1.000 (86 / 86) | moved towards its target; up 0.023; within the observed range of five runs [1.000, 1.000] | G097 [1], G099 [1] |
| rerank_hit_at_3 | 1.000 (86 / 86) | 1.000 (86 / 86) | unchanged; within the observed range of five runs [1.000, 1.000] | none |
| rerank_hit_at_5 | 1.000 (86 / 86) | 1.000 (86 / 86) | unchanged; within the observed range of five runs [1.000, 1.000] | none |
| rerank_mrr | 0.988 (85.000 / 86) | 1.000 (86.000 / 86) | moved towards its target; up 0.012; within the observed range of five runs [1.000, 1.000] | G097 [1], G099 [1] |
| similarity_gate_survival | 0.989 (86 / 87) | 0.989 (86 / 87) | unchanged; within the observed range of five runs [0.989, 0.989] | none |

### serving

| metric | base | new | movement | cases |
|---|---|---|---|---|
| unserved_answerable_share | 0.057 (5 / 87) | 0.046 (4 / 87) | moved towards its target; down 0.011; within the observed range of five runs [0.046, 0.046] | G097 [1] |
| wrong_abstention_share | 0.217 (5 / 23) | 0.174 (4 / 23) | moved towards its target; down 0.043; within the observed range of five runs [0.174, 0.174] | G097 [1], G130, G130 [1] |
| verdict_distribution.answered | 0.795 (89 / 112) | 0.796 (90 / 113) | moved, in no direction; up 0.002; denominator moved; within the observed range of five runs [0.796, 0.796] | G097 [1] |
| verdict_distribution.answered_unreranked | 0.000 (0 / 112) | 0.000 (0 / 113) | unchanged; denominator moved; within the observed range of five runs [0.000, 0.000] | none |
| verdict_distribution.abstained_empty_corpus | 0.000 (0 / 112) | 0.000 (0 / 113) | unchanged; denominator moved; within the observed range of five runs [0.000, 0.000] | none |
| verdict_distribution.abstained_empty_pool | 0.000 (0 / 112) | 0.000 (0 / 113) | unchanged; denominator moved; within the observed range of five runs [0.000, 0.000] | none |
| verdict_distribution.abstained_similarity_floor | 0.045 (5 / 112) | 0.044 (5 / 113) | moved, in no direction; down 0.000; denominator moved; within the observed range of five runs [0.044, 0.044] | none |
| verdict_distribution.abstained_rerank_floor | 0.161 (18 / 112) | 0.159 (18 / 113) | moved, in no direction; down 0.001; denominator moved; within the observed range of five runs [0.159, 0.159] | G097 [1], G130, G130 [1] |

### booking

| metric | base | new | movement | cases |
|---|---|---|---|---|
| tool_selection_correctness | 0.611 (11 / 18) | 0.778 (14 / 18) | moved towards its target; up 0.167; within the observed range of five runs [0.722, 0.778] | G062, G095, G102 |
| end_to_end_task_success | 0.833 (15 / 18) | 1.000 (18 / 18) | moved towards its target; up 0.167; within the observed range of five runs [0.944, 1.000] | G062, G095, G102 |


## Case movements

### verdict (2)

- G097 [1]: abstained_rerank_floor -> answered (improved against its label; labelled answerable) - affects rerank_hit_at_1, rerank_mrr, similarity_hit_at_1, similarity_hit_at_3, similarity_mrr, unserved_answerable_share, verdict_distribution.abstained_rerank_floor, verdict_distribution.answered, wrong_abstention_share - "Do I need a referral to see a dentist?"
- G130 [1]: no outcome recorded -> abstained_rerank_floor (changed, in no direction) - affects intent_accuracy, verdict_distribution.abstained_rerank_floor, wrong_abstention_share - "Can I set up a payment plan?"

### retrieval_rank (4)

- G097 [1]: rerank rank 2 -> rerank rank 1 (improved against its label) - affects rerank_hit_at_1, rerank_mrr, similarity_hit_at_1, similarity_hit_at_3, similarity_mrr, unserved_answerable_share, verdict_distribution.abstained_rerank_floor, verdict_distribution.answered, wrong_abstention_share
- G097 [1]: similarity rank 4 -> similarity rank 1 (improved against its label) - affects rerank_hit_at_1, rerank_mrr, similarity_hit_at_1, similarity_hit_at_3, similarity_mrr, unserved_answerable_share, verdict_distribution.abstained_rerank_floor, verdict_distribution.answered, wrong_abstention_share
- G099 [1]: rerank rank 2 -> rerank rank 1 (improved against its label) - affects rerank_hit_at_1, rerank_mrr, similarity_hit_at_1, similarity_mrr
- G099 [1]: similarity rank 2 -> similarity rank 1 (improved against its label) - affects rerank_hit_at_1, rerank_mrr, similarity_hit_at_1, similarity_mrr

### segmentation (3)

- G033: faq_question, small_talk -> faq_question (changed, in no direction) - affects intent_accuracy, request_count_accuracy
- G077: urgent_condition, booking_for_another -> booking_for_another, urgent_condition (changed, in no direction) - the case was set aside in both runs (handed_off_turn)
- G130: faq_question, unknown -> faq_question, faq_question (improved against its label) - affects exact_segmentation_match, intent_accuracy, verdict_distribution.abstained_rerank_floor, wrong_abstention_share

### tool_selection (3)

- G062: missing book_appointment -> every labelled tool called (improved against its label) - affects end_to_end_task_success, tool_selection_correctness
- G095: missing book_appointment -> every labelled tool called (improved against its label) - affects end_to_end_task_success, tool_selection_correctness
- G102: missing book_appointment, cancel_appointment -> every labelled tool called (improved against its label) - this case varied on an unchanged build - affects end_to_end_task_success, tool_selection_correctness

### database_state (3)

- G062: did not match at scheduling_after -> expected appointments found (improved against its label) - affects end_to_end_task_success, tool_selection_correctness
- G095: did not match at scheduling_after -> expected appointments found (improved against its label) - affects end_to_end_task_success, tool_selection_correctness
- G102: did not match at scheduling_after -> expected appointments found (improved against its label) - this case varied on an unchanged build - affects end_to_end_task_success, tool_selection_correctness


## Cases that moved under an unchanged metric

None: every case that moved moved a metric with it.
