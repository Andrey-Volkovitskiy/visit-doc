# Quickstart — comparing two runs

How to read what a change did, and how much the numbers move on their own. Validation scenarios in
cost order, so everything free fails before anything paid starts.

## What this is, and what it is not

**It is a reporting tool, run on demand.** `make eval-compare` reads two stored runs and says what
moved. It spends nothing, needs nothing running, and always exits zero — a finding is in the report,
never in the exit code.

**It is not a gate.** No build fails on a metric, in CI or anywhere else. The per-commit gate the
roadmap once called for is dropped, for the reasons `docs/ROADMAP.md` Phase 2c records: 135 live
turns per push to decide red or green from numbers that move on their own is the most expensive
place to put the least trustworthy decision.

**It is not a verdict on a build either.** Without a band it reports movement and says so. With a
band it says whether a movement is inside the range five runs of one unchanged build produced —
which is a measurement, not a significance test.

Four things now exist side by side:

| | What it answers | Deterministic | Cost | Runs in CI |
|---|---|---|---|---|
| `make test-unit` | is the harness's own code right? | yes | free | yes |
| `make eval-run` | what does this build score? | no | live calls | no |
| `make eval-compare` | what moved between two runs? | yes | free | no |
| `make eval-band` | how much do the numbers move on their own? | yes, given the runs | free (the five runs are not) | no |

## Prerequisites

`compare` and `band` need **nothing running** — no chat service, no scheduler, no Postgres, no
Qdrant. They need two stored runs and `evals/golden/cases.json`.

Taking a run still needs the full stack with `LOG_FORMAT=json make services-up` and `make migrate`,
exactly as Phase 2b documents.

## Commands

```bash
make eval-compare BASE=<run> NEW=<run>            # what moved
make eval-compare BASE=<run> NEW=<run> BAND=<band> # ... and whether it is outside the noise
make eval-band RUNS=<id>,<id>,<id>,<id>,<id>       # measure the noise from five runs
```

`BASE` and `NEW` each take a run id under `.run/evals/` **or** a directory path — so the committed
2b run is usable as a baseline where it sits:

```bash
make eval-compare BASE=specs/012-golden-set-metrics/evaluation NEW=<the run you just took>
```

## Validation scenarios

### 1. The unit tier (free)

`make test-unit`. The comparison is driven from paired recorded runs under
`evals/harness/tests/fixtures/runs/`, the band from five recorded runs, and the purity test parses
every `comparison/*.py` for a forbidden import.

**Expected**: green, no live call, and `tests/comparison/test_purity.py` present — a new sibling
package inherits nothing from `tests/scoring/test_purity.py`, whose glob is not recursive.

### 2. A run compared with itself (free)

`make eval-compare BASE=specs/012-golden-set-metrics/evaluation NEW=specs/012-golden-set-metrics/evaluation`

**Expected**: every metric unchanged, no case movement, an empty condition delta, 135 cases common.
And — the point of running it — `git status` clean afterwards: scoring the committed baseline must
not rewrite the `report.json` inside it (research R2). This is the scenario that proves the pure
`score(...)` split landed.

### 3. Re-comparing is reproducible (free)

Run scenario 2 twice and diff the two `comparison.json` files.

**Expected**: identical apart from `compared_at` and `compare_seconds` (SC-003).

### 4. Offline, with everything down (free)

`make services-down`, then scenario 2 again.

**Expected**: identical output. The comparison reads files and nothing else (SC-002).

### 5. A label change stops it (free)

Edit one scored field of a case in `evals/golden/cases.json` — an `intent`, a `cites`, an
`answerable` — and re-run scenario 2.

**Expected**: it stops, names the case whose label moved, and reports no metric (FR-011). This is
2b's existing `LabelDigestMismatchError` doing the work; the comparison inherits the refusal rather
than restating it. Revert the edit.

### 6. A narrowed run (a few cents)

`make eval-run CASES=G016,G096,G097,G100` — the four wrong abstentions from the 2b run — then
compare it against the committed baseline.

**Expected**: the report says it covers 4 common cases before any metric, names the 131 cases on the
baseline side only, and every metric's numerator and denominator is over those 4 — not the
baseline's published 87 (FR-023).

### 7. A real change, end to end (a full pass) — optional, and reverted

This one exercises the tool against a change rather than against itself. **The phase ships no such
change**: moving a floor is out of scope here (spec, Out of Scope), so this is a local experiment
made, measured and reverted, and neither its runs nor its comparison is committed.

Move the rerank floor in your working tree, take a full run, compare, then revert the edit.

**Expected**: the condition delta names `rerank_floor` and both values, at the top, before any
metric (FR-008) — and the comparison still runs (FR-009), because this is the change under test. The
four wrong abstentions appear as verdict movements with their questions attached.

### 8. The noise band (five full passes)

Five `make eval-run`s against one unchanged build, then `make eval-band RUNS=…`.

**Expected**: a band naming every metric's five values and its range, and the cases that varied with
how many of the five took each outcome. Then compare two of those five runs with `BAND=`: every
movement between them is inside the observed range and none is marked outside it (SC-009) — which is
true by construction and is exactly why it is worth asserting.

### 9. A band that does not apply (free) — needs scenario 7

Compare the scenario-7 runs — taken under a moved floor — with the scenario-8 band. Skipped when
scenario 7 was skipped; the same refusal is asserted for free by the unit tier (FR-033).

**Expected**: the report says the band was measured under other conditions and marks nothing against
it (FR-033). A band from one set of thresholds says nothing about runs taken under another.

## Reading a comparison

In this order, and the report prints it in this order for the same reason:

1. **The condition delta.** If the floors or the models moved, every number below is a comparison of
   two different systems — which is fine when that was the change, and misleading when it was not.
2. **The coverage restriction.** A comparison over 4 cases is not a statement about the set.
3. **The exclusions.** A moved denominator is usually an exclusion moving, not behaviour.
4. **The metrics**, then **the case movements** beneath them — including the section for cases that
   moved under a metric that did not, which is the thing a delta cannot show.

Then, before acting: check whether the band saw those same cases vary on their own. A case that
flips in 2 of 5 runs of one build has not been changed by your edit.
