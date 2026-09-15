# Contract: The metrics

Every metric's numerator, denominator and exclusions, stated so a test can be written from this page
alone. Each is published with all three (FR-045); a metric whose denominator is 0 is published as
**`not_measured`**, never as 0% and never as 100%.

## Alignment, which everything else rests on

For each case, the produced segmentation (from `intent.classified`) is compared to the labelled one:

| State | When |
|---|---|
| `excluded` | the case carries a case-scoped `ExclusionReason` — it never reaches alignment |
| `aligned` | produced request count **equals** labelled request count; requests pair by position (FR-015) |
| `unaligned` | the counts differ; **all** of the case's labelled requests are unaligned (FR-016) |

**Conservation** (SC-002): over a run, `aligned + unaligned + excluded` labelled requests equals the
run's labelled total — 190 for a full run. The scorer asserts this about itself; it is not left for
a reader to check.

Exclusion reasons carry a scope (FR-018a). `run_error`, `silenced_turn`, `cancelled_turn`,
`missing_log_slice`, `unresolvable_fixture` and `outcome_unknown` exclude a case from **every**
metric, and no metric quietly readmits one. `handed_off_turn` is the deliberate exception: the turn
classified before it handed off, so it is aligned or unaligned like any other case and scored for
classification (§A), and it is excluded from retrieval (§B) and serving (§C), which have nothing to
score on it.

---

## A. Classification

### A1. Request-count accuracy (FR-019)

- **Numerator**: cases whose produced request count equals the labelled count.
- **Denominator**: cases not excluded.

### A2. Intent accuracy (FR-020)

- **Numerator**: aligned requests whose produced intent equals the labelled intent.
- **Denominator**: aligned requests.
- **Published beside it**: the unaligned count, so a high accuracy over a small aligned set cannot
  be read as a high accuracy over the set.

### A3. Exact segmentation match (FR-021)

- **Numerator**: cases that are aligned **and** whose intents match at every position.
- **Denominator**: cases not excluded.

A3 is the roadmap's "expected number of requests, their order, and each one's intent" as one value.
A1 and A2 are the two halves that say which of them failed — the reason all three exist rather than
only the strict one.

A produced intent of `classification_failed` is never scored: the case is `run_error` (FR-023).

---

## B. Retrieval — computed **per stage**, never pooled (FR-025)

Two stages, from two events:

| Stage | Ranked list | Source |
|---|---|---|
| `similarity` | the candidate pool in descending similarity | `faq.retrieval_completed.candidates` |
| `rerank` | the reranked list in descending rerank score | `faq.reranking_completed.scores` |

**Scored over**: aligned FAQ requests whose label is `answerable: true` (FR-028). A gap cites
nothing, so there is no rank to look for.

A labelled `cites` entry id is resolved to the session's own FAQ entry id before any comparison
(FR-012); a chunk belongs to a cited entry when its `entry_id` matches.

### B1. hit@k, at k = 1, 3, 5 (FR-026)

- **Numerator**: scored requests where a chunk of **any** cited entry appears in that stage's top k.
- **Denominator**: scored requests for that stage.

3 and 5 are the caps the pipeline actually applies — the rerank cap and the similarity cap. 1 is
what the stage got right outright.

### B2. MRR (FR-027)

- **Value**: the mean over scored requests of `1 / rank` of the **first** chunk belonging to any
  cited entry; `0` for a request where no cited chunk is ranked at all.
- **Denominator**: scored requests for that stage.

Rank is 1-based and is the position in that stage's own ordering.

### Stage exclusions

A request whose turn raised `faq.reranking_unavailable` is excluded from **rerank-stage** metrics
with that reason and is still scored for the similarity stage (FR-029). A request whose turn issued
no search at all — an empty corpus — has no candidate list and is excluded from both.

A request none of whose cited chunks is in its segment's `faq.similarity_gate.kept` is excluded from
**rerank-stage** metrics as `not_reached_reranker`, and is still scored for the similarity stage
(FR-029a). The reranker is handed exactly what the gate kept, so a chunk the gate dropped was never
the reranker's to rank, and scoring it as a rerank miss would charge the floor's loss to the
reranker. With it excluded, rerank-stage **hit@5 is 1 by construction** while the similarity cap is
5 or less, and the report says so beside the value rather than publishing it as a finding.

A handed-off turn's requests are excluded from both stages as `handed_off_turn` (FR-018a). A labelled
`faq_question` produced under another intent never reached the FAQ half, so no search was issued
for it: it is excluded from both stages as `not_routed_to_faq`, and never as `no_search`, which
means a turn whose corpus was empty (FR-018a).

### B3. Similarity-gate survival (FR-029a)

- **Numerator**: similarity-scored requests with at least one cited chunk in `faq.similarity_gate.kept`.
- **Denominator**: requests scored for the similarity stage.

