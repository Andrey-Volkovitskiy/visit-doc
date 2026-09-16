# Comparison - 01M2NHQFK02X5TJNJ05BM8BNX1 -> 01M2NMC5ATQ0G5G6246PW0VP3E

## Runs

- Base: 01M2NHQFK02X5TJNJ05BM8BNX1 (/home/andrey/visit-doc/.run/evals/01M2NHQFK02X5TJNJ05BM8BNX1), started 2026-09-16T16:45:17.280735+00:00, complete
- New: 01M2NMC5ATQ0G5G6246PW0VP3E (/home/andrey/visit-doc/.run/evals/01M2NMC5ATQ0G5G6246PW0VP3E), started 2026-09-16T17:31:32.058255+00:00, complete

## Conditions

The two runs' conditions matched in every field, corpus hash included.

## Coverage

Both runs recorded the same 135 cases.

## Band

Band: 01M2NN164ECVN78MTMYGEECKPP.
The band is the spread of five observations of one unchanged build. It is an observed range, not a confidence interval and not a significance test.

## Alignment and exclusions

| totals | aligned | unaligned | excluded | labelled |
|---|---|---|---|---|
| base | 183 | 7 | 0 | 190 |
| new | 184 | 6 | 0 | 190 |

Alignment moved, so a denominator below may have moved with it.

- handed_off_turn: 33 -> 33

## Metrics

### classification

| metric | base | new | movement | cases |
|---|---|---|---|---|
| request_count_accuracy | 0.956 (129 / 135) | 0.963 (130 / 135) | moved towards its target; up 0.007; within the observed range of five runs [0.956, 0.970] | G108 |
| intent_accuracy | 0.962 (176 / 183) | 0.973 (179 / 184) | moved towards its target; up 0.011; denominator moved; within the observed range of five runs [0.962, 0.973] | G003, G003 [0], G023, G023 [0], G108, G108 [0] |
| exact_segmentation_match | 0.911 (123 / 135) | 0.933 (126 / 135) | moved towards its target; up 0.022; within the observed range of five runs [0.911, 0.933] | G003, G023, G108 |

### retrieval

| metric | base | new | movement | cases |
|---|---|---|---|---|
| similarity_hit_at_1 | 0.988 (83 / 84) | 0.977 (84 / 86) | moved away from its target; down 0.011; denominator moved; within the observed range of five runs [0.976, 0.988] | G003, G003 [0], G099 [1], G108, G108 [0] |
| similarity_hit_at_3 | 1.000 (84 / 84) | 0.988 (85 / 86) | moved away from its target; down 0.012; denominator moved; within the observed range of five runs [0.988, 1.000] | G003, G003 [0], G097 [1], G108, G108 [0] |
| similarity_hit_at_5 | 1.000 (84 / 84) | 1.000 (86 / 86) | unchanged; denominator moved; within the observed range of five runs [1.000, 1.000] | G003, G003 [0], G108, G108 [0] |
| similarity_mrr | 0.994 (83.500 / 84) | 0.985 (84.750 / 86) | moved away from its target; down 0.009; denominator moved; within the observed range of five runs [0.985, 0.994] | G003, G003 [0], G097 [1], G099 [1], G108, G108 [0] |
| rerank_hit_at_1 | 1.000 (84 / 84) | 0.976 (83 / 85) | moved away from its target; down 0.024; denominator moved; within the observed range of five runs [0.976, 1.000] | G003, G003 [0], G081 [1], G097 [1], G099 [1], G108, G108 [0] |
| rerank_hit_at_3 | 1.000 (84 / 84) | 1.000 (85 / 85) | unchanged; denominator moved; within the observed range of five runs [0.988, 1.000] | G003, G003 [0], G081 [1], G108, G108 [0] |
| rerank_hit_at_5 | 1.000 (84 / 84) | 1.000 (85 / 85) | unchanged; denominator moved; within the observed range of five runs [1.000, 1.000] | G003, G003 [0], G081 [1], G108, G108 [0] |
| rerank_mrr | 1.000 (84.000 / 84) | 0.988 (84.000 / 85) | moved away from its target; down 0.012; denominator moved; within the observed range of five runs [0.988, 1.000] | G003, G003 [0], G081 [1], G097 [1], G099 [1], G108, G108 [0] |
| similarity_gate_survival | 1.000 (84 / 84) | 0.988 (85 / 86) | moved away from its target; down 0.012; denominator moved; within the observed range of five runs [0.988, 1.000] | G003, G003 [0], G081 [1], G108, G108 [0] |

