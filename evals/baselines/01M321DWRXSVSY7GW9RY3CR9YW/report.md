# Golden-set report - run 01M321DWRXSVSY7GW9RY3CR9YW

## Conditions

- Clock: 2026-03-02T08:00:00
- Session: 01M321DVZFFVECZQQKHKT4EEDW
- Corpus: live 3d94d02192c4cdf385884b0f681e6edfaa3fd9a229c2f0312bf37c64e6c9ec7b, pinned 3d94d02192c4cdf385884b0f681e6edfaa3fd9a229c2f0312bf37c64e6c9ec7b, matched: True
- Selection: all (97 cases)
- Recorded cases: 97 of 97
- classification_model: claude-haiku-4-5-20251001
- generation_model: claude-sonnet-5
- embedding_model: voyage-4-lite
- rerank_model: rerank-3
- retrieval_pool_size: 25
- similarity_floor: 0.25
- similarity_cap: 5
- rerank_floor: 0.58
- rerank_cap: 3
- max_segments: 3
- context_turns: 5

## Alignment

| aligned | unaligned | excluded | labelled |
|---|---|---|---|
| 117 | 0 | 0 | 117 |

## Exclusions

- handed_off_turn: 22

### Fixtures that would not plant

None.

## Metrics

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| request_count_accuracy | 1.000 | 97 / 97 | - |
| intent_accuracy | 1.000 | 117 / 117 | - |
| exact_segmentation_match | 1.000 | 97 / 97 | - |

Unaligned requests beside intent accuracy: 0

### Segmentation disagreements

None.

### Turns combined to stay within the segment cap

none

## Retrieval

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| similarity_hit_at_1 | 0.977 | 42 / 43 | - |
| similarity_hit_at_3 | 1.000 | 43 / 43 | - |
| similarity_hit_at_5 | 1.000 | 43 / 43 | - |
| similarity_mrr | 0.988 | 42.500 / 43 | - |
| rerank_hit_at_1 | 1.000 | 43 / 43 | - |
| rerank_hit_at_3 | 1.000 | 43 / 43 | - |
| rerank_hit_at_5 | 1.000 | 43 / 43 | - |
| rerank_mrr | 1.000 | 43.000 / 43 | - |
| similarity_gate_survival | 1.000 | 43 / 43 | - |

On rerank_hit_at_5: rerank_hit_at_5 is 1 by construction while similarity_cap (5) is 5 or less: the reranker is handed at most that many chunks, and the rerank stage scores only requests with a cited chunk among them - so it is not a finding.

Unaligned labelled-answerable requests, scored at neither stage: 0

## Serving

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| unserved_answerable_share | 0.000 | 0 / 43 | - |
| wrong_abstention_share | 0.000 | 0 / 17 | handed_off_turn 22 |

unserved_answerable_share and wrong_abstention_share share part of their numerator - an abstention on a labelled-answerable request counts in both - and differ in their denominator: the first asks how much of what could be served was not, over the labelled-answerable requests whose turn was permitted to answer; the second asks how often an abstention was wrong, over every abstention the run produced. A run can move one without moving the other.

Denominators: unserved_answerable_share 43, wrong_abstention_share 17.

### Unserved answerable requests

By cause: abstained 0, lost_to_count_mismatch 0, misclassified 0.

None.

### Not permitted to answer (handed off or silenced; out of the denominator)

none

### Wrong abstentions

None.

### Degraded answers (answered_unreranked)

None.

### Answers on labelled gaps

None.

### Verdict distribution

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| verdict_distribution.answered | 0.717 | 43 / 60 | handed_off_turn 22 |
| verdict_distribution.answered_unreranked | 0.000 | 0 / 60 | handed_off_turn 22 |
| verdict_distribution.abstained_empty_corpus | 0.000 | 0 / 60 | handed_off_turn 22 |
| verdict_distribution.abstained_empty_pool | 0.000 | 0 / 60 | handed_off_turn 22 |
| verdict_distribution.abstained_similarity_floor | 0.017 | 1 / 60 | handed_off_turn 22 |
| verdict_distribution.abstained_rerank_floor | 0.233 | 14 / 60 | handed_off_turn 22 |
| verdict_distribution.abstained_generation | 0.033 | 2 / 60 | handed_off_turn 22 |

## Booking

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| tool_selection_correctness | 1.000 | 23 / 23 | - |
| end_to_end_task_success | 1.000 | 23 / 23 | - |

tool_selection_correctness is scored once per turn's booking half, against the union of the tools its booking requests are labelled with. The booking loop is handed all of a turn's booking requests at once, and the log attributes a tool call to the loop rather than to one request, so no per-request number is published. A tool called that no label names is not a miss. For a case whose scripted reply was posted, the tools called in both of its turns count together, as one booking half. The reply is posted only when the first turn did not already leave the expected appointments, so a case the loop finished in one turn is scored on that turn's calls alone. The roster the booking node reads into its prompt before the model's first request counts as a list_practitioners call; a roster read that failed does not.

### Tool-selection misses

None.

### End-to-end failures

None.

## Runs and attempts

- Cases needing more than one attempt: 0
- Turns marked assistant_failed that still replied: none
- Streams that broke the service's contract and then settled: none
- Drive seconds: 497.74
- Score seconds: 0.1022

## Not computed

None.
