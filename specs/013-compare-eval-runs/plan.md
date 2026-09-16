# Implementation Plan: Comparing One Version Against Another (Phase 2c)

**Branch**: `013-compare-eval-runs` | **Date**: 2026-09-16 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/013-compare-eval-runs/spec.md`

## Summary

Add a third harness command beside `run` and `score`: **`compare`**, which takes two stored runs and
reports what moved — the condition delta first, then every metric with both runs' numerators and
denominators, then the cases behind each movement. Add a **`band`** command that measures the
run-to-run noise from five full runs of one unchanged build, and let `compare` mark each movement as
inside or outside it. Nothing drives a turn; nothing spends a model call except the five band runs,
which are taken with the existing `make eval-run`.

Three decisions from Phase 0 shape the build, and two of them were not visible from the spec.

- **The comparison re-scores both runs rather than reading their stored reports** (research R1).
  `report.json` is a serialized `Report` whose models are `extra="forbid"`, so the moment this phase
  adds a field the committed 2b record stops parsing. Re-scoring also gives the only honest answer
  for a narrowed comparison (FR-023), and 2b's own SC-003 guarantees it reproduces what the stored
  report says. So the comparison's inputs are `run.json` and the case files; `report.json` is an
  output of the harness that the harness itself never reads.
- **`score_run` writes its report beside the run it scored** (research R2, `report.py:182`). Calling
  it on the committed baseline under `specs/012-golden-set-metrics/evaluation/` would rewrite that
  frozen record in place. The phase therefore splits scoring in two — a pure `score(...)` that
  returns a `Report` and touches nothing, and the existing `score_run` as the thin writing wrapper
  the `score` CLI keeps using. This is the one change to 2b's shipped code the phase needs, and it
  is a refactor with no behaviour change: same inputs, same `Report`, same bytes on disk from the
  same caller.
- **The scorers already publish the per-item detail a movement needs** (research R4). Retrieval
  publishes every scored request with its per-stage rank; classification, serving and booking each
  publish the complete list of items on the shortfall side. A case's contribution is derived from
  those lists against the scored population, so no scoring rule is re-implemented and a movement can
  never disagree with the metric it sits under.

The new code is one module tree inside the existing `evals/harness` member — `comparison/` beside
`scoring/` — plus two CLI subcommands and two Makefile targets. No new workspace member, no new
dependency, no service, no database.

## Technical Context

**Language/Version**: Python 3.12 (the workspace's pinned version)

**Primary Dependencies**: none new. `pydantic` for the comparison and band models, as the run record
and report already use; the existing `golden_harness.scoring` and `golden_harness.record` modules for
scoring and loading; `chat` (already a workspace dependency) for `FaqVerdict` and `IntentLabel`,
which the movement types name rather than copy. The comparison deliberately adds **no** dependency
that could reach a network or a database — that is the property FR-004 and the purity test protect.

**Storage**: JSON files on disk, as 2b established. The comparison reads a run directory
(`run.json` + `cases/*.json`) and the golden set's labels; it writes its own record and summary
under `.run/evals/comparisons/`, never into either input run (research R8). The band is one JSON
file under `.run/evals/bands/`. No database, no table, no migration.

**Testing**: pytest, colocated at `evals/harness/tests/`, inside the existing unit tier. The
comparison is driven entirely from small hand-written run directories under
`tests/fixtures/runs/`, the way 2b drives its scorers — two recorded runs differing in exactly one
respect is the whole test setup for most cases. No test makes a live call, and the purity test is
extended to the new modules so that stays true by construction rather than by review.

**Target Platform**: local developer machine (Linux/WSL2). `compare` and `band` need nothing
running — not the chat service, not the scheduler, not Postgres, not Qdrant. Taking the five band
runs needs the full stack up with `LOG_FORMAT=json`, exactly as 2b's runs do.

**Project Type**: additional commands in an existing CLI tool inside the uv monorepo; not a service,
not a shared library, not a test tier.

**Performance Goals**: none as a target, but one constraint worth naming: a comparison scores two
runs of 135 cases each, and 2b's scoring takes about 0.1 s per run, so the whole command is
sub-second and no caching or incremental scheme is justified. Reading 2.5 MB twice is not a problem
to solve.

**Constraints**: the command always exits zero on a finding (FR-006); it may not import the HTTP
client, the gRPC stack, the database layer or `driver/` (FR-004); it may not write into either input
run; it may not read either run's stored `report.json`; and it changes nothing about what the
assistant does — no floor, prompt or routing rule moves in this phase.

**Scale/Scope**: two runs of up to 135 cases, 190 labelled requests, 17 canonical metric names across
four families (`METRICS_BY_FAMILY`) — one of which, `verdict_distribution`, expands into six
published rows — 11 exclusion reasons, six movement groups, five band runs.

## Constitution Check

*GATE: evaluated before Phase 0 research and re-evaluated after Phase 1 design. Both passes below.*

| Principle | Before Phase 0 | After Phase 1 | Note |
|---|---|---|---|
| I. Phase-gated scope discipline | PASS | PASS | This is Phase 2c, taken in order after 2b shipped. It adds no service and no platform layer; 2d's tracing is untouched and explicitly out of scope. |
| II. AI core is the centerpiece | PASS | PASS | Evaluation is the roadmap's named centerpiece of Phase 2. The phase spends its effort on reading the AI core's behaviour, not on infrastructure around it. |
| III. Deliberate, minimal service boundaries | PASS | PASS | No new boundary, no new datastore, no cross-service call. The comparison talks to nothing. |
| IV. Structured outputs & decoupled tools | N/A | N/A | No model call and no agent step is added, so neither clause has a subject here. |
| V. Grounded retrieval with mandatory abstention | N/A | N/A | Nothing about retrieval or abstention changes. The phase measures both and moves neither. |
| VI. Documentation as a first-class deliverable | PASS | PASS | FR-040 puts the doc updates in this change: the harness README gains what a comparison is and is not, `.claude/CLAUDE.md` gains the commands, the README gains the technology-choice entry, and the band's record states what it does not claim. |
| VII. Clean architecture, SOLID & design patterns | PASS | PASS | `comparison/` depends on `scoring/` and `record`, never the reverse; the one change to existing code separates a pure computation from its side effect, which is the dependency the current signature confuses. No new abstraction is introduced that a single caller would not need. |
| VIII. Test-driven development | PASS | PASS | Every task pair in the tasks file will be written tests-first: the movement types and their derivation, the restriction to common cases, the label refusal, the band's construction and its refusals, and the purity extension — each observed failing before the module that satisfies it. The recorded fixture runs are data, written before the tests that read them. |

**No violations to track.** The Complexity Tracking table below is therefore empty, as it should be.

## Project Structure

### Documentation (this feature)

```text
specs/013-compare-eval-runs/
├── plan.md              # This file
├── research.md          # Phase 0 output — the ten findings the build rests on
├── data-model.md        # Phase 1 output — the comparison and band entities
├── quickstart.md        # Phase 1 output — validation scenarios in cost order
├── contracts/
│   ├── comparison-record.md   # what a stored comparison holds, and why each field
│   ├── noise-band.md          # how a band is built, what it records, what it refuses
│   └── cli.md                 # the two commands, their arguments and their exits
├── checklists/
│   └── requirements.md  # spec quality checklist (complete)
├── evaluation/          # the phase's record: five band runs' reports, the band, one comparison
└── tasks.md             # /speckit-tasks output — NOT created by /speckit-plan
```

### Source Code (repository root)

```text
evals/harness/
├── src/golden_harness/
│   ├── cli.py                    # CHANGED: `compare` and `band` subcommands
│   ├── report.py                 # CHANGED: `score_run` splits into pure `score` + writing wrapper
│   ├── record.py                 # unchanged
│   ├── scoring/                  # unchanged
│   └── comparison/               # NEW
│       ├── __init__.py
│       ├── model.py              # the comparison and band models (data-model.md)
│       ├── metrics.py            # metric movement: pairing, direction, denominator change
│       ├── cases.py              # case movement: the six groups, derived per research R4
│       ├── conditions.py         # the condition delta, and the label refusal it does not own
│       ├── coverage.py           # the common case set, and each side's completeness
│       ├── band.py               # building a band from five runs; marking movements against one
│       ├── compare.py            # the entry point: two run dirs + labels -> Comparison
│       └── render.py             # the human-readable summary
└── tests/
    ├── comparison/               # NEW, mirroring the modules above
    │   ├── test_model.py
    │   ├── test_compare.py
    │   ├── test_metrics.py
    │   ├── test_cases.py
    │   ├── test_conditions.py
    │   ├── test_coverage.py
    │   ├── test_band.py
    │   ├── test_render.py
    │   └── test_purity.py        # NEW: extends the rule to comparison/ (see below)
    ├── fixtures/runs/            # EXTENDED: paired recorded runs for the comparison tests
    └── scoring/test_purity.py    # unchanged

Makefile                          # CHANGED: `eval-compare`, `eval-band`
.claude/CLAUDE.md                 # CHANGED: the two commands, one design-decision line
README.md                         # CHANGED: a technology-choices entry for this phase
docs/testing-strategy.md          # CHANGED: the comparison is in the unit tier; band runs are not
evals/harness/README.md           # CHANGED: what compare and band are, and are not
```

**Structure Decision**: the comparison is a **sibling of `scoring/`, not a member of it**. Both are
pure and both are offline, so the alternative was tempting, but they answer different questions —
`scoring/` turns one run into metrics, `comparison/` turns two scored runs into movements — and the
dependency runs strictly one way. Keeping them apart is also what keeps 2b's suite meaningful: a
test that fails in `scoring/` says the metrics are wrong, and one that fails in `comparison/` says
the reading of them is.

One consequence has to be handled rather than assumed. 2b's `tests/scoring/test_purity.py` globs
`golden_harness/scoring/*.py` and is **not recursive**, so a new sibling directory inherits none of
its protection. The phase adds `tests/comparison/test_purity.py` applying the same forbidden roots
(`httpx`, `grpc`, `sqlalchemy`, `golden_harness.driver`) by the same static-AST mechanism to
`comparison/*.py`. Extending the existing test's glob to cover both trees was considered and
rejected: one test file that silently governs two packages is exactly the arrangement that leaves a
third package unguarded later.

## Complexity Tracking

> No Constitution Check violations. Nothing to justify.
