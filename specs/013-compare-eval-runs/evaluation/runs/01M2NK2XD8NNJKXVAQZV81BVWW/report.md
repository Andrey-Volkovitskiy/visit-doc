# Golden-set report - run 01M2NK2XD8NNJKXVAQZV81BVWW

## Conditions

- Clock: 2026-03-02T08:00:00
- Session: 01M2NK2WTZBE003M0NQ44KJW0M
- Corpus: live ea83b6c4c0b55657c5fa73bb1b5d1226442b7fa71b6e8f8a8512a6d9a48b20a0, pinned ea83b6c4c0b55657c5fa73bb1b5d1226442b7fa71b6e8f8a8512a6d9a48b20a0, matched: True
- Selection: all (135 cases)
- Recorded cases: 135 of 135
- classification_model: claude-haiku-4-5-20251001
- generation_model: claude-sonnet-5
- embedding_model: voyage-4-lite
- rerank_model: rerank-3
- retrieval_pool_size: 25
- similarity_floor: 0.3
- similarity_cap: 5
- rerank_floor: 0.58
- rerank_cap: 3
- max_segments: 3
- context_turns: 5

## Alignment

| aligned | unaligned | excluded | labelled |
|---|---|---|---|
| 185 | 5 | 0 | 190 |

## Exclusions

- handed_off_turn: 33

### Fixtures that would not plant

None.

## Metrics

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| request_count_accuracy | 0.970 | 131 / 135 | - |
| intent_accuracy | 0.973 | 180 / 185 | - |
| exact_segmentation_match | 0.933 | 126 / 135 | - |

Unaligned requests beside intent accuracy: 5

### Segmentation disagreements

- G003: labelled ['faq_question'], produced ['booking']
- G023: labelled ['faq_question'], produced ['small_talk']
- G031: labelled ['small_talk'], produced ['booking']
- G033: labelled ['small_talk'], produced ['faq_question', 'small_talk']
- G068: labelled ['urgent_condition'], produced ['urgent_condition', 'booking']
- G077: labelled ['urgent_condition'], produced ['booking_for_another', 'urgent_condition']
- G097: labelled ['faq_question', 'faq_question'], produced ['faq_question', 'unknown']
- G113: labelled ['faq_question', 'booking_for_another'], produced ['small_talk', 'booking_for_another']
- G117: labelled ['faq_question', 'urgent_condition'], produced ['urgent_condition']

### Turns combined to stay within the segment cap

none

## Retrieval

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| similarity_hit_at_1 | 0.988 | 84 / 85 | not_routed_to_faq 2, handed_off_turn 6 |
| similarity_hit_at_3 | 1.000 | 85 / 85 | not_routed_to_faq 2, handed_off_turn 6 |
| similarity_hit_at_5 | 1.000 | 85 / 85 | not_routed_to_faq 2, handed_off_turn 6 |
| similarity_mrr | 0.994 | 84.500 / 85 | not_routed_to_faq 2, handed_off_turn 6 |
| rerank_hit_at_1 | 1.000 | 84 / 84 | not_routed_to_faq 2, not_reached_reranker 1, handed_off_turn 6 |
| rerank_hit_at_3 | 1.000 | 84 / 84 | not_routed_to_faq 2, not_reached_reranker 1, handed_off_turn 6 |
| rerank_hit_at_5 | 1.000 | 84 / 84 | not_routed_to_faq 2, not_reached_reranker 1, handed_off_turn 6 |
| rerank_mrr | 1.000 | 84.000 / 84 | not_routed_to_faq 2, not_reached_reranker 1, handed_off_turn 6 |
| similarity_gate_survival | 0.988 | 84 / 85 | not_routed_to_faq 2, handed_off_turn 6 |

On rerank_hit_at_5: rerank_hit_at_5 is 1 by construction while similarity_cap (5) is 5 or less: the reranker is handed at most that many chunks, and the rerank stage scores only requests with a cited chunk among them - so it is not a finding.

