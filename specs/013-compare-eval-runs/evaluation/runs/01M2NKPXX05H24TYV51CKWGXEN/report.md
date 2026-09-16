# Golden-set report - run 01M2NKPXX05H24TYV51CKWGXEN

## Conditions

- Clock: 2026-03-02T08:00:00
- Session: 01M2NKPXE3TQC42YWRA3BDMQW0
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
| 184 | 6 | 0 | 190 |

## Exclusions

- handed_off_turn: 33

### Fixtures that would not plant

None.

## Metrics

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| request_count_accuracy | 0.963 | 130 / 135 | - |
| intent_accuracy | 0.962 | 177 / 184 | - |
| exact_segmentation_match | 0.919 | 124 / 135 | - |

Unaligned requests beside intent accuracy: 6

### Segmentation disagreements

- G002: labelled ['faq_question'], produced ['faq_question', 'faq_question']
- G003: labelled ['faq_question'], produced ['booking']
- G023: labelled ['faq_question'], produced ['small_talk']
- G031: labelled ['small_talk'], produced ['booking']
- G033: labelled ['small_talk'], produced ['faq_question', 'small_talk']
- G068: labelled ['urgent_condition'], produced ['urgent_condition', 'booking']
- G077: labelled ['urgent_condition'], produced ['booking_for_another', 'urgent_condition']
- G111: labelled ['faq_question', 'urgent_condition'], produced ['urgent_condition', 'faq_question']
- G113: labelled ['faq_question', 'booking_for_another'], produced ['small_talk', 'booking_for_another']
- G117: labelled ['faq_question', 'urgent_condition'], produced ['urgent_condition']
- G130: labelled ['faq_question', 'faq_question'], produced ['faq_question', 'unknown']

### Turns combined to stay within the segment cap

none

## Retrieval

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| similarity_hit_at_1 | 0.988 | 84 / 85 | not_routed_to_faq 1, handed_off_turn 6 |
| similarity_hit_at_3 | 0.988 | 84 / 85 | not_routed_to_faq 1, handed_off_turn 6 |
| similarity_hit_at_5 | 1.000 | 85 / 85 | not_routed_to_faq 1, handed_off_turn 6 |
| similarity_mrr | 0.991 | 84.250 / 85 | not_routed_to_faq 1, handed_off_turn 6 |
| rerank_hit_at_1 | 0.988 | 83 / 84 | not_routed_to_faq 1, not_reached_reranker 1, handed_off_turn 6 |
| rerank_hit_at_3 | 0.988 | 83 / 84 | not_routed_to_faq 1, not_reached_reranker 1, handed_off_turn 6 |
| rerank_hit_at_5 | 1.000 | 84 / 84 | not_routed_to_faq 1, not_reached_reranker 1, handed_off_turn 6 |
| rerank_mrr | 0.991 | 83.250 / 84 | not_routed_to_faq 1, not_reached_reranker 1, handed_off_turn 6 |
| similarity_gate_survival | 0.988 | 84 / 85 | not_routed_to_faq 1, handed_off_turn 6 |

On rerank_hit_at_5: rerank_hit_at_5 is 1 by construction while similarity_cap (5) is 5 or less: the reranker is handed at most that many chunks, and the rerank stage scores only requests with a cited chunk among them - so it is not a finding.

Unaligned labelled-answerable requests, scored at neither stage: 1

## Serving

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| unserved_answerable_share | 0.080 | 7 / 87 | handed_off_turn 6 |
| wrong_abstention_share | 0.227 | 5 / 22 | handed_off_turn 33 |

unserved_answerable_share and wrong_abstention_share share part of their numerator - an abstention on a labelled-answerable request counts in both - and differ in their denominator: the first asks how much of what could be served was not, over the labelled-answerable requests whose turn was permitted to answer; the second asks how often an abstention was wrong, over every abstention the run produced. A run can move one without moving the other.

Denominators: unserved_answerable_share 87, wrong_abstention_share 22.

### Unserved answerable requests

By cause: abstained 5, lost_to_count_mismatch 1, misclassified 1.

