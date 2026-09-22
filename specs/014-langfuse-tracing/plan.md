# Implementation Plan: Tracing with Langfuse (Phase 2d)

**Branch**: `014-langfuse-tracing` | **Date**: 2026-09-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/014-langfuse-tracing/spec.md`

## Summary

Give every chat turn one Langfuse trace: a root observation per turn, one per graph node, one per
retrieval step, one per tool call, and a generation per Anthropic call carrying its model and token
usage. Traces are exported over OpenTelemetry by the Langfuse SDK to the Cloud Hobby project; the log
and the harness's reading of it do not change. Eval runs are traced by default and tag their traces
with run and case ids; `make eval-run TRACE=0` sends a request header that makes the service drop
those turns' traces while every other turn keeps tracing.

Three decisions from Phase 0 shape the build, and two of them change what the roadmap said.

- **Node spans come from the graph's own `node_span`, not from Langfuse's `CallbackHandler`**
  (research R2). The handler runs on an executor thread, so nothing opened inside a node can nest
  under the node's span — the retrieval steps, generations and tool calls the spec requires in a
  tree would come out as loose siblings. It also needs the `langchain` package, and it records
  nothing about direct Anthropic SDK calls. `node_span` already wraps all six nodes in the node's own
  coroutine, so one observation there nests everything by plain contextvar propagation. The roadmap
  and README are corrected in this change; see Complexity Tracking.
- **"Untraced" is a sampling decision made per turn** (research R4). The service owns an isolated
  `TracerProvider` whose sampler drops every span of a turn whose context carries the untraced flag;
  the flag comes from an `X-VisitDoc-Trace: off` header on that turn's request (R6). Measured: zero
  spans exported for a flagged turn, full traces for the one beside it — the only mechanism of five
  tried that is both per-request and complete.
- **Masking is `shared-logging`'s own rule, in two stages** (R7). The SDK's legacy `mask` hook
  cannot see status messages, which is where an exception's text — the spec's own example of a
  leaking secret — ends up. So the key-name rule runs when a structured value is recorded, and the
  export-time `mask_otel_spans`, which sees every attribute, runs off the event loop and fails
  closed, replaces configured secret values. Strings are recorded verbatim, so patient text that
  reads as JSON is never re-encoded. Its redaction is the log's redaction, made public in
  `shared-logging` rather than copied.

The new code is one package inside the chat service, `chat.observability`, which alone imports the
SDK (R14); five call sites, the retrieval pipeline, the tool registry and `node_span` call into it.
The harness gains a flag, three request headers, two record fields and a restart rule. No new
service, database, table or migration.

## Technical Context

**Language/Version**: Python 3.12 (the workspace's pinned version)

**Primary Dependencies**: `langfuse>=4.15.4,<5` added to `services/chat` only; it brings
`opentelemetry-api`/`-sdk`/`-exporter-otlp-proto-http` (1.33.1–<2). **Not** `langchain`, **not**
`opentelemetry-instrumentation-anthropic` (R2, R3). The harness gains no dependency; its purity
tests forbid both packages in `scoring/` and `comparison/` (R13).

**Storage**: none new. Traces live in Langfuse Cloud (30-day retention). The harness's `run.json`
gains `tracing`, and each case file gains `traces`; both default, so every stored run — including the
committed baseline `evals/baselines/01M321DWRXSVSY7GW9RY3CR9YW` — still reads and scores.

**Testing**: pytest, in the existing unit tier. Tracing tests build the tracer through the lifespan's
own factory with an injected `InMemorySpanExporter` and a unique public key per test (R9), and assert
on exported spans. The default test settings carry no Langfuse keys, so every existing test runs with
tracing off; a test pins that. The Anthropic fakes in `services/chat/tests/conftest.py` gain `usage`
(R10). No test reaches Langfuse.

**Target Platform**: the chat service under uvicorn on Linux (WSL2 locally), exporting over HTTPS to
`https://cloud.langfuse.com`.

**Project Type**: web service (FastAPI) plus its evaluation CLI.

**Performance Goals**: a traced turn's reply streams with no wait the patient can perceive beyond an
untraced one (SC-004). Span export is batched on a background thread; the caller-thread cost is
attribute serialization only. An untraced turn creates non-recording spans and pays near nothing.

**Constraints**: tracing may never change a turn (FR-011) or fail one (FR-012); no secret leaves the
process (FR-014); the Hobby allowance is 50k units/month, 6–23 per turn as measured (evaluation/units.md), measured in
this phase (FR-030).

**Scale/Scope**: one developer's hand-driven sessions plus eval runs of 97 cases. Six graph nodes,
five Anthropic call sites, five retrieval steps per FAQ request, seven tools.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Phase-gated scope | 2d is the roadmap's current, last Phase 2 subphase. No service, broker or infrastructure is added; the Cloud tier is chosen precisely so none is. One roadmap deviation — the node-span mechanism — is justified in Complexity Tracking and the roadmap is corrected in the same change. | Pass (justified deviation) |
| II. AI core is the centerpiece | Observability over the agent, retrieval and tool use is Phase 2's stated centerpiece. | Pass |
| III. Minimal service boundaries | Langfuse is an external dependency, and its failure handling is designed here, not later: a failed export is logged and never reaches a turn (FR-012, R8). The scheduling service is not instrumented (FR-020). | Pass |
| IV. Structured outputs & decoupled tools | Unchanged. The tool observation sits inside `ToolRegistry.dispatch`, behind the same seam. | Pass |
| V. Grounded retrieval & abstention | Unchanged; the trace records the gates' decisions from the same payload the log gets (R10), so it can never report a different decision. | Pass |
| VI. Documentation | README tracing section, roadmap correction, CLAUDE.md commands and the `.env` keys ship in the change (FR-031). | Pass |
| VII. Clean architecture | Only `chat.observability` imports the SDK, enforced by test (R14); call sites use domain-shaped functions. | Pass |
| VIII. TDD | Every contract in `contracts/` has its tests written and observed failing before implementation; tasks will order them so. | Pass |

