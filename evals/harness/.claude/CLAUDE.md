# CLAUDE.md — evals/harness

## Key design decisions to preserve

Moved here from the root `.claude/CLAUDE.md`, which keeps the cross-cutting principles.

- **A comparison re-scores both runs; it never reads their stored reports, and never writes into
  an input run** (013). `report.json` is a serialized `Report` whose models are `extra="forbid"`,
  so a phase that adds a field would otherwise make the committed 2b record unparseable — and only
  re-scoring gives an honest answer when the two runs cover different case sets, since every metric
  is then computed over the cases both recorded rather than quoted from a published report. This is
  why `score_run` split into a pure `score(run_dir, cases, *, only=None)` and the thin wrapper that
  writes both artifacts: comparing against the baseline under `specs/` must not rewrite it. A
  comparison's own record goes under `.run/evals/comparisons/<base>__<new>/`, never beside either
  input, and the command exits zero whatever it finds — a finding belongs in the report, not in an
  exit code, so a gate stays a deliberate act rather than an accident. A noise band is five full
  runs of one build, an observed range and a frequency count: never a threshold, and never a
  confidence interval.
- **Scoring the golden set is a pure function of a stored run** (012). `golden_harness.scoring` and
  `report.py` read `run.json`, the case files and the labels and nothing else: no `scoring` module
  may import `httpx`, `grpc`, `sqlalchemy` or `driver/` (`test_purity.py` checks), and `score` is
  tested with the network and database refused. Any stored run therefore re-scores offline, and
  scoring refuses labels whose scored-field digests changed since the run. A handed-off turn
  classified before it handed off, so it is scored for classification and excluded from retrieval
  and serving only (FR-018a); the other six turn-level reasons exclude a case from every metric.
  The field names of `chat`'s startup `service.configured` event are a data contract with the
  harness, which takes a run's conditions from it — rename one and runs stop rather than silently
  recording less.
- **Tracing is not a run condition** (014, from chat's tracing decision). On the harness side,
  `run.json`'s `tracing` lives on `Run`, **never on `RunConditions`**: a traced and an untraced run
  measure the same thing, so it must not enter a condition delta or a band. A restart that changes
  `tracing_enabled` stops a traced or service-off run and a resume refuses it; neither applies to a
  run that asked to be untraced.
