---

description: "Task list for Comparing One Version Against Another (Phase 2c)"
---

# Tasks: Comparing One Version Against Another (Phase 2c)

**Input**: Design documents from `/specs/013-compare-eval-runs/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: Per the constitution's Test-Driven Development principle (VIII), every test task precedes
the implementation it covers, and the tests are **observed failing** before that implementation is
written.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: the user story the task belongs to (US1, US2, US3); setup, foundational and polish
  tasks carry none

## Path conventions

The harness is an existing uv workspace member. New code lives at
`evals/harness/src/golden_harness/comparison/`, its tests at `evals/harness/tests/comparison/`, and
its recorded fixtures at `evals/harness/tests/fixtures/runs/`. Paths below are repo-relative.

## Things that are easy to get wrong here

1. **`score_run` writes** (`report.py:182`). Until T006 lands, any code path that scores the
   committed 2b baseline rewrites `specs/012-golden-set-metrics/evaluation/report.json`. Do not
   score a baseline from a test or a script before then.
2. **Never read a run's `report.json`** (research R1). The comparison's inputs are `run.json` and
   `cases/*.json`. A task that parses a stored report has taken the wrong route, however convenient.
3. **The purity test is not inherited.** `tests/scoring/test_purity.py` globs `scoring/*.py`
   non-recursively; `comparison/` is protected only by T008.
4. **Five band runs, not "at least five"** (FR-027). A band from another count is a different
   measurement.

---

## Phase 1: Setup

**Purpose**: the package skeleton the rest of the phase fills.

- [X] T001 Create the package `evals/harness/src/golden_harness/comparison/__init__.py` (empty, as
  `scoring/__init__.py` is — every symbol is imported from its own module) and the test package
  directory `evals/harness/tests/comparison/`
- [X] T002 Add the two Makefile targets `eval-compare` and `eval-band` in [`Makefile`](../../Makefile)
  beside `eval-run`/`eval-score`, passing `BASE`/`NEW`/`BAND` and `RUNS` through per
  [`contracts/cli.md`](./contracts/cli.md), with a comment naming this spec — the targets will fail
  until T021 wires `compare` and T030 wires `band`, which is the expected state during the phase

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: the scoring split every story depends on, the models they all build, and the guard that
keeps the package offline. No user story can start until this phase is done.

### The pure scoring entry point (research R2, R3)

- [X] T003 Write failing tests in `evals/harness/tests/test_report.py`: a new pure entry point scores
  a stored run and **writes nothing** — asserted by comparing the run directory's file list and
  every file's mtime and bytes before and after — while `score_run` still writes `report.json` and
  `report.md` exactly as it does today; both return an equal `Report` for the same input
- [X] T004 Write failing tests in `evals/harness/tests/test_report.py` for the restriction parameter:
  scoring a recorded run restricted to a subset of its case ids computes every metric, alignment
  total and exclusion count over **only** those cases; an empty restriction is refused rather than
  scoring everything; a restriction naming a case the run did not record is refused, naming it
- [X] T005 Write a failing test in `evals/harness/tests/scoring/test_alignment.py` that alignment's
  conservation holds over a restricted population — every labelled request of the restricted cases
  lands in exactly one of aligned / unaligned / excluded — so a subset that broke conservation is
  distinguishable from a scorer bug (research R3). Observe T003–T005 fail
- [X] T006 Split `score_run` in `evals/harness/src/golden_harness/report.py` into a pure
  `score(run_dir, cases, *, only=None) -> Report` that touches no file, and keep `score_run` as the
  wrapper that calls it and writes both artifacts; the `score` CLI path and its output are unchanged.
  Observe T003–T005 pass

### The comparison models (data-model.md)

- [X] T007 [P] Write failing tests in `evals/harness/tests/comparison/test_model.py` for the models of
  [`data-model.md`](./data-model.md) — `RunSide`, `ConditionChange`, `ConditionDelta`,
  `MovementDirection`, `MetricMovement`, `BandVerdict`, `MovementGroup`, `CaseMovement`,
  `CaseCoverage`, `AlignmentMovement`, `ExclusionCount`, `Comparison`: every model rejects an extra
  field and is
  frozen; `MetricMovement` with either side `None` is `not_comparable` and refuses a numeric delta;
  a `CaseMovement` may not carry `unchanged`; `CaseCoverage.restricted` follows from the exclusive
  lists; `RunSide.complete` follows from `selection` against `recorded_cases`. Observe them fail
- [X] T008 [P] Write `evals/harness/tests/comparison/test_purity.py`, applying the forbidden roots
  (`httpx`, `grpc`, `sqlalchemy`, `golden_harness.driver`) to every `comparison/*.py` by the same
  static-AST mechanism `tests/scoring/test_purity.py` uses, including that test's self-check of the
  detector over the seven import spellings. Observe it fail for a package that does not yet exist
- [X] T009 Implement `evals/harness/src/golden_harness/comparison/model.py` per
  [`data-model.md`](./data-model.md), importing `ExclusionReason`, `RunConditions`, `CorpusRecord`
  and `Selection` from the record/cases modules and `Metric`/`Exclusions` from `scoring.metric`
  rather than redefining any of them. Observe T007 and T008 pass

### The recorded fixtures every story is tested from

- [X] T010 [P] Write the paired fixture runs under `evals/harness/tests/fixtures/runs/compare/`: a
  small baseline run (a handful of cases, full `run.json` + `cases/*.json` in the shapes
  `specs/012-golden-set-metrics/contracts/run-record.md` defines) and variants differing in exactly
  one respect each — one request's verdict, one request's retrieval rank, one case's produced
  segmentation, one booking's tool call, one booking's post-state, one case's `excluded`, one
  condition field, one corpus hash, one label digest, one case absent. Data only, no code

**Checkpoint**: `uv run pytest evals/harness/tests` is green, `score_run` behaves exactly as before,
and the committed 2b run can be scored without its directory changing.

---

## Phase 3: User Story 1 — See what moved between two runs (Priority: P1)

**Goal**: name two stored runs and learn what changed — conditions first, then every metric with
both sides' numerators and denominators, then the cases behind each movement.

**Independent test**: compare the committed 2b run against a second full run of the same build;
every metric appears with both values, the condition delta is empty, and each reported case movement
is confirmable in both runs' case records.

### Tests for User Story 1

- [X] T011 [P] [US1] Write failing tests in `evals/harness/tests/comparison/test_conditions.py`: the
  delta names each differing `RunConditions` field with both values in declaration order; an
  identical pair yields an empty delta reported as such; a differing corpus hash appears as a change
  **and** sets `corpus_moved` (FR-010); a condition difference never stops the comparison (FR-009)
- [X] T012 [P] [US1] Write failing tests in `evals/harness/tests/comparison/test_metrics.py`: every
  metric either run published appears, unchanged ones included (FR-013, FR-014); direction follows
  the declared polarity table, with the two zero-target shares improving downward; a moved
  denominator sets `denominator_moved` independently of the numerator (FR-016); a metric
  `not_measured` on one side, or absent from one side's `not_computed`, is `not_comparable` and no
  delta is computed (FR-015)
- [X] T013 [P] [US1] Write failing tests in `evals/harness/tests/comparison/test_cases.py`, one per
  `MovementGroup` against the T010 fixtures: a verdict movement is directed against the label's
  `answerable` and an abstention moving between gates is `directionless` (FR-019); a retrieval rank
  improvement is a lower rank and scored↔excluded is directionless; a segmentation movement is
  directed by agreement with the label; tool-selection and database-state movements are derived from
  the misses and failures lists; an exclusion movement is **always** directionless and is reported
  with both states (FR-021); an unchanged case produces no movement; and every movement carries the
  metric names it changed the contribution to, so each metric can name its movements (FR-017,
  `CaseMovement.affects`)
- [X] T014 [US1] Write failing tests in `evals/harness/tests/comparison/test_compare.py`: comparing a
  run with itself yields every metric unchanged, no case movement and an empty delta (US1 AS7);
  differing label digests stop the comparison naming the cases and reporting no metric (FR-011); a
  movement under a metric whose value did not change is still reported (FR-018); comparing the same
  pair twice produces an identical `Comparison` apart from `compared_at` and `compare_seconds`
  (FR-007); neither input directory is written to; and the comparison **never reads either run's
  `report.json`** (FR-003, research R1) — asserted by renaming both runs' `report.json` in the
  fixture copy and finding every movement unchanged, which no amount of import checking can show
- [X] T015 [P] [US1] Write failing tests in `evals/harness/tests/comparison/test_render.py` for the
  order [`contracts/comparison-record.md`](./contracts/comparison-record.md) fixes — runs, condition
  delta, coverage, band, alignment and exclusions, metrics, case movements, the unchanged-metric
  churn section — and for its language rules: without a band the words `regression`, `better` and
  `worse` do not appear about a metric (FR-036), and "not computed" and "not measured" are rendered
  as themselves; and that the summary renders from a stored `Comparison` alone, with neither run
  directory present, so the machine-readable form is sufficient to re-render the human-readable one
  (FR-025). Observe T011–T015 fail

### Implementation for User Story 1

- [X] T016 [P] [US1] Implement `evals/harness/src/golden_harness/comparison/conditions.py` — the
  condition delta and `corpus_moved`. Observe T011 pass
- [X] T017 [P] [US1] Implement `evals/harness/src/golden_harness/comparison/metrics.py` — metric
  pairing over `METRICS_BY_FAMILY` order, the polarity table as data, direction,
  `denominator_moved`, and `not_comparable`. Observe T012 pass
- [X] T018 [US1] Implement `evals/harness/src/golden_harness/comparison/cases.py` — the six movement
  groups, each derived from the scored per-item lists and the case records per research R4, never by
  re-implementing a scoring rule, and each carrying the metrics it affects. Observe T013 pass
- [X] T019 [US1] Implement `evals/harness/src/golden_harness/comparison/compare.py` — score both runs
  through T006's pure entry point, assemble `Comparison`, and let the existing
  `LabelDigestMismatchError` refusal propagate rather than restating it (research R6). Observe T014
  pass
- [X] T020 [US1] Implement `evals/harness/src/golden_harness/comparison/render.py` — the summary in
  the contract's order and language. Observe T015 pass
- [X] T021 [US1] Add the `compare` subcommand to `evals/harness/src/golden_harness/cli.py` per
  [`contracts/cli.md`](./contracts/cli.md): `--base`, `--new`, `--artifacts`, `--labels`, resolved
  through the existing `resolve_run` (`--band` is declared here but inert until T031 gives it an
  effect); write `comparison.json` and `comparison.md` under
  `<artifacts>/comparisons/<base>__<new>/` (research R8) and print the summary
- [X] T022 [US1] Add the comparison's new error types to `cli.py`'s `_REPORTED_FAILURES` tuple so a
  refusal prints as a named sentence rather than a traceback, and write failing-then-passing tests in
  `evals/harness/tests/test_cli.py`: a comparison that finds movement exits **0**, a label-digest
  refusal exits 1 with the cases named, and a missing run directory exits 1 (FR-006, contracts/cli.md)

**Checkpoint**: quickstart scenarios 2, 3, 4 and 5 behave as described — including that scenario 2
leaves `git status` clean, which is what proves T006 landed.

---

## Phase 4: User Story 2 — Know which movements mean anything (Priority: P2)

**Goal**: measure how much the numbers move on their own, and mark each movement against it.

**Independent test**: build a band from five runs of one build, then compare two of those same five
— every movement falls inside the band and none is marked outside it.

### Tests for User Story 2

- [X] T023 [P] [US2] Write the five recorded band fixtures under
  `evals/harness/tests/fixtures/runs/band/` — five small runs over one case set under identical
  conditions, differing only in a couple of requests' outcomes — plus the variants each refusal
  needs: one with a differing condition field, one with a differing corpus hash, one with a differing
  label digest, one with a different case set. Data only, no code
- [X] T024 [P] [US2] Write failing tests in `evals/harness/tests/comparison/test_band.py` for
  construction: a band records all five values per metric in run order with their low and high
  (FR-028), the conditions, corpus hash and case set (FR-029), and per varying case each observed
  state with how many of the five produced it
- [X] T025 [P] [US2] Write failing tests in `evals/harness/tests/comparison/test_band.py` for every
  refusal in [`contracts/noise-band.md`](./contracts/noise-band.md): four runs and six runs are both
  refused naming the count, and a run whose selection is not the full set is refused naming it
  (FR-027 — five narrowed runs agree with each other and still measure something else); a differing
  condition, corpus hash, label digest or case set is refused naming the field and the run (FR-030)
- [X] T026 [US2] Write failing tests in `evals/harness/tests/comparison/test_band.py` for application:
  a movement inside the observed range is marked inside and one outside is marked outside (FR-031); a
  band whose conditions differ from either run marks nothing and says so (FR-033); a metric the band
  never observed is reported unmarked (FR-034); a case the band saw vary carries `varies_on_its_own`
  wherever a comparison reports it moved (FR-035); every rendering citing a band states it is five
  observations and not a confidence interval (FR-032). Observe T024–T026 fail

### Implementation for User Story 2

- [X] T027 [US2] Implement `evals/harness/src/golden_harness/comparison/band.py` — `NoiseBand` and
  `MetricObservation`/`CaseVariation` construction from five scored runs, minting the band's own ULID
  as a run mints its id, with the refusals as typed errors. Observe T024 and T025 pass
- [X] T028 [US2] Extend `comparison/metrics.py` and `comparison/cases.py` to attach `BandVerdict` and
  `varies_on_its_own` when a band applies, leaving both absent when it does not. Observe T026 pass
- [X] T029 [US2] Extend `comparison/render.py` with the band block and its wording rules — the band's
  id, the five-observations sentence, and the not-applicable line — per the contract. Observe the
  T015 and T026 rendering assertions pass
- [X] T030 [US2] Add the `band` subcommand to `evals/harness/src/golden_harness/cli.py` per
  [`contracts/cli.md`](./contracts/cli.md), writing `<artifacts>/bands/<band_id>.json`, and extend
  `evals/harness/tests/test_cli.py`: a built band exits 0, each refusal exits 1 with its field named
- [X] T031 [US2] Wire `--band` through the `compare` subcommand to T028's marking, accepting a band id
  or a file path, and test in `evals/harness/tests/test_cli.py` that an absent band leaves every
  movement unmarked

**Checkpoint**: a band builds from the five fixture runs, and comparing two of them marks every
movement inside the observed range (SC-009).

---

## Phase 5: User Story 3 — Compare a narrowed run (Priority: P3)

**Goal**: the cheap investigation loop — a few cases driven, compared against a wider baseline,
without anyone reading the result as a statement about the set.

**Independent test**: a four-case run compared against the committed 135-case run covers exactly
those four cases, states the restriction, and recomputes both sides over them.

### Tests for User Story 3

- [X] T032 [P] [US3] Write failing tests in `evals/harness/tests/comparison/test_coverage.py`:
  `CaseCoverage` over two runs with different case sets yields the intersection as `common` and each
  exclusive set separately (FR-022); `restricted` is true exactly when either is non-empty
- [X] T033 [US3] Write failing tests in `evals/harness/tests/comparison/test_compare.py`: a narrowed
  run against a wider baseline computes every metric on **both** sides over the common cases only,
  never quoting the baseline's own denominators (FR-023, US3 AS2); two runs with no case in common
  stop the comparison with a named refusal rather than reporting everything unchanged (FR-024); a
  case present on one side only is named and contributes to no metric
- [X] T034 [P] [US3] Write a failing test in `evals/harness/tests/comparison/test_render.py` that the
  restriction and its count are printed before any metric, and that an incomplete run — fewer
  recorded cases than its selection names — is named as incomplete with its count (FR-012). Observe
  T032–T034 fail

### Implementation for User Story 3

- [X] T035 [US3] Implement `evals/harness/src/golden_harness/comparison/coverage.py` — `CaseCoverage`
  and the incompleteness of each `RunSide`. Observe T032 pass
- [X] T036 [US3] Pass the common case set into T006's `only=` restriction from
  `comparison/compare.py`, so both sides are scored over it, and raise the typed refusal when it is
  empty. Observe T033 pass
- [X] T037 [US3] Extend `comparison/render.py` with the coverage and incompleteness lines in the
  contract's position. Observe T034 pass

**Checkpoint**: quickstart scenario 6 behaves as described against the committed 2b run.

---

## Phase 6: Polish & cross-cutting concerns

**Purpose**: the gates, the documentation the constitution requires, and the phase's committed
record.

- [X] T038 [P] Write `evals/harness/README.md`'s new section: what `compare` and `band` are and are
  not, the four-way table from [`quickstart.md`](./quickstart.md), where a comparison is written and
  why never into an input run, and what stops a comparison — claiming nothing the tests do not
  demonstrate
- [X] T039 [P] Update [`.claude/CLAUDE.md`](../../.claude/CLAUDE.md): `make eval-compare` and
  `make eval-band` under "Commands", and one line under "Key design decisions to preserve" that a
  comparison re-scores both runs rather than reading their stored reports, and never writes into an
  input run
- [X] T040 [P] Add a "Comparing runs: technology choices" section to [`README.md`](../../README.md)
  following the existing sections' shape: re-scoring versus parsing stored reports, the pure/writing
  split, an observed range versus a statistical interval, a constant exit code, and their tradeoffs
- [X] T041 [P] Update [`docs/testing-strategy.md`](../../docs/testing-strategy.md): the comparison's
  tests are in the unit tier and make no live call; `make eval-band`'s five runs are not a test tier
  and spend live calls
- [X] T042 Run the full gates: `make precommit` and `make test-unit`; fix until green
- [X] T043 Quickstart scenarios 1–5 (free): the unit tier is green with `tests/comparison/test_purity.py`
  present; a run compared with itself reports no movement and leaves `git status` clean; re-comparing
  is identical apart from the two time fields; the same holds with `make services-down`; a one-field
  label edit stops the comparison naming the case, and is reverted afterwards
- [ ] T044 Quickstart scenario 6 (a few cents): drive `make eval-run CASES=G016,G096,G097,G100` and
  compare it against `specs/012-golden-set-metrics/evaluation`, confirming the restriction line, the
  131 baseline-only cases, and denominators over 4
- [ ] T045 Take the five band runs: with the stack started as `LOG_FORMAT=json make services-up` and
  databases migrated, run `make eval-run` five times against one unchanged build, then
  `make eval-band RUNS=<the five ids>`. This is the phase's one deliberate live-call expense
  (FR-027) — roughly an hour of driving
- [ ] T046 Quickstart scenario 8: compare two of the five band runs with `BAND=`, confirming every
  movement is marked inside the observed range and none outside it (SC-009)
- [ ] T047 Commit the phase's record under `specs/013-compare-eval-runs/evaluation/` (FR-037): the
  five runs' `report.json`/`report.md`, the band, and the T046 comparison, with a `README.md` stating
  the conditions, that the band is evidence of self-movement and not a threshold (FR-038), and which
  cases the five runs showed to be unstable. The five runs' `cases/` are **not** committed (SC-011),
  and the README says so along with what that costs: the band's per-case variation cannot be traced
  to its turns once the local runs are gone
- [ ] T048 Record in `specs/013-compare-eval-runs/evaluation/README.md` what the band found — the
  metrics whose range is widest, the cases
  that varied and how often, and what that implies for reading any future comparison — without moving
  a threshold, re-labelling a case, or changing anything the assistant does (spec Out of Scope)

---

## Dependencies & execution order

### Phase dependencies

- **Setup (Phase 1)**: none. T001 before any new file; T002 is independent.
- **Foundational (Phase 2)**: after Setup; blocks every story. Within it, T003–T006 (the scoring
  split) are independent of T007–T009 (the models) and of T010 (the fixtures), so the three groups
  can proceed in parallel; T008 fails until T009 creates the package.
- **US1 (Phase 3)**: after Foundational. Nothing else depends on it being finished, but US2 and US3
  both extend its modules, so they start after it rather than beside it.
- **US2 (Phase 4)**: after US1 — T028 and T029 extend `metrics.py`, `cases.py` and `render.py`.
  Its fixtures (T023) need only Foundational and can be written earlier.
- **US3 (Phase 5)**: after US1 — T036 extends `compare.py`. Independent of US2; the two never touch
  the same function.
- **Polish (Phase 6)**: after the stories. T045 needs US2 shipped, T044 needs US3, and T047–T048 need
  T045 and T046.

### Within each story

Contract → tests → **observed failing** → implementation → tests observed passing, per constitution
principle VIII.

### Parallel opportunities

- Phase 2: T003–T005, T007–T008 and T010 are three independent tracks.
- Phase 3: T011, T012, T013 and T015 are different test files; T016 and T017 are different modules.
- Phase 4: T023, T024 and T025 are independent of each other.
- Phase 6: T038–T041 are four different documents.

## Implementation strategy

**MVP is User Story 1 alone.** It delivers the phase's headline value — name two runs, learn what
moved — and everything after it is refinement: US2 says which movements are worth acting on, US3
makes the loop cheap. Shipping US1 and stopping would leave a useful tool whose reports honestly
decline to call anything a regression, which is exactly the behaviour FR-036 mandates when no band
exists.

**The expensive task is T045, and it is last for a reason.** Five full runs cost roughly an hour and
five times a run's model spend. Everything testable is tested against recorded fixtures before a
single live call is made.

---

## Phase 7: Convergence

- [X] T049 Name the request position beside the case id in each metric row's list of movements in
  `evals/harness/src/golden_harness/comparison/render.py`, per FR-017 (partial) — FR-017 asks for
  the cases behind a metric "identified by case id and, where the metric is per request, by request
  position", and `_family_lines` currently prints the id alone
- [X] T050 Strengthen the metric-to-movement test in
  `evals/harness/tests/comparison/test_render.py` so the cases a metric row names are exactly the
  movements whose `affects` holds that metric, per contracts/comparison-record.md §7 (partial) — the
  contract's "the same list seen from two sides rather than two lists that can disagree" is what
  makes FR-017 answerable in both directions, and the current test only asserts one case id appears

---

## Phase 8: Convergence

- [X] T051 Refuse a band built from a run that recorded fewer cases than its selection names, in
  `evals/harness/src/golden_harness/comparison/band.py`, naming the run and both counts, per FR-027
  and contracts/noise-band.md (contradicts) — `_refuse_a_partial_run` checks each run's selection
  and lets an *incomplete* run through, so a value measured over 3 cases enters a range beside four
  measured over 4, and `band.case_ids` names a set the band did not measure. Test it in
  `evals/harness/tests/comparison/test_band.py` first, from a fixture run with a record removed
- [X] T052 Report a verdict movement when a request produced an outcome in one run and none in the
  other, in `evals/harness/src/golden_harness/comparison/cases.py`, per FR-020 and US1/AC5
  (partial) — `_verdict_movements` compares only positions both runs answered, so a request that
  was answered in one run and produced nothing in the other appears in no group at all. Give the
  absent side a state of its own, direction `directionless` since no label makes one available, and
  test both directions in `evals/harness/tests/comparison/test_cases.py` first

---

## Phase 9: Convergence

- [X] T053 Narrow a positioned movement's `affects` to the metrics whose contribution changed **at
  that request**, in `evals/harness/src/golden_harness/comparison/cases.py`, per FR-017 and
  data-model.md's `CaseMovement.affects` (contradicts) — `_affects` matches
  `item[1] in (movement.position, None)`, so a verdict or rank movement also claims every per-turn
  metric of its case, and after T049 the metric row prints a request position under a per-turn
  metric. A per-turn change always has a movement of its own to own it (segmentation, tool
  selection, database state, exclusion), so nothing is lost. Test first, from a case carrying both
  a segmentation movement and a verdict movement