- G002 [0] lost_to_count_mismatch: What are your clinic hours and locations?
- G003 [0] misclassified: Can I see a specialist if my GP hasn't sent anything over?
- G016 [0] abstained at rerank_floor: Is parking free?
- G081 [1] abstained at similarity_floor: What cards do you take?
- G096 [1] abstained at rerank_floor: Is parking free?
- G097 [1] abstained at rerank_floor: Does this clinic have a dentist or provide dental services?
- G100 [1] abstained at rerank_floor: What happens if you do not take Medicare?

### Not permitted to answer (handed off or silenced; out of the denominator)

G111, G112, G113, G114, G116, G117

### Wrong abstentions

- G016 [0] at rerank_floor: Is parking free?
- G081 [1] at similarity_floor: What cards do you take?
- G096 [1] at rerank_floor: Is parking free?
- G097 [1] at rerank_floor: Does this clinic have a dentist or provide dental services?
- G100 [1] at rerank_floor: What happens if you do not take Medicare?

### Degraded answers (answered_unreranked)

None.

### Answers on labelled gaps

- G020 [0] answered: Does the clinic accept Blue Cross for dental implants?
- G024 [0] answered: Do you do blood tests on Saturdays?
- G094 [0] answered: Are you open Sundays?
- G098 [1] answered: Is what a returning patient should bring different from what a new patient should bring?
- G129 [1] answered: How much does a follow-up visit cost out of pocket?

### Verdict distribution

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| verdict_distribution.answered | 0.800 | 88 / 110 | handed_off_turn 33 |
| verdict_distribution.answered_unreranked | 0.000 | 0 / 110 | handed_off_turn 33 |
| verdict_distribution.abstained_empty_corpus | 0.000 | 0 / 110 | handed_off_turn 33 |
| verdict_distribution.abstained_empty_pool | 0.000 | 0 / 110 | handed_off_turn 33 |
| verdict_distribution.abstained_similarity_floor | 0.045 | 5 / 110 | handed_off_turn 33 |
| verdict_distribution.abstained_rerank_floor | 0.155 | 17 / 110 | handed_off_turn 33 |

## Booking

| metric | value | numerator / denominator | excluded |
|---|---|---|---|
| tool_selection_correctness | 0.500 | 9 / 18 | - |
| end_to_end_task_success | 0.722 | 13 / 18 | - |

tool_selection_correctness is scored once per turn's booking half, against the union of the tools its booking requests are labelled with. The booking loop is handed all of a turn's booking requests at once, and the log attributes a tool call to the loop rather than to one request, so no per-request number is published. A tool called that no label names is not a miss. For a case with a scripted reply, the tools called in both of its turns count together, as one booking half: the loop is specified to ask in the first turn and act in the second.

### Tool-selection misses

- G048: missing reschedule_appointment; called list_my_appointments
- G049: missing list_practitioners; called nothing
- G053: missing check_availability; called nothing
- G062: missing book_appointment; called check_availability
- G090: missing cancel_appointment; called list_my_appointments
- G092: missing list_practitioners; called nothing
- G093: missing reschedule_appointment; called list_my_appointments, check_availability
- G095: missing book_appointment; called check_availability
- G102: missing book_appointment; called list_my_appointments, check_availability, cancel_appointment

### End-to-end failures

- G062, read after the last turn: expected, not found: William Osler +2d standing | found, not expected: none
- G090, read after the last turn: expected, not found: William Osler +4d 11:00 cancelled | found, not expected: William Osler 2026-03-06T11:00:00 standing
- G093, read after the last turn: expected, not found: William Osler +9d 10:00 standing | found, not expected: William Osler 2026-03-04T10:00:00 standing
- G095, read after the last turn: expected, not found: William Osler +4d standing | found, not expected: none
- G102, read after the last turn: expected, not found: William Osler +2d standing | found, not expected: none

## Runs and attempts

- Cases needing more than one attempt: 0
- Turns marked assistant_failed that still replied: none
- Streams that broke the service's contract and then settled: none
- Drive seconds: 654.76
- Score seconds: 0.0957

## Not computed

None.