Unaligned labelled-answerable requests, scored at neither stage: 0

## Serving

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| unserved_answerable_share | 0.057 | 5 / 87 | handed_off_turn 6 |
| wrong_abstention_share | 0.143 | 3 / 21 | handed_off_turn 33 |

unserved_answerable_share and wrong_abstention_share share part of their numerator - an abstention on a labelled-answerable request counts in both - and differ in their denominator: the first asks how much of what could be served was not, over the labelled-answerable requests whose turn was permitted to answer; the second asks how often an abstention was wrong, over every abstention the run produced. A run can move one without moving the other.

Denominators: unserved_answerable_share 87, wrong_abstention_share 21.

### Unserved answerable requests

By cause: abstained 3, lost_to_count_mismatch 0, misclassified 2.

- G003 [0] misclassified: Can I see a specialist if my GP hasn't sent anything over?
- G016 [0] abstained at rerank_floor: Is parking free?
- G096 [1] abstained at rerank_floor: Is parking free?
- G097 [1] misclassified: Do I need a referral to visit a dentist?
- G100 [1] abstained at rerank_floor: What happens if you do not accept Medicare?

### Not permitted to answer (handed off or silenced; out of the denominator)

G111, G112, G113, G114, G116, G117

### Wrong abstentions

- G016 [0] at rerank_floor: Is parking free?
- G096 [1] at rerank_floor: Is parking free?
- G100 [1] at rerank_floor: What happens if you do not accept Medicare?

### Degraded answers (answered_unreranked)

None.

### Answers on labelled gaps

- G020 [0] answered: Do you accept Blue Cross insurance for dental implants?
- G024 [0] answered: Do you do blood tests on Saturdays?
- G094 [0] answered: Are you open on Sundays?
- G098 [1] answered: What should a returning patient bring?
- G129 [1] answered: How much is a follow-up visit out of pocket?

### Verdict distribution

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| verdict_distribution.answered | 0.807 | 88 / 109 | handed_off_turn 33 |
| verdict_distribution.answered_unreranked | 0.000 | 0 / 109 | handed_off_turn 33 |
| verdict_distribution.abstained_empty_corpus | 0.000 | 0 / 109 | handed_off_turn 33 |
| verdict_distribution.abstained_empty_pool | 0.000 | 0 / 109 | handed_off_turn 33 |
| verdict_distribution.abstained_similarity_floor | 0.037 | 4 / 109 | handed_off_turn 33 |
| verdict_distribution.abstained_rerank_floor | 0.156 | 17 / 109 | handed_off_turn 33 |

## Booking

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| tool_selection_correctness | 0.667 | 12 / 18 | - |
| end_to_end_task_success | 0.889 | 16 / 18 | - |

tool_selection_correctness is scored once per turn's booking half, against the union of the tools its booking requests are labelled with. The booking loop is handed all of a turn's booking requests at once, and the log attributes a tool call to the loop rather than to one request, so no per-request number is published. A tool called that no label names is not a miss. For a case with a scripted reply, the tools called in both of its turns count together, as one booking half: the loop is specified to ask in the first turn and act in the second.

### Tool-selection misses

- G048: missing reschedule_appointment; called list_my_appointments
- G049: missing list_practitioners; called nothing
- G053: missing check_availability; called nothing
- G090: missing cancel_appointment; called list_my_appointments
- G092: missing list_practitioners; called nothing
- G095: missing book_appointment; called check_availability

### End-to-end failures

- G090, read after the last turn: expected, not found: William Osler +4d 11:00 cancelled | found, not expected: William Osler 2026-03-06T11:00:00 standing
- G095, read after the last turn: expected, not found: William Osler +4d standing | found, not expected: none

## Runs and attempts

- Cases needing more than one attempt: 0
- Turns marked assistant_failed that still replied: none
- Streams that broke the service's contract and then settled: none
- Drive seconds: 618.69
- Score seconds: 0.0983

## Not computed

None.