What the gate lost, as its own number: its complement is exactly the set the rerank stage excludes as
`not_reached_reranker`.

---

## C. Serving

### C1. Unserved-answerable share (FR-031) — target 0

- **Denominator**: labelled-answerable FAQ requests in cases whose turn was **permitted to answer**.
- **Numerator**: those that did not receive an answered verdict — whether the turn abstained on them,
  or the case was unaligned and the request never became an outcome at all.
- **Broken down** by cause: abstained; lost to a count mismatch; and misclassified — a case aligned by
  count whose produced request at that position is not a `faq_question`, so the question never
  reached the FAQ half. FR-031 names the first two at minimum; the third is kept (decided 2026-09-14)
  because each cause points at a different fix — the corpus and floors, the segmenter, the classifier
  — and filing a misclassified question under either of the others would say something untrue.

**Excluded from the denominator** (FR-031a): any case whose turn a stopping cause silenced or handed
off. Six cases in `overriding-segment` pair an answerable question with an urgent condition,
distress, a request for a person, or a booking for another, and *not* answering those is what spec
009 requires. A case pairing an answerable question with an `unknown` request is **not** excluded:
`not_authorized` escalates without silencing, so the question is still expected to be answered.

This is deliberately the one metric whose denominator comes from the label rather than from the
aligned requests: what it measures is an answer the patient did not receive, and why the system lost
it does not change that they did not receive it.

### C2. Wrong-abstention share (FR-032) — target 0

- **Denominator**: all abstentions the run produced.
- **Numerator**: those aligned to a labelled-answerable request.

C1 and C2 share part of a numerator and differ in denominator, and the report says so (FR-033). C1
asks how much of what could be served was served; C2 asks how often an abstention was wrong. A run
can move one without moving the other.

Both list the requests behind a non-zero value, by case id and question (FR-034).

### C3. Verdict distribution (FR-036)

A count per `FaqVerdict` value, all six. `answered_unreranked` counts as **answered** for C1 and C2
and is reported separately (FR-035): a degraded answer must never be silently counted as a reranked
one, and must never be counted as a corpus gap.

---

## D. Booking

### D1. Tool-selection correctness (FR-042)

- **Scored per turn's booking half**, not per request: the booking loop is handed all of a turn's
  scheduling requests at once, and `booking.tool_called` attributes a call to the loop rather than to
  one request. The report says so rather than publishing a per-request number the log cannot support.
- **Numerator**: turns whose booking half called every tool in the union of its booking requests'
  labelled tools.
- **Denominator**: turns with at least one booking request, not excluded.

A tool called that no label names is **not** a miss (FR-043): the label is a required subset, not a
sequence, so a `check_availability` before a `book_appointment` is correct behaviour.

For a case with a scripted `reply` (FR-037b) the calls of both turns count together, as one booking
half: the loop is specified to ask in the first and act in the second.

### D2. End-to-end task success (FR-041)

- **Numerator**: fixture-carrying cases whose appointments after the last turn match `expect` under
  the perfect matching in [`fixture-label.md`](./fixture-label.md) — and, for a case with a `reply`,
  whose appointments after the first turn still matched `given` as standing (FR-037b).
- **Denominator**: fixture-carrying cases, not excluded.
- **Detail**: every failure names the unmatched `expect` entries and the unaccounted-for
  appointments separately (FR-041a) — too little and too much are opposite defects — and, for a
  reply case, which of the two reads failed.

A case whose fixture could not be planted is `unresolvable_fixture` and is excluded, never a failure.
A case whose turn's outcome is unknown (FR-007d) is `outcome_unknown` and excluded the same way: its
appointments are stored for a reader and never scored.

The post-state is read **before** the harness cancels what the case left standing (FR-041b), so the
cleanup never reaches a score. It is what keeps one case's appointments out of the next case's
slots: without it, a run's end-to-end number would depend on the order the cases ran in.

---

## What the report carries besides the metrics

The run's conditions and selection verbatim (FR-047), the alignment totals, the `cap_bound` count
(FR-024), the number of cases that needed more than one attempt (FR-007c), the cases whose turn was
marked `assistant_failed` and still replied (FR-007e), the cases whose stream broke the service's
contract and then settled, each with which turn — a stream that only broke off or timed out is not
among them (FR-041d), the list of metrics
**not** computed (FR-046), and two durations (FR-047a):

| Duration | What it is |
|---|---|
| drive | the sum of the cases' own elapsed times — what the run actually cost in time. Not the wall-clock span, which a resumed run inflates by an interval nobody spent. |
| score | how long the scoring pass took. Reported because FR-044 claims re-scoring is cheap, and a cost claim should be checkable. |

Neither is a metric and neither has a target. The drive figure is an input to 2c's cadence decision;
the score figure is evidence about the scorer, not about the assistant.
