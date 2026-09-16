# Contract: the noise band

What the band is measured from, what it records, what it refuses, and — the part that matters most —
what it may never be read as.

## What it is

The spread of the golden set's metrics across **five full runs of one unchanged build**. It answers
one question: *how much do these numbers move when nothing changes?* Until it exists, every
statement of the form "this change made things better" rests on nothing, because no one has ever run
the set twice against the same code.

## What it is not

- **Not a threshold.** No run is required to beat it. Nothing in this phase or a later one may read
  a band value as a pass mark (FR-038).
- **Not a statistical interval.** Five observations give an observed range and a frequency count.
  They do not give a standard deviation, a confidence interval or a significance test, and the band
  publishes none (research R7, spec Out of Scope). Every report citing it says so (FR-032).
- **Not a licence to dismiss a finding.** A movement inside the observed range is *not shown to be
  noise*; it is *not shown to be more than noise*. The difference matters when the movement is on a
  metric whose target is zero, and the renderer's wording keeps it: **within the observed range**,
  never "just noise".

## How it is measured

1. Bring the stack up as 2b requires: `LOG_FORMAT=json make services-up`, databases migrated.
2. Take five full runs with `make eval-run`, changing nothing between them. Restarting the services
   between runs is allowed — sameness is enforced by the recorded conditions, not by uptime.
3. `make eval-band RUNS=<five ids>`.

Roughly an hour of driving and five times a full run's model cost. This is the phase's one
deliberate live-call expense.

## What it records

Per the `NoiseBand` model in [`data-model.md`](../data-model.md):

- **Per metric**: every one of the five values, in run order, plus the lowest and highest. Keeping
  all five costs five floats and lets a later reader see whether four runs agreed and one did not —
  a distinction a range alone destroys.
- **Per case**: each case or request observed to differ between any two of the five runs, with each
  observed state and how many of the five produced it. This is the frequency count that five runs
  buy over three: *"G024 answered in 4 of 5, abstained at the rerank floor in 1"* is a sentence a
  range cannot make, and it is the one that tells a flaky case from a stable one.
- **The conditions**, the corpus hash, the run clock and the case set, so a later comparison can
  tell whether the band applies to it at all.

## What it refuses

A band asserts that the five runs differ *only* by chance. Anything that contradicts that is a
refusal, not a warning:

| Refusal | Reason |
|---|---|
| Not exactly five runs | FR-027. A band from another count is a different measurement, and the record must not quietly say five |
| One run named more than once | one run five times agrees with itself in every field below, and produces a zero-wide range per metric and an empty variation list that renders as "every case produced the same outcome in all five runs". That is one observation wearing five, and a comparison marked against it would read a real movement as outside measured noise on the strength of a measurement nobody took (FR-027) |
| A run that is not a full-set run | FR-027 measures the noise over all 135 cases. Five narrowed runs agree with each other perfectly well and still measure something else, so the check is each run's own selection, not merely that the five match |
| A run that recorded fewer cases than it selected | the selection says what a run meant to drive and the records say what it has, and it is the records every metric is computed over. A run that stopped partway measures each metric over a population of its own, so its value beside four full ones would put a population difference inside a range that claims to be run-to-run noise — while the band's `case_ids` named a set it never measured. This is the one place a band is stricter than a comparison, which reports such a run as incomplete and carries on (FR-012): a comparison over a narrower case set is still a comparison, whereas a band's whole claim is that its five runs differ by chance alone |
| Conditions differ in any field | a band across two builds measures the change, not the noise (FR-030) |
| Corpus hash differs | the runs answered against different text |
| Run clocks differ | every scheduling fixture's day offset is resolved against the run clock, so two clocks are two sets of expected appointments — a difference in what was asked, not in what chance did with one question |
| Label digests differ | the runs were scored against different questions |
| Case sets differ | a metric's range would be taken over different populations |

Each refusal names the field and the run, so the fix is obvious.

## How a comparison uses one

- A band applies only when its conditions, corpus hash, run clock and case set match **both** runs
  being compared. Otherwise the comparison says the band was measured under other conditions and
  marks nothing against it (FR-033). The clock is among them for the same reason the band refuses
  five runs that differ in it: a band measured on another clock ranged over another set of expected
  appointments.
- A metric the band holds no observation for is reported unmarked — never assumed stable (FR-034).
- A case the band saw vary on its own is marked wherever a comparison reports it as moved (FR-035),
  which is how a reader tells "this change did that" from "this case does that anyway".

## The record this phase commits

`specs/013-compare-eval-runs/evaluation/` holds the five runs' reports, the band built from them,
and one worked comparison between two of the five (FR-037). Its README states the conditions, that
the band is evidence of self-movement rather than a threshold (FR-038), and which cases the five
runs showed to be unstable — the most directly useful thing the measurement produces, since those
are the cases every future comparison has to read with suspicion.

**The five runs' case records are not committed** (SC-011, decided 2026-09-16): five full runs are
some 12 MB of JSON beside 2b's 2.5 MB, and what a reader needs from a band is the spread and the
list of unstable cases, both of which the band itself carries. The cost is real and is stated rather
than discovered — the band's per-case variation cannot be traced to the turn that produced it after
the local runs are gone, and a question of that kind needs five fresh runs.

Only this band is committed, as 2b commits only its one run: promoting another is a deliberate act
with a reason, never a side effect of measuring one.
