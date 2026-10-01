# Golden-set report - run 01M3W2MA7Y1BBTG2Y9F5W1NCKN

## Conditions

- Clock: 2026-03-02T08:00:00
- Session: 01M3W2M8RK4W00HQBTRHEVRN2W
- Corpus: live 3af64fc850bdcc445694739bfa21baf33b9052ff6e1f124206b564028a236d0f, pinned 3af64fc850bdcc445694739bfa21baf33b9052ff6e1f124206b564028a236d0f, matched: True
- Selection: all (146 cases)
- Recorded cases: 146 of 146
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

## Alignment

| aligned | unaligned | excluded | labelled |
|---|---|---|---|
| 167 | 0 | 1 | 168 |

## Exclusions

- handed_off_turn: 22
- run_error: 1

### Fixtures that would not plant

None.

## Metrics

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| request_count_accuracy | 1.000 | 145 / 145 | run_error 1 |
| intent_accuracy | 0.976 | 163 / 167 | run_error 1 |
| exact_segmentation_match | 0.972 | 141 / 145 | run_error 1 |

Unaligned requests beside intent accuracy: 0

### Segmentation disagreements

- G-s-10: labelled ['faq_question'], produced ['small_talk']
- G-t-01: labelled ['faq_question'], produced ['small_talk']
- G-t-04: labelled ['faq_question'], produced ['booking']
- G-w-02: labelled ['faq_question'], produced ['small_talk']

### Turns combined to stay within the segment cap

none

## Retrieval

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| similarity_hit_at_1 | 0.927 | 76 / 82 | not_routed_to_faq 4, run_error 1 |
| similarity_hit_at_3 | 0.976 | 80 / 82 | not_routed_to_faq 4, run_error 1 |
| similarity_hit_at_5 | 1.000 | 82 / 82 | not_routed_to_faq 4, run_error 1 |
| similarity_mrr | 0.955 | 78.333 / 82 | not_routed_to_faq 4, run_error 1 |
| rerank_hit_at_1 | 1.000 | 82 / 82 | not_routed_to_faq 4, run_error 1 |
| rerank_hit_at_3 | 1.000 | 82 / 82 | not_routed_to_faq 4, run_error 1 |
| rerank_hit_at_5 | 1.000 | 82 / 82 | not_routed_to_faq 4, run_error 1 |
| rerank_mrr | 1.000 | 82.000 / 82 | not_routed_to_faq 4, run_error 1 |
| similarity_gate_survival | 1.000 | 82 / 82 | not_routed_to_faq 4, run_error 1 |

On rerank_hit_at_5: rerank_hit_at_5 is 1 by construction while similarity_cap (5) is 5 or less: the reranker is handed at most that many chunks, and the rerank stage scores only requests with a cited chunk among them - so it is not a finding.

Unaligned labelled-answerable requests, scored at neither stage: 0

## Serving

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| unserved_answerable_share | 0.070 | 6 / 86 | run_error 1 |
| wrong_abstention_share | 0.080 | 2 / 25 | handed_off_turn 22, run_error 1 |

unserved_answerable_share and wrong_abstention_share share part of their numerator - an abstention on a labelled-answerable request counts in both - and differ in their denominator: the first asks how much of what could be served was not, over the labelled-answerable requests whose turn was permitted to answer; the second asks how often an abstention was wrong, over every abstention the run produced. A run can move one without moving the other.

Denominators: unserved_answerable_share 86, wrong_abstention_share 25.

### Unserved answerable requests

By cause: abstained 2, lost_to_count_mismatch 0, misclassified 4.

- G-s-10 [0] misclassified: I got a text from 72913 - is that really you?
- G-s-11 [0] abstained at generation: Do you take Delta Dental PPO?
- G-t-01 [0] misclassified: My mouth is still frozen from the filling. Can I have a coffee?
- G-t-04 [0] misclassified: Something came up and I'll get there about twenty minutes after my slot. Will I still be seen?
- G-t-08 [0] abstained at generation: Can I put moisturiser on before the heart tracing?
- G-w-02 [0] misclassified: How much is 93000 if I'm paying myself?

### Not permitted to answer (handed off or silenced; out of the denominator)

none

### Wrong abstentions

- G-s-11 [0] at generation: Do you take Delta Dental PPO?
- G-t-08 [0] at generation: Can I put moisturiser on before the heart tracing?

### Degraded answers (answered_unreranked)

None.

### Answers on labelled gaps

None.

### Verdict distribution

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| verdict_distribution.answered | 0.762 | 80 / 105 | handed_off_turn 22, run_error 1 |
| verdict_distribution.answered_unreranked | 0.000 | 0 / 105 | handed_off_turn 22, run_error 1 |
| verdict_distribution.abstained_empty_corpus | 0.000 | 0 / 105 | handed_off_turn 22, run_error 1 |
| verdict_distribution.abstained_empty_pool | 0.000 | 0 / 105 | handed_off_turn 22, run_error 1 |
| verdict_distribution.abstained_similarity_floor | 0.000 | 0 / 105 | handed_off_turn 22, run_error 1 |
| verdict_distribution.abstained_rerank_floor | 0.171 | 18 / 105 | handed_off_turn 22, run_error 1 |
| verdict_distribution.abstained_generation | 0.067 | 7 / 105 | handed_off_turn 22, run_error 1 |

## Booking

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| tool_selection_correctness | 1.000 | 24 / 24 | - |
| end_to_end_task_success | 1.000 | 24 / 24 | - |

tool_selection_correctness is scored once per turn's booking half, against the union of the tools its booking requests are labelled with. The booking loop is handed all of a turn's booking requests at once, and the log attributes a tool call to the loop rather than to one request, so no per-request number is published. A tool called that no label names is not a miss. For a case whose scripted reply was posted, the tools called in both of its turns count together, as one booking half. The reply is posted only when the first turn did not already leave the expected appointments, so a case the loop finished in one turn is scored on that turn's calls alone. The roster the booking node reads into its prompt before the model's first request counts as a list_practitioners call; a roster read that failed does not.

### Tool-selection misses

None.

### End-to-end failures

None.

## Runs and attempts

- Cases needing more than one attempt: 0
- Turns marked assistant_failed that still replied: none
- Streams that broke the service's contract and then settled: none
- Drive seconds: 575.09
- Score seconds: 0.0942

## Not computed

None.
