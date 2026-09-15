# Implementation Plan: Metrics Over the Golden Set (Phase 2b)

**Branch**: `012-golden-set-metrics` | **Date**: 2026-09-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/012-golden-set-metrics/spec.md`

## Summary

Build a harness that drives all 135 golden cases through the running chat service as real turns,
one at a time, stores everything each turn produced, and scores each **request** against its label —
reporting every metric with the denominator it was computed over and the exclusions it set aside.

Three things shape the build, and two of them came out of Phase 0 rather than out of the spec.

- **The record and the log answer different questions.** What the turn *decided* — each request's
  verdict, answer and citations — is on the stored message, which is what 1h put there. What
  retrieval *ranked* is only in the turn's log events, because the record carries the survivors and
  hit@k is a question about the candidates. The harness reads both and keeps them apart.
- **The log has no machine-readable form yet** (research R1). `shared-logging` ends its chain with
  a colourised console renderer and nothing else, so every value reaches the file as a Python
  `repr`. Plain data survives that; values that are not Python literals do not — and two of them sit
  on events this phase must read, including `intent.classified`'s `segments`, which is the only
  source of the produced segmentation. The phase adds a JSON renderer selected by a setting — one
  change, in the one place rendering is decided, downstream of redaction so the security control is
  untouched. It is one of the two changes FR-051 permits; the other is one startup event through which
  the service states the settings a run is measured under (FR-047c, research R11).
- **The corpus pin cannot be verified** (research R3). `corpus.json`'s `sha256` is described as
  "over the entry texts" and reproduced by none of seventeen plausible constructions, though the
  texts themselves have not drifted. FR-010 requires checking it before spending a model call, so
  the phase re-takes the pin with the construction written down beside the value.

The harness is one new workspace member, `evals/harness`, split three ways: a **driver** that takes
a run, a **scorer** that is a pure function of a stored run, and a **reporter**. The split is what
makes FR-044 true — the arithmetic that can be wrong is testable without a stack, and the first
run's committed record stays re-scorable forever.

## Technical Context

**Language/Version**: Python 3.12 (the workspace's pinned version)

**Primary Dependencies**: `httpx` for the chat service's HTTP surface; `grpcio` + `shared-proto` for
the scheduler's contract; `shared-db` + SQLAlchemy for the two narrow direct reads/writes the spec
sanctions; `pydantic` for the record and report models; `chat` as a workspace dependency, for
`IntentLabel`, `FaqVerdict` and `RequestOutcome` themselves (research R5); `scheduler` as a workspace
dependency, for `SESSION_SEED` and `BOOKING_HORIZON_DAYS`, so a fixture's plantability is checked
against the roster itself rather than a copy of it, on R5's reasoning; `jsonschema` for FR-014's
validation of `cases.json`; `python-ulid` for the history rows' ids (research R6)

**Storage**: run artifacts as JSON files on disk — one `run.json` plus one file per case (research
R8). No new database, no new table, no migration. The chat and scheduler databases are read and
written only as FR-005, FR-040 and FR-041b permit.

**Testing**: pytest, colocated at `evals/harness/tests/`, inside the existing unit tier. No live
model call in any test: the scorer is driven from recorded case files and the driver from stubbed
HTTP and gRPC surfaces (research R10). The two direct reads and writes FR-005 sanctions — planting
history, reading a chat's ids — are tested against the chat test database, as the chat suite's own
repository tests are, since stubbing the one seam that is a database write would test nothing.

**Target Platform**: local developer machine (Linux/WSL2) with the stack up — chat, scheduler,
Postgres, Qdrant — started as `.claude/CLAUDE.md` documents, with the chat service configured to
emit JSON logs.

**Project Type**: a CLI tool inside the existing uv monorepo; not a service, not a shared library.

**Performance Goals**: none, deliberately. A full pass is 135 turns taken strictly one after another
(FR-003a), and the spec forbids trading that away for speed. How long that takes is an input to 2c's
cadence decision, not a target this phase optimizes against.

**Constraints**: no change to what the assistant does (FR-051), the log renderer and the startup
settings event being the two named exceptions; scoring must run offline against a stored run (FR-044); every labelled request must
land in exactly one of aligned / unaligned / excluded (FR-017).

**Scale/Scope**: 135 cases, 190 labelled requests, 117 FAQ requests, 19 booking requests across 18
cases, 9 corpus entries, 2 seeded practitioners, 5 cases carrying history.

## Constitution Check

*GATE: evaluated before Phase 0 research and re-evaluated after Phase 1 design. Both passes below.*

| Principle | Verdict | Note |
|---|---|---|
| I. Phase-Gated Scope Discipline | **PASS** | 2a shipped the data; this is 2b and nothing else. The gate is 2c and tracing is 2d, both fenced off in the spec's Out of Scope. |
| II. AI Core Is the Centerpiece | **PASS** | The evaluation harness *is* the centerpiece work this principle protects time for. |
| III. Deliberate, Minimal Service Boundaries | **PASS** | No new service, no new datastore. The run crosses the existing boundaries by their own contracts — HTTP for the turn, gRPC for the scheduler. |
| IV. Structured Outputs & Decoupled Tool Interfaces | **PASS (n/a)** | Nothing about classification or the tool registry changes; this measures them. |
| V. Grounded Retrieval with Mandatory Abstention | **PASS** | Unchanged, and now measured: two of the metrics exist to catch an abstention that should not have happened. |
| VI. Documentation as a First-Class Deliverable | **PASS, with work** | Four contracts and a quickstart here; `evals/harness/README.md` and a README tradeoff note for the log-format setting are deliverables of the change, not follow-ups. |
| VII. Clean Architecture, SOLID & Design Patterns | **PASS** | Driver / scorer / reporter are separated because the middle one must be pure (FR-044), not for symmetry. The scorer depends on stored records, never on a client. |
| VIII. Test-Driven Development | **PASS, binding** | Contracts first, tests derived from them and observed to fail, then implementation. The two zero-target metrics are proven against hand-written case files *before* the first real run, so the first run's numbers are read by a scorer already known to be right on known answers. |

**Post-design re-check**: unchanged. The two design decisions that could have moved a verdict are the
`shared-logging` change and the chat service's `service.configured` startup event, both examined
under Complexity Tracking below.

## Project Structure

### Documentation (this feature)

```text
specs/012-golden-set-metrics/
├── plan.md              # This file
├── research.md          # Phase 0 — ten questions, and the two findings that moved the build
├── data-model.md        # Phase 1 — the fixture label, the run record, the report
├── quickstart.md        # Phase 1 — how a run is taken, and what it costs
├── contracts/
│   ├── fixture-label.md     # the golden-set schema extension (FR-037, FR-037a)
│   ├── run-record.md        # what one driven case stores (FR-006, FR-048)
│   ├── metrics.md           # every metric's numerator, denominator and exclusions
│   └── log-access.md        # the JSON log line, and how a turn's slice is taken
├── checklists/
│   └── requirements.md  # spec quality checklist (already written)
├── evaluation/          # FR-048a — the first full run's report and case records
└── tasks.md             # /speckit-tasks output, not created here
```

### Source Code (repository root)

```text
evals/
├── golden/                      # existing; 2a's data
│   ├── cases.json               # gains a per-case `scheduling` fixture (18 cases)
│   ├── schema.json              # gains the fixture's shape
│   ├── corpus.json              # gains `algorithm`; `sha256` re-taken (research R3)
│   └── PROVENANCE.md            # records the old, unreproducible pin
└── harness/                     # NEW uv workspace member `golden-harness`
    ├── pyproject.toml
    ├── README.md
    ├── src/golden_harness/
    │   ├── cases.py             # load + validate cases.json, resolve fixtures
    │   ├── corpus.py            # the pin: compute, compare, name what moved
    │   ├── driver/
    │   │   ├── session.py       # mint the run's session, seed check, chat per case
    │   │   ├── history.py       # plant history rows (direct DB write)
    │   │   ├── turn.py          # post the turn, read the terminal event and thread
    │   │   ├── logslice.py      # offsets + turn_id selection (research R2)
    │   │   ├── scheduling.py    # plant preconditions, read post-state, cancel what is left (gRPC)
    │   │   └── run.py           # the sequential loop, retry policy, resume
    │   ├── record.py            # the stored run/case models
    │   ├── scoring/
    │   │   ├── alignment.py     # aligned / unaligned / excluded, and the conservation rule
    │   │   ├── metric.py        # numerator, denominator, exclusions, not_measured
    │   │   ├── classification.py
    │   │   ├── retrieval.py     # hit@k and MRR, per stage
    │   │   ├── serving.py       # the two zero-target metrics
    │   │   └── booking.py       # tool selection, end-to-end task success
    │   ├── report.py            # metrics + the conditions they were measured under
    │   ├── cli.py               # `run` and `score`
    │   └── __main__.py          # `python -m golden_harness`
    └── tests/                   # unit tier; no live calls
        └── fixtures/runs/       # hand-written recorded runs with known answers

