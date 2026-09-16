# The first full run — a record, not a baseline

This directory is phase 2b's frozen record (FR-048a): the first run of the golden harness over all
135 cases, committed as the harness wrote it.

- `run.json` — the run's conditions, label digests, corpus pin and entry-id map
- `cases/G001.json` … `cases/G135.json` — the per-case records every number was computed from
- `report.json`, `report.md` — the report `make eval-run` produced at the end of the run

**It is evidence, not a threshold.** Nothing may read a number here as the value a later run has to
beat. What counts as a regression, and how much run-to-run difference is noise, is phase 2c's call.
It needs a variance measurement that one run cannot give. No other run is committed (FR-048b), so a
later run belongs under `.run/evals/`, not beside this one.

Any number in `report.md` traces back to the case records without re-running anything.
`make eval-score` works only on a run under `.run/evals/`, so copy this directory to
`.run/evals/01M2JZZWJSHT31VPT99KEF9EFW/` first. Re-scoring spends nothing, and it refuses to run if a
scored label in `evals/golden/cases.json` has changed since (FR-044a).

## Conditions it was measured under

| | |
|---|---|
| Run id | `01M2JZZWJSHT31VPT99KEF9EFW`, 2026-09-15 16:56:49Z – 17:07:58Z (629 s of driving) |
| Code | `7a07a1d` on `main`, clean tree |
| Run clock | `2026-03-02T08:00:00` (a Monday) |
| Corpus | `ea83b6c4…a48b20a0`, matching `evals/golden/corpus.json`'s pin |
| Classification model | `claude-haiku-4-5-20251001` |
| Generation model | `claude-sonnet-5` |
| Embedding / rerank | `voyage-4-lite` / `rerank-3` |
| Retrieval pool | 25 |
| Similarity floor / cap | 0.3 / 5 |
| Rerank floor / cap | 0.58 / 3 |
| Max segments / context turns | 3 / 5 |
| Stack | local, `LOG_FORMAT=json make services-up`, chat + scheduler + Postgres + Qdrant |

Every model and threshold value above comes from the chat service's own `service.configured`
event (FR-047c), not from the harness's environment. A number measured under other values is not
comparable to these.

Two gates were checked on the same day against this code:

- **SC-004 (drifted corpus):** a one-character edit to the `referral` entry stopped
  `make eval-run CASES=G001` before any turn. The error named `referral` and no run directory was
  written.
- **SC-003 (re-scoring):** re-scoring this run with the stack down produced a `report.json`
  byte-identical to the one here, apart from `score_seconds`.

## What the run found

Everything below is a **finding for a person to adjudicate**. No label was changed because of this
run, and no threshold was moved (spec, Out of Scope). Where a label and the assistant disagree,
either could be wrong. Deciding which is the adjudication.

### Alignment and exclusions

185 of the 190 labelled requests aligned and 5 did not; none were excluded at alignment (SC-002).
The 5 unaligned requests come from the four cases whose produced request count differs from the
label: G002, G068, G077 and G117.

33 turns handed off. As FR-018a requires, they are scored for classification and excluded from the
retrieval and serving metrics. No case was excluded for any other reason. No fixture failed to
plant, no case needed a retry, no stream broke the service's contract, and no turn hit the segment
cap (`cap_bound`: none).

### The two zero-target metrics

**Unserved-answerable share: 6 / 87.**

| Case | Cause | Request | What happened |
|---|---|---|---|
| G016 | rerank floor | *Is parking free?* | Best rerank score 0.559 on `hours-location`, below the 0.58 floor |
| G096 [1] | rerank floor | *is parking free?* | 0.535 on `hours-location`. In the same reply, the other request's answer says the parking is paid |
| G097 [1] | rerank floor | *what about for a dentist?* | The segmenter rewrote the request as *"does this clinic have a dentist?"*, which changes the question. It then scored 0.520 |
| G100 [1] | rerank floor | *What if you do not?* | Best rerank score 0.377 on `out-of-network` |
| G002 | count mismatch | *hours and locations* | Split into two requests, both answered from the right entry. Lost only to alignment |
| G003 | misclassified | *specialist without a referral* | Classified as `booking`. The booking loop refused it as a policy question and called nobody |

The two parking cases fall just below the floor on the entry that became their evidence when
2a relabelled them for "fee parking". Whether the floor is too high, or the label asks more of that
phrase than it carries, is an open question. This record does not answer it.

**Wrong-abstention share: 4 / 21.** These are exactly the four rerank-floor abstentions above.
The other 17 abstentions (4 at the similarity floor, 13 at the rerank floor) landed on labelled
gaps.

### Answers on labelled gaps, and G020 (SC-010)

The harness reported five of the run's answers as landing on labelled gaps:

| Case | Request | Rerank | Content of the answer |
|---|---|---|---|
| **G020** | *Blue Cross for dental implants specifically?* | 0.754 on `insurance-plans` | Says Blue Cross is accepted and that implant coverage varies by plan. Offers the front desk |
| G094 [0] | *Are you open Sundays?* | 0.625 on `hours-location` | Says closed on Sundays, inferred from "Monday through Saturday" |
| G024 | *blood tests on Saturdays?* | 0.613 on `hours-location` | *"I don't have that information in the provided context. Please contact the clinic directly"* |
| G098 [1] | *different for a returning patient?* | 0.645 on `arrival-time` | Says the context does not specify a different list |
| G129 [1] | *follow-up visit price?* | 0.754 on `out-of-pocket-rates` | Says the context does not give a follow-up rate |

