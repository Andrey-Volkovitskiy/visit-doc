# Contract: the two commands

`compare` and `band` join `run` and `score` on the harness command line. Both are offline: they read
stored runs and the labels, and nothing else.

## `compare`

```bash
uv run --package golden-harness -- python -m golden_harness compare \
    --base <run id or directory> --new <run id or directory> \
    [--band <band id or file>] [--artifacts <dir>] [--labels <file>]
```

Makefile: `make eval-compare BASE=<run> NEW=<run>`, with an optional `BAND=<band>`.

| Argument | Default | Notes |
|---|---|---|
| `--base` | required, never defaulted | the run compared *from*; resolved by `resolve_run`, which already accepts a directory or a bare id (`cli.py:131`). The committed 2b run is the documented starting baseline (FR-039), and naming it is the caller's job: a command that silently reads a path under `specs/` when the flag is omitted is reaching somewhere its caller did not look |
| `--new` | required | the run compared *to* |
| `--band` | none | when given, movements are marked against it (FR-031) |
| `--artifacts` | `.run/evals` | where bare ids resolve, and where the comparison is written |
| `--labels` | `evals/golden/cases.json` | the labels both runs are scored against |

**Behaviour**: scores both runs over their common cases, writes the comparison record and its
summary under `<artifacts>/comparisons/`, prints the summary.

**Exit status**:

| Situation | Exit | Why |
|---|---|---|
| A comparison was produced, whatever it found | **0** | FR-006. A finding is never a failure, and no flag changes this |
| A movement lies outside the band | **0** | the finding is in the report, not the exit code |
| Label digests differ between the runs | 1 | FR-011: no comparison exists to report |
| No case in common | 1 | FR-024 |
| A run directory is missing or unreadable | 1 | the existing `RunNotFoundError` path |

The failures all reach the terminal through `cli.py`'s existing `_REPORTED_FAILURES` handler, as a
named sentence rather than a traceback, and the new error types are added to that tuple.

**What it never does**: write into either input run directory, read either run's `report.json`, open
a socket, or touch a database.

## `band`

```bash
uv run --package golden-harness -- python -m golden_harness band \
    --runs <id|dir>,<id|dir>,... [--artifacts <dir>] [--labels <file>]
```

Makefile: `make eval-band RUNS=<id>,<id>,<id>,<id>,<id>`.

**Behaviour**: scores each run, checks they agree on conditions, corpus hash and label digests,
computes the per-metric observed range and the per-case variation, writes the band under
`<artifacts>/bands/<band_id>.json`, prints a summary.

**Exit status**: 0 when a band was built; 1 when it was refused.

| Refusal | Named in the message |
|---|---|
| Fewer than five runs | how many were given (FR-027) |
| Conditions differ | the field and the two values, and which run |
| Corpus hash differs | both hashes |
| Label digests differ | the cases |
| Case sets differ | the cases not common to all five |

Five is the required count, not a minimum to be exceeded quietly: a band built from a different
number is a different measurement, and the record has to say which.

## What neither command offers

- **No `--fail-on-regression`.** FR-006 and research R10: the exit code is the surface a CI step
  attaches to, and leaving it constant is what keeps a gate a deliberate act rather than an
  accident.
- **No `--threshold`.** Nothing in this phase turns a number into a pass mark.
- **No narrowing flags of its own.** A comparison covers whatever the two runs recorded; narrowing
  happens when a run is *driven* (`eval-run CASES=…`), which is where the cost is.