### serving

| metric | base | new | movement | cases |
|---|---|---|---|---|
| unserved_answerable_share | 0.069 (6 / 87) | 0.069 (6 / 87) | unchanged; within the observed range of five runs [0.057, 0.080] | G003, G003 [0], G081 [1], G097 [1], G108, G108 [0] |
| wrong_abstention_share | 0.143 (3 / 21) | 0.217 (5 / 23) | moved away from its target; up 0.075; denominator moved; within the observed range of five runs [0.143, 0.227] | G023, G023 [0], G081 [1], G097 [1], G108, G108 [1] |
| verdict_distribution.answered | 0.811 (90 / 111) | 0.795 (89 / 112) | moved, in no direction; down 0.016; denominator moved; within the observed range of five runs [0.795, 0.811] | G003, G003 [0], G081 [1], G097 [1] |
| verdict_distribution.answered_unreranked | 0.000 (0 / 111) | 0.000 (0 / 112) | unchanged; denominator moved; within the observed range of five runs [0.000, 0.000] | none |
| verdict_distribution.abstained_empty_corpus | 0.000 (0 / 111) | 0.000 (0 / 112) | unchanged; denominator moved; within the observed range of five runs [0.000, 0.000] | none |
| verdict_distribution.abstained_empty_pool | 0.000 (0 / 111) | 0.000 (0 / 112) | unchanged; denominator moved; within the observed range of five runs [0.000, 0.000] | none |
| verdict_distribution.abstained_similarity_floor | 0.036 (4 / 111) | 0.045 (5 / 112) | moved, in no direction; up 0.009; denominator moved; within the observed range of five runs [0.036, 0.045] | G081 [1] |
| verdict_distribution.abstained_rerank_floor | 0.153 (17 / 111) | 0.161 (18 / 112) | moved, in no direction; up 0.008; denominator moved; within the observed range of five runs [0.153, 0.170] | G023, G023 [0], G097 [1], G108, G108 [1] |

### booking

| metric | base | new | movement | cases |
|---|---|---|---|---|
| tool_selection_correctness | 0.500 (9 / 18) | 0.611 (11 / 18) | moved towards its target; up 0.111; within the observed range of five runs [0.500, 0.667] | G042, G088, G090, G102 |
| end_to_end_task_success | 0.722 (13 / 18) | 0.833 (15 / 18) | moved towards its target; up 0.111; within the observed range of five runs [0.722, 0.889] | G042, G088, G090, G102 |


## Case movements

### verdict (5)

- G003 [0]: no outcome recorded -> answered (changed, in no direction) - this case varied on an unchanged build - affects intent_accuracy, rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr, similarity_gate_survival, similarity_hit_at_1, similarity_hit_at_3, similarity_hit_at_5, similarity_mrr, unserved_answerable_share, verdict_distribution.answered - "Can I see a specialist without a GP referral?"
- G023 [0]: no outcome recorded -> abstained_rerank_floor (changed, in no direction) - this case varied on an unchanged build - affects intent_accuracy, verdict_distribution.abstained_rerank_floor, wrong_abstention_share - "How much does parking at the Mega Mall garage cost per hour?"
- G081 [1]: answered -> abstained_similarity_floor (degraded against its label; labelled answerable) - this case varied on an unchanged build - affects rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr, similarity_gate_survival, unserved_answerable_share, verdict_distribution.abstained_similarity_floor, verdict_distribution.answered, wrong_abstention_share - "What cards do you take?"
- G097 [1]: answered -> abstained_rerank_floor (degraded against its label; labelled answerable) - this case varied on an unchanged build - affects rerank_hit_at_1, rerank_mrr, similarity_hit_at_3, similarity_mrr, unserved_answerable_share, verdict_distribution.abstained_rerank_floor, verdict_distribution.answered, wrong_abstention_share - "Does this clinic have a dentist?"
- G108 [1]: abstained_rerank_floor -> no outcome recorded (changed, in no direction) - this case varied on an unchanged build - affects verdict_distribution.abstained_rerank_floor, wrong_abstention_share - "Where should I leave my car?"