packages/shared-logging/src/shared_logging/logging.py   # + JSON renderer, LOG_FORMAT
services/chat/src/chat/core/config.py                   # + LOG_FORMAT setting
services/chat/src/chat/main.py                          # + service.configured startup event (FR-047c)
services/chat/src/chat/rag/embeddings.py                # `_MODEL` renamed `EMBEDDING_MODEL`, for that event
pyproject.toml                                          # + workspace member, mypy files, testpaths
Makefile                                                # + eval targets
```

**Structure Decision**: the harness is a new uv workspace member at `evals/harness/`, beside the
data it reads, rather than under `packages/` (documented as the place for code *shared between
services*, which this is not) or under `specs/` (excluded from ruff and mypy, which FR-050 forbids).
The cost is three central config edits — the workspace member list, mypy's `files`/`mypy_path`, and
pytest's `testpaths` — because this repo centralizes tool configuration on purpose. Ruff needs none:
its hierarchical discovery finds the root config by walking up.

The internal split follows from FR-044 rather than from taste: `scoring/` may import `record.py`, the
labels in `cases.py` and its own modules, and nothing from `driver/`, so the arithmetic is testable against files and the first run stays re-scorable
forever. `driver/` is the only part that knows an HTTP client, a database or a gRPC channel exists.

## Complexity Tracking

> Two changes outside the harness. The first is examined because it touches a shared component that
> carries a security control; the second because it changes a service this phase measures.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| A second renderer in `shared-logging`, selected by a new `LOG_FORMAT` setting | The only existing renderer emits every value as a Python `repr`. `intent.classified` — the sole source of the produced segmentation (FR-021a) — renders its segments carrying `<IntentLabel.FAQ_QUESTION: 'faq_question'>`, which `ast.literal_eval` rejects outright, and `booking.tool_called` renders `datetime.datetime(...)` arguments the same way. Without a machine-readable form, FR-019 through FR-027 cannot be implemented at all. | Parsing the console output was tried against real rendered lines, not on paper: plain data round-trips, but non-literal values do not, and the same enum renders two different ways depending on nesting depth. Beyond the mechanics, `ConsoleRenderer` is a renderer for humans with no format-stability guarantee, and 008's contract froze field names as a data contract. A replay endpoint on the chat service is a far larger change to a published surface. The renderer sits **last** in the chain, downstream of `make_redact_secrets_processor`, so redaction is upstream of the change and unaffected — and a test asserts a JSON line is rendered from an already-redacted event dict. |
| A `service.configured` event at the chat service's startup (FR-047c) | A run's conditions have to describe the process that answered its turns. The harness reads the same `.env`, but a running service can have loaded something else — an overridden floor, last week's build — and the classification, generation and embedding models appear on no existing event, so there is nothing else to read them from. | Reading the harness's own environment was the first design, and was rejected on analysis: it records the settings of the wrong process, silently. A settings endpoint would be a new published surface for what one log line carries. The event is emitted once, carries no secret-bearing setting, and changes nothing a turn does. |

## Phase 1 Artifacts

- [`data-model.md`](./data-model.md) — the fixture label, the run and case records, the alignment
  record, and the report.
- [`contracts/fixture-label.md`](./contracts/fixture-label.md) — the golden-set schema extension.
- [`contracts/run-record.md`](./contracts/run-record.md) — what one driven case stores.
- [`contracts/metrics.md`](./contracts/metrics.md) — each metric's numerator, denominator and
  exclusions, stated so a test can be written from it.
- [`contracts/log-access.md`](./contracts/log-access.md) — the JSON log line and the slice rule.
- [`quickstart.md`](./quickstart.md) — taking a run, and what it costs.