**G020's standing instruction is discharged by measurement.** The label predicted that the request
has to be stopped at the rerank floor. It was not: it cleared the floor at 0.754 on the insurance
entry and was answered. The answer states only what the entry supports and does not claim implant
coverage. So either G020's `gap` label no longer holds against the current corpus, or the rerank
floor lets a partly-covered question through. That is the adjudication PROVENANCE §2 asked for,
and this record does not make it.

G094 raises the same question in a milder form: can "Monday through Saturday" answer "open on
Sundays?"

**G024, G098 [1] and G129 [1] are a different finding, and not a label question.** Each request
passed both gates and got the verdict `answered`, but the generated answer itself declines. Because
the verdict is `answered`, no `corpus_could_not_answer` escalation was raised for any of the three
(no `escalation.raised` event in their records). Yet the replies to G098 and G129 tell the patient
the question *"has been forwarded to our staff"*. In those two turns the composer states that a
person was called when none was. G024's reply sends the patient to "contact the clinic" and also
calls nobody. The generated text of G024 and G129 also leaks the prompt's wording ("in the provided
context").

### Classification and segmentation

Request count accuracy is 131 / 135, intent accuracy 179 / 185, exact segmentation 125 / 135.

Two of the ten disagreements are small talk that the history turned into a request:

- **G031** (*"Thanks!"*, after a one-message history *"Can I book Monday at 9am?"*) was classified
  as the booking request from the history. The booking loop's model call then ended at `max_tokens`
  with no text (`booking.truncated`, `text_chars: 0`). The turn completed as `done` and stored an
  **empty assistant message**. Nothing was recorded as a failure and nobody was called. The same
  log event's `max_tokens` value is written as `***REDACTED***`: the log redaction treats the field
  as a secret.
- **G033** (*"ok"*, after *"What should I do before my visit?"*) was classified as that FAQ question
  and answered it again.

The history in both cases is a single patient message with no assistant reply between it and the
next one. A live conversation reaches that state only while the assistant is paused. Whether that
history is the right one to test small talk against is part of the adjudication.

The other disagreements:

| Case | Label | Produced | Note |
|---|---|---|---|
| G023 | `faq_question` (gap) | `small_talk` | The reply says it has no parking rates and calls nobody |
| G068 | `urgent_condition` | `urgent_condition` + `booking` | Still handed off. The label says urgency takes the whole turn |
| G077 | `urgent_condition` | `booking_for_another` + `urgent_condition` | Still handed off as urgent |
| G113 | `faq_question` + `booking_for_another` | `small_talk` + `booking_for_another` | *"Where are you?"* read as small talk. Handed off either way |
| G117 | `faq_question` + `urgent_condition` | `urgent_condition` only | The location question was dropped. Handed off either way |
| G130 | `faq_question` ×2 | `faq_question` + `unknown` | *"set up a payment plan"* read as unauthorized rather than a corpus gap. Staff were called either way, under a different cause |

### Booking

**Tool selection: 12 / 18. End-to-end task success: 16 / 18.**

Three misses share one shape. The label names a scheduling tool, but the assistant answered from
the practitioner roster, which is already in the booking loop's prompt (`booking.roster_read`), and
called no tool:

- **G049** (*which cardiologists*): label names `list_practitioners`
- **G092** (*Dr. Vesalius's specialty*): label names `list_practitioners`
- **G053** (*a cardiologist next week*): label names `check_availability`

The answers were right in all three: there is no cardiologist, and Vesalius practises dentistry.
Adjudicate whether a tool label should require a call that the design makes unnecessary.

**G048** (*move my Thursday appointment*, no scripted reply): the loop listed the appointments and
asked which new time the patient wanted. It never reached `reschedule_appointment`, which a
one-turn case cannot reach. The fixture expects the appointment still standing, and it was, so
task success passes while tool selection fails. The tool label and the one-turn fixture disagree
with each other.

**G062 and G095** fail on both metrics, the same way. After the first turn offered times, the
scripted reply said *"the earliest … time is fine, yes please book it"*. The loop checked
availability again and asked for confirmation of that specific slot (*"9:00am … Shall I book
that?"*) instead of booking. No appointment was made. G095's first reply also asked whether the
booking was for the patient themselves. Adjudicate whether the scripted reply is explicit enough,
or whether the loop is asking for one confirmation too many.

### Retrieval

Among the 85 aligned answerable requests scored at each stage, hit@1 is 83, hit@3 is 84 and hit@5
is 85 at both similarity and rerank, and MRR is 0.985. Every one survived the similarity gate.
rerank hit@5 is 1 by construction under a similarity cap of 5, as the report states. All four
wrong abstentions had their labelled entry among the chunks handed to the reranker. In three of
them (G016, G096, G100) that entry was the reranker's top chunk and still scored under the floor.
In G097 it came last of four, below `out-of-pocket-rates`, for the rewritten question.