**Post-design re-check**: unchanged — the design artifacts introduce no component beyond those
above. The one new mechanism with an invariant, the sampler flag, has that invariant written into
`contracts/tracing-headers.md` with a test per clause (per the project's "a new mechanism is the risky
fix" practice).

## Project Structure

### Documentation (this feature)

```text
specs/014-langfuse-tracing/
├── plan.md                        # This file
├── research.md                    # Phase 0: R1–R14
├── data-model.md                  # Phase 1: directive, observation tree, run/case fields
├── quickstart.md                  # Phase 1: setup and validation scenarios
├── contracts/
│   ├── trace-shape.md             # the observation tree, names, types, attributes
│   ├── tracing-headers.md         # X-VisitDoc-* request headers and the sampler invariant
│   └── run-record-tracing.md      # service.configured, turn.traced, run.json, case file, CLI/Make
├── checklists/requirements.md
└── evaluation/                    # FR-030: measured unit cost per turn shape and per run (at ship)
```

### Source Code (repository root)

```text
services/chat/src/chat/
├── observability/                 # NEW — the only importer of langfuse / opentelemetry
│   ├── __init__.py                #   turn_trace, step, generation, tool_call, record, TraceDirective; install/uninstall
│   │                              #   the one active tracer (never the SDK's get_client(), research R4)
│   ├── client.py                  #   build_tracer(settings, *, span_exporter=None): client + provider
│   ├── sampling.py                #   the untraced ContextVar and the flag-reading Sampler
│   ├── masking.py                 #   mask_otel_spans over shared_logging's redaction
│   └── log_bridge.py              #   stdlib langfuse/opentelemetry WARNING+ → tracing.export_failed
├── api/turn.py                    # headers → TraceDirective; root observation in run_pipeline; turn.traced
├── agent/node_logging.py          # node_span also opens the node's observation
├── agent/classify_intent.py       # generation(...) around messages.create
├── agent/answer_faq.py            # generation(...); retrieval step observations via record(...)
├── agent/handle_booking.py        # generation(...) per loop iteration
├── agent/small_talk.py            # generation(...)
├── agent/compose_answer.py        # generation(...); turn.completed payload onto the root
├── agent/graph.py                 # intent.classified payload onto the root
├── agent/tools/registry.py        # dispatch opens the tool observation
├── rag/retriever.py               # faq.embed / faq.search observations
├── rag/reranking.py               # rerank observation (its two events via record(...))
├── core/config.py                 # LANGFUSE_PUBLIC_KEY / _SECRET_KEY / _BASE_URL / _ENVIRONMENT
├── core/logging.py                # LANGFUSE_SECRET_KEY joins the secret fields
└── main.py                        # build/shutdown the tracer in lifespan; tracing_enabled in service.configured

packages/shared-logging/src/shared_logging/logging.py   # redaction made public API (R7)

evals/harness/src/golden_harness/
├── driver/turn.py                 # sends X-VisitDoc-Trace / -Eval-Run / -Eval-Case
├── driver/run.py                  # Run.tracing at start; restart/resume rule on tracing_enabled
├── driver/logslice.py             # reads turn.traced into the case's traces
├── record.py                      # RunTracing enum; Run.tracing; CaseRun.traces
├── cli.py                         # run --no-trace
└── comparison/render.py           # shows each run's tracing beside its identity
Makefile                           # eval-run TRACE=0|1

services/chat/tests/               # test_observability_*.py, test_tracing_boundary.py, updated fakes
evals/harness/tests/               # purity tuples; record/driver/comparison tests for the new fields
```

**Structure Decision**: one new package inside the existing `chat` workspace member. It is not a
`packages/shared-*` library because only one service is traced (FR-020) — a shared package with one
consumer would be indirection with no second reader, the reasoning this repo already applied to the
frontend style guide. `shared-logging` changes only to export the redaction it already owns.

## Complexity Tracking

| Deviation | Why Needed | Simpler / Prescribed Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Graph nodes are spanned by `node_span`, not by Langfuse's `CallbackHandler`, which `docs/ROADMAP.md` 2d names | The spec requires retrieval steps, generations and tool calls nested under their node (FR-003, FR-005). Measured: a span opened inside a node cannot be parented by the handler's node span, because the handler runs on an executor thread under a copied context (R2). | The handler as prescribed yields flat traces that fail FR-003/FR-005, records nothing about direct Anthropic calls, and adds the `langchain` package as a dependency to produce a span `node_span` already bounds. The roadmap bullet is rewritten in this change to say what was built and why. Accepted by the user 2026-09-22 (spec Clarifications). |
| A custom OpenTelemetry sampler (a new mechanism with an invariant) | FR-022/FR-024: one process must trace every turn except those that asked not to be. | Every simpler mechanism was measured and fails: not opening a root (strays become root traces), `suppress_instrumentation` (ignored by the SDK tracer), a second disabled client (per-key singleton), `sample_rate` (process-wide). `should_export_span` works but builds, masks and serializes every span it then discards. |