### retrieval_rank (9)

- G003 [0]: rerank set aside: not_routed_to_faq -> rerank rank 1 (changed, in no direction) - this case varied on an unchanged build - affects intent_accuracy, rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr, similarity_gate_survival, similarity_hit_at_1, similarity_hit_at_3, similarity_hit_at_5, similarity_mrr, unserved_answerable_share, verdict_distribution.answered
- G003 [0]: similarity set aside: not_routed_to_faq -> similarity rank 1 (changed, in no direction) - this case varied on an unchanged build - affects intent_accuracy, rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr, similarity_gate_survival, similarity_hit_at_1, similarity_hit_at_3, similarity_hit_at_5, similarity_mrr, unserved_answerable_share, verdict_distribution.answered
- G081 [1]: rerank rank 1 -> rerank set aside: not_reached_reranker (changed, in no direction) - this case varied on an unchanged build - affects rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr, similarity_gate_survival, unserved_answerable_share, verdict_distribution.abstained_similarity_floor, verdict_distribution.answered, wrong_abstention_share
- G097 [1]: rerank rank 1 -> rerank rank 2 (degraded against its label) - this case varied on an unchanged build - affects rerank_hit_at_1, rerank_mrr, similarity_hit_at_3, similarity_mrr, unserved_answerable_share, verdict_distribution.abstained_rerank_floor, verdict_distribution.answered, wrong_abstention_share
- G097 [1]: similarity rank 2 -> similarity rank 4 (degraded against its label) - this case varied on an unchanged build - affects rerank_hit_at_1, rerank_mrr, similarity_hit_at_3, similarity_mrr, unserved_answerable_share, verdict_distribution.abstained_rerank_floor, verdict_distribution.answered, wrong_abstention_share
- G099 [1]: rerank rank 1 -> rerank rank 2 (degraded against its label) - this case varied on an unchanged build - affects rerank_hit_at_1, rerank_mrr, similarity_hit_at_1, similarity_mrr
- G099 [1]: similarity rank 1 -> similarity rank 2 (degraded against its label) - this case varied on an unchanged build - affects rerank_hit_at_1, rerank_mrr, similarity_hit_at_1, similarity_mrr
- G108 [0]: rerank not scored -> rerank rank 1 (changed, in no direction) - this case varied on an unchanged build - affects intent_accuracy, rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr, similarity_gate_survival, similarity_hit_at_1, similarity_hit_at_3, similarity_hit_at_5, similarity_mrr, unserved_answerable_share
- G108 [0]: similarity not scored -> similarity rank 1 (changed, in no direction) - this case varied on an unchanged build - affects intent_accuracy, rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr, similarity_gate_survival, similarity_hit_at_1, similarity_hit_at_3, similarity_hit_at_5, similarity_mrr, unserved_answerable_share

### segmentation (4)

