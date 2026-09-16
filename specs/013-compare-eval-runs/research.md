# Phase 0 — Research

What the comparison has to be built on, decided before any of it is written. Every finding here was
taken from the code and the artifacts 2b shipped, not from memory of them.

---

## R1 — What the comparison reads: the case records, not the stored report

**Decision**: A comparison **re-scores both runs itself** from their run record and case records, and
does not parse either run's stored `report.json`.

**Rationale**: Three things fall out of it at once.

1. **The committed baseline keeps loading, forever.** `report.json` is a serialized
   `Report`, whose models are `extra="forbid"`. The moment this phase adds a field anywhere in that
   tree — and per-case attribution is exactly such a field — the 2b report committed under
   `specs/012-golden-set-metrics/evaluation/` becomes a document the current code cannot parse. A
   comparison that never reads a stored report cannot be broken by its own additions.
2. **A narrowed comparison needs metrics recomputed, not quoted** (FR-023). The baseline's published
   `unserved_answerable_share` is 6/87 over 135 cases; against a 12-case run the honest number is
   whatever those 12 cases give on both sides. Only re-scoring can produce it.
3. **It is already guaranteed to agree.** 2b's SC-003, re-checked as this phase's T064 equivalent,
   is that re-scoring a stored run reproduces its report byte for byte apart from `score_seconds`.
   So re-scoring is not a second opinion about the baseline; it is the same opinion, recomputed.

**Alternatives considered**: *Parse both `report.json` files.* Cheapest, and it is what a reader
does by hand today — but it freezes the report schema for the life of the project, cannot
recompute over a subset, and gives no access to per-case attribution that the report does not
already publish. *Diff the raw case records only, computing nothing.* Independent of the scorer, but
it would re-derive "was this request served?" in a second place, and the two derivations would drift
— the defect the project's own "one value, one meaning" principle exists to prevent.

---

## R2 — `score_run` writes, and a comparison must not

**Finding**: `report.score_run(run_dir, cases)` is documented as "score the run stored in `run_dir`
**and write its report beside it**" (`evals/harness/src/golden_harness/report.py:182`). Its inputs
are a run directory and the labels; it reads `run.json`, checks label digests, reads every recorded
case file, scores the five families, and writes.

**Decision**: Split the write off. A pure `score(...)` returns a `Report` and touches no file; the
existing `score_run` becomes the thin wrapper that calls it and writes, so the `score` CLI is
unchanged. The comparison calls the pure one, twice.

**Rationale**: FR-002 requires the committed 2b run to be usable as a baseline **where it sits**.
With today's entry point, comparing against it would rewrite `report.json` inside
`specs/012-golden-set-metrics/evaluation/` — silently mutating the phase's frozen record, which
FR-048a of 2b exists to keep fixed. A comparison is a read; nothing about it may write into either
input. This is also the smallest change that makes scoring usable twice in one process.

**Alternatives considered**: *Copy each run to a temporary directory and score there.* Works, and is
what a shell script would do — but it makes a read cost a 2.5 MB copy, and leaves the writing
behaviour in place for the next caller to trip over. *Pass a `write=False` flag.* A boolean
parameter that changes whether a function has a side effect is the shape this project treats as a
smell; two functions say it plainly.

---

## R3 — Narrowing: scoring is driven by the run's own selection

**Finding**: `score_run` scores `run.selection.case_ids` intersected with the case files actually on
disk, and **raises** when a case file exists for a case the run did not select
(`report.py:214`). There is no parameter for scoring a subset.

**Decision**: The pure `score(...)` takes an optional restriction — the set of case ids to score —
defaulting to everything the run recorded. The comparison passes the intersection of the two runs'
recorded case sets.

**Rationale**: It is the mechanism FR-023 needs, and it is one parameter rather than a second
scoring path. Restricting the *input* rather than filtering the *output* matters: every denominator,
every exclusion count and every alignment total is then computed over the common cases by
construction, and nothing downstream has to be taught about the restriction.

**Consequence for alignment**: alignment's conservation check (every labelled request lands in
exactly one of aligned / unaligned / excluded, `ConservationError`) must hold over the restricted
set too — it is a property of the scored population, not of the number 190, so it survives
restriction unchanged. This is worth a test of its own, because a subset that broke conservation
would be indistinguishable from a scorer bug.

---

## R4 — Attributing a metric to the cases behind it

**Finding**: the scorers already publish per-item detail, and it is nearly complete:

| Family | What is published per item | File |
|---|---|---|
| Retrieval | `RetrievalScores.requests: list[RetrievalRequest]` — **every** scored request, with per-stage `rank` and `excluded` | `scoring/retrieval.py:144` |
| Classification | `disagreements: list[SegmentationDisagreement]` (labelled vs produced intents) and `cap_bound_cases` | `scoring/classification.py:41` |
| Serving | `unserved`, `wrong_abstentions`, `degraded_answers`, `answers_on_labelled_gaps`, `not_permitted_to_answer`, `verdict_distribution` | `scoring/serving.py:176` |
| Booking | `tool_selection_misses`, `task_failures` | `scoring/booking.py:118` |

Retrieval publishes the full population. The other three publish **the complete list of items on one
side** — the disagreements, the misses, the failures — and leave the passing side implied.

**Decision**: Derive each case's contribution from these lists plus the scored population: an item is
in the numerator when it is *not* in the family's shortfall list and *is* in the scored denominator.
Where a request-level verdict is needed and no list carries it, read it from the case record's
`request_outcomes`, which is the authority 1h created for exactly that question.

**Rationale**: No scoring logic is re-implemented, so a movement the comparison reports and the
metric it sits under cannot disagree. It also means the comparison inherits every exclusion rule
already tested in 2b's suite rather than restating it.

