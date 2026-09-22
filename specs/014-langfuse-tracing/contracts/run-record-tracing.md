# Contract: tracing in the service's startup event, the run record, and the eval commands

## `service.configured` (chat, startup)

Gains one field:

| Field | Type | Meaning |
|---|---|---|
| `tracing_enabled` | bool | both Langfuse keys are set; this process exports traces for turns not sent `off` |

It is **not** a run condition. `RunConditions` does not gain it, so `from_event` ignores it, and no
condition delta, band check or conditions-changed refusal ever names it. `test_main.py`'s pinned field
set includes it.

## `run.json`

Gains `tracing: "traced" | "untraced_by_request" | "untraced_service_off"`, default
`"untraced_service_off"` (a file without the field is a run from before this phase). Meaning and
precedence: [data-model.md §4](../data-model.md#4-harness-run-artifacts).

Set once, at the run's start, from the CLI flag and the `service.configured` event the run takes its
conditions from.

## Case file

Gains `traces: {<patient message id>: <trace id>}`, default `{}`, filled from `turn.traced` events in
the case's log slice.

## Restart and resume

- `_RestartWatch`: a restart whose `service.configured` states a different `tracing_enabled` than
  the run started under stops the run with `ServiceRestartedError`, naming the field — unless the run
  is `untraced_by_request`, which the service's state cannot affect.
- `resume_run`: refuses, naming the field, when the running service's `tracing_enabled` disagrees
  with a `traced` or `untraced_service_off` run; an `untraced_by_request` run resumes and keeps
  sending `X-VisitDoc-Trace: off`.

## Scoring and comparison

- Scoring ignores both fields.
- `compare` prints each run's `tracing` beside its id and does not include it in the condition delta.
  A traced and an untraced run of the same build and conditions compare with an **empty** delta.
- `band` does not check it: five runs may mix traced and untraced.

## CLI and Makefile

| Invocation | Result |
|---|---|
| `python -m golden_harness run [--cases …\|--family …]` | traced (default) |
| `python -m golden_harness run --no-trace …` | `untraced_by_request`; every turn sent with `X-VisitDoc-Trace: off` |
| `python -m golden_harness run --resume <id>` | tracing taken from the stored run, not from flags; `--no-trace` with `--resume` is a usage error |
| `make eval-run` / `make eval-run TRACE=1` | traced |
| `make eval-run TRACE=0` | `--no-trace` |
| `make eval-run TRACE=<anything else>` | Make stops with an error naming the two accepted values |

Every traced eval turn is sent with `X-VisitDoc-Eval-Run: <run_id>` and `X-VisitDoc-Eval-Case:
<case_id>`; an untraced one sends them too (they are harmless when nothing is exported).