- G003: booking -> faq_question (improved against its label) - this case varied on an unchanged build - affects exact_segmentation_match, intent_accuracy, rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr, similarity_gate_survival, similarity_hit_at_1, similarity_hit_at_3, similarity_hit_at_5, similarity_mrr, unserved_answerable_share, verdict_distribution.answered
- G023: small_talk -> faq_question (improved against its label) - this case varied on an unchanged build - affects exact_segmentation_match, intent_accuracy, verdict_distribution.abstained_rerank_floor, wrong_abstention_share
- G077: booking_for_another, urgent_condition -> urgent_condition, booking_for_another (changed, in no direction) - the case was set aside in both runs (handed_off_turn) - this case varied on an unchanged build
- G108: faq_question, faq_question -> faq_question (improved against its label) - this case varied on an unchanged build - affects exact_segmentation_match, intent_accuracy, request_count_accuracy, rerank_hit_at_1, rerank_hit_at_3, rerank_hit_at_5, rerank_mrr, similarity_gate_survival, similarity_hit_at_1, similarity_hit_at_3, similarity_hit_at_5, similarity_mrr, unserved_answerable_share, verdict_distribution.abstained_rerank_floor, wrong_abstention_share

### tool_selection (4)

- G042: missing cancel_appointment -> every labelled tool called (improved against its label) - this case varied on an unchanged build - affects end_to_end_task_success, tool_selection_correctness
- G088: missing book_appointment -> every labelled tool called (improved against its label) - this case varied on an unchanged build - affects end_to_end_task_success, tool_selection_correctness
- G090: missing cancel_appointment -> every labelled tool called (improved against its label) - this case varied on an unchanged build - affects end_to_end_task_success, tool_selection_correctness
- G102: every labelled tool called -> missing book_appointment, cancel_appointment (degraded against its label) - this case varied on an unchanged build - affects end_to_end_task_success, tool_selection_correctness

### database_state (4)

- G042: did not match at scheduling_after -> expected appointments found (improved against its label) - this case varied on an unchanged build - affects end_to_end_task_success, tool_selection_correctness
- G088: did not match at scheduling_after -> expected appointments found (improved against its label) - this case varied on an unchanged build - affects end_to_end_task_success, tool_selection_correctness
- G090: did not match at scheduling_after -> expected appointments found (improved against its label) - this case varied on an unchanged build - affects end_to_end_task_success, tool_selection_correctness
- G102: expected appointments found -> did not match at scheduling_after (degraded against its label) - this case varied on an unchanged build - affects end_to_end_task_success, tool_selection_correctness


## Cases that moved under an unchanged metric

- G003 [0]: rerank set aside: not_routed_to_faq -> rerank rank 1, under rerank_hit_at_3, rerank_hit_at_5, similarity_hit_at_5, unserved_answerable_share
- G003 [0]: similarity set aside: not_routed_to_faq -> similarity rank 1, under rerank_hit_at_3, rerank_hit_at_5, similarity_hit_at_5, unserved_answerable_share
- G003: booking -> faq_question, under rerank_hit_at_3, rerank_hit_at_5, similarity_hit_at_5, unserved_answerable_share
- G003 [0]: no outcome recorded -> answered, under rerank_hit_at_3, rerank_hit_at_5, similarity_hit_at_5, unserved_answerable_share
- G081 [1]: rerank rank 1 -> rerank set aside: not_reached_reranker, under rerank_hit_at_3, rerank_hit_at_5, unserved_answerable_share
- G081 [1]: answered -> abstained_similarity_floor, under rerank_hit_at_3, rerank_hit_at_5, unserved_answerable_share
- G097 [1]: rerank rank 1 -> rerank rank 2, under unserved_answerable_share
- G097 [1]: similarity rank 2 -> similarity rank 4, under unserved_answerable_share
- G097 [1]: answered -> abstained_rerank_floor, under unserved_answerable_share
- G108 [0]: rerank not scored -> rerank rank 1, under rerank_hit_at_3, rerank_hit_at_5, similarity_hit_at_5, unserved_answerable_share
- G108 [0]: similarity not scored -> similarity rank 1, under rerank_hit_at_3, rerank_hit_at_5, similarity_hit_at_5, unserved_answerable_share
- G108: faq_question, faq_question -> faq_question, under rerank_hit_at_3, rerank_hit_at_5, similarity_hit_at_5, unserved_answerable_share
