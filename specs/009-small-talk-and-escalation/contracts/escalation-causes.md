# Contract: Escalation Causes

One table governs every cause. Adding a cause means adding a row, and a row that is missing an entry
in any column is a defect, not a default.

| Cause | Silences the assistant | Message mark | Mark clears on a staff reply | Precedence |
|---|---|---|---|---|
| `urgent_condition` | yes | `urgent_condition` | yes | 1 |
| `distress` | yes | `distress` | yes | 2 |
| `patient_asked_for_person` | yes | `patient_asked_for_person` | yes | 3 |
| `booking_for_another_person` | yes | `booking_for_another_person` | yes | 4 |
| `not_authorized` | no | `not_authorized` | yes | 5 |
| `corpus_could_not_answer` | no | `corpus_could_not_answer` | **no** | 6 |
| `assistant_failed` | no | `assistant_failed` | **no** | 7 |

## Rules

1. **One mark per message.** A turn may record several causes; the lowest precedence number present
   becomes the mark. Every recorded cause is logged, including the discarded ones.
2. **Silencing is decided by the set, applied once.** As today, requests are collected during the
   turn and applied after the graph completes: the turn that escalates still speaks, and silence
   begins with the patient's *next* message.
3. **A conversation's reason is write-once.** The guarded `UPDATE` that sets it will not overwrite
   an earlier one. This phase adds no upgrade path, and none is reachable: a message arriving while
   the assistant is silent is never classified (FR-025a), so no stronger cause can be raised in a
   conversation that is already stopped.
4. **A mark and a cause are the same string.** The mark is derived from the cause, so they cannot
   disagree.
5. **`unanswered` is not a cause.** It remains a mark with no escalation behind it, set by the
   silence gate, cleared by a staff reply.
6. **Taken-over turns apply nothing.** Unchanged: if a person answered while the turn ran, every
   recorded cause is logged and none applied.

## What each cause costs

| Cause | Model calls | Retrieval | Scheduling calls |
|---|---|---|---|
| `urgent_condition`, `distress`, `booking_for_another_person`, `patient_asked_for_person` | classification only | none | none |
| `not_authorized`, solo | classification only | none | none |
| `not_authorized`, accompanying a servable intent | classification + whatever the servable path costs + the merge | as that path does | as that path does |
| `corpus_could_not_answer`, `assistant_failed` | unchanged | unchanged | unchanged |