**Alternatives considered**: *Add a full per-case attribution structure to every scorer.* Cleaner to
consume, and tempting — but it is a change to five tested modules and to the report shape, in a
phase whose whole point is to read what 2b produced. If the derivation above proves awkward for one
family, adding attribution to **that** family is the smaller move, and R1 means the report shape can
change safely.

---

## R5 — What counts as a movement, and which way is "better"

**Decision**: Movements are grouped by what changed, and a direction is attached only where the
label makes one available:

| Group | Source of both states | Direction |
|---|---|---|
| Verdict | `request_outcomes` per request | From the label: an abstention on a labelled-answerable request is worse than an answer; an answer on a labelled gap is worse than an abstention |
| Retrieval rank | `RetrievalRequest.similarity` / `.rerank` | Lower rank is better; scored → excluded is directionless |
| Segmentation / intents | produced segmentation vs label | Agreement with the label is better |
| Tool selection | `tool_selection_misses` | Fewer misses is better |
| Database state | `task_failures` | Success is better |
| Exclusion | `excluded` on the case record | **Directionless** |

**Rationale for the directionless cases**: FR-019 forbids forcing a change into better or worse when
it is neither. Two real examples from the 2b run make the rule concrete. An abstention that moves
from `abstained_similarity_floor` to `abstained_rerank_floor` is the same outcome for the patient
and the same miss for the metric — only the gate differs. And a case that becomes excluded has not
behaved better or worse; it has left the denominator, which is a different fact and the one FR-021
insists be reported separately.

---

## R6 — Conditions: report the delta, refuse only on labels

**Finding**: every run records its conditions from the chat service's `service.configured` event —
the model ids, the floors, the caps, the pool size, the segment cap and the context turns — and its
per-case label digests. 2b's `score_run` already raises `LabelDigestMismatchError` when a digest
moved.

**Decision**: The comparison reports the condition delta above every metric (FR-008) and continues
(FR-009); it stops on a label digest difference (FR-011), and gets that stop for free by scoring
each run through the existing entry point, which already refuses.

**Rationale**: The two are asymmetric because of what they mean. A threshold that differs is usually
*the change under test* — refusing it would remove the tool's main use. A label that differs means
the two runs were scored against different questions, and no arithmetic over them is meaningful.
Getting the second from the existing check rather than a new one also guarantees the comparison and
`make eval-score` can never disagree about which labels are acceptable.

**A corollary worth stating**: the corpus hash is a condition, not a label, so it is reported rather
than refused (FR-010). A corpus edit does not change what a label *says*; it changes what the label
is *about*, which is why 2a pinned it and why the report has to put it where a reader cannot miss it.

---

## R7 — The noise band: five runs, an observed range, no statistics

**Decision**: The band is the per-metric `[min, max]` observed across five full runs of one
unchanged build, plus a per-case record of what varied and how often each outcome occurred. It is
stored as its own artifact with the conditions it was measured under, and every report citing it
says it is five observations.

**Rationale**: Five points support a range and a frequency count — "this case abstained in 2 of 5
runs" is a useful, honest sentence. They do not support a standard deviation, a confidence interval
or a significance test, and computing one would dress five samples as a distribution. The spec's Out
of Scope says so and FR-032 enforces it in the output, so the restraint is checkable rather than
merely intended.

**On building it**: refuse to build a band from runs whose conditions or label digests differ
(FR-030). A band assembled across two builds measures the change, which is the one thing it must not
do.

**Alternatives considered**: *A per-metric standard deviation over five runs.* One number instead of
two, and familiar — but five samples give it an enormous error of its own, and a reader who sees
"σ = 0.01" will treat it as a distribution parameter. *A band from three runs* was the first
proposal and was raised to five deliberately, for the frequency counts above.

---

## R8 — Where the comparison's own output goes

**Decision**: printed to the terminal, and written as a machine-readable record under the local
artifacts directory (`.run/evals/`), in a location named after the two runs compared, never inside
either input run's directory.

**Rationale**: FR-025 wants both forms and FR-026 wants the inputs recorded. Writing beside the
*new* run would be the obvious choice and is wrong for one reason: the new run may be the committed
baseline under `specs/`, or a run someone else will later use as a baseline, and a read should not
add files to its input. The local artifacts directory is already the place the harness owns and
`.gitignore` covers.

---

## R9 — Purity, and how it stays enforced

**Finding**: 2b's `tests/scoring/test_purity.py` exists precisely to keep the scorer offline, and
the spec (FR-004) extends the same rule here.

**Decision**: The comparison lives in its own module beside `scoring/`, and the existing purity test
is extended to cover it: no import of the HTTP client, the gRPC stack, the database layer or
`driver/`, checked statically rather than by convention. The offline guarantee is then demonstrated
the way 2b demonstrated it — by running the comparison with the whole stack down (SC-002, and
quickstart scenario).

**Rationale**: A comparison that could reach a service would eventually reach one, and the property
this phase sells — *re-readable forever, costs nothing* — would quietly stop being true. Static
enforcement is what makes it survive a later contributor who has not read this document.

---

## R10 — Exit status

**Decision**: the command returns zero in every case it completes, including when it finds a
movement outside the band, and there is no flag to make it do otherwise.

**Rationale**: FR-006, and the roadmap decision behind it. The exit code is the one surface a CI step
would attach to without anyone deciding to build a gate; leaving it constant means a gate has to be
a deliberate act. Note the distinction the implementation must keep: *refusing to compare* (mismatched
labels, no common cases) is a failure to produce a report and follows the CLI's existing error
path, which already exits 1 for every `_REPORTED_FAILURES` case. A **finding** is never that.
