# Committed runs

A run kept out of `.run/evals/` so a later one has something to be compared against. `.run/` is
gitignored and is cleaned up freely, so a run that matters to anyone but the person who took it
belongs here instead.

`make eval-compare BASE=<path> NEW=<run>` takes a directory path as well as a run id, so a run
here is usable where it sits — and a comparison writes its record under
`.run/evals/comparisons/`, never back into either input, so naming one of these as `BASE` cannot
rewrite it.

**A baseline is evidence, not a threshold.** Nothing here is the number a later run has to beat.
The assistant is non-deterministic and `claude-sonnet-5` takes no `temperature`, so a single run
is one sample: what counts as a regression is read by a person, against a noise band measured
from five runs of one unchanged build (`make eval-band`), and there is no band yet.

A run is added here deliberately, by copying it out of `.run/evals/`, and is never edited
afterwards. Compare against one rather than re-scoring it: `compare` writes into neither input,
while `score` rewrites the run's own `report.json` and `report.md` where they sit — in practice
only its `score_seconds`, but enough to make a committed run differ from what the harness wrote. Re-scoring one refuses if a scored field of a label it selects has changed since, so
a committed run stops being scoreable when the golden set moves under it — which is a fact worth
finding out, not a thing to repair by editing the run.

## The runs

| Run | Cases | Code | Taken |
|---|---|---|---|
| [`01M321DWRXSVSY7GW9RY3CR9YW`](01M321DWRXSVSY7GW9RY3CR9YW/) | 97 (all) | `12341e1` on `new-golden-set` | 2026-09-21 |

The first run of the golden set at v2 that is scoreable. The 2b record under
`specs/012-golden-set-metrics/evaluation/` is not: it selects v1 case ids the set no longer holds,
so both `compare` and `score` refuse it. That record stays frozen as FR-048a made it; this one
supersedes it as the run a comparison starts from, and does not replace it as a record.
