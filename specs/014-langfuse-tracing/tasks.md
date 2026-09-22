---

description: "Task list for Tracing with Langfuse (Phase 2d)"
---

# Tasks: Tracing with Langfuse (Phase 2d)

**Input**: Design documents from `/specs/014-langfuse-tracing/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: Per the constitution's Test-Driven Development principle (VIII), every test task precedes
the implementation it covers, and the tests are **observed failing** before that implementation is
written.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: the user story the task belongs to (US1, US2, US3); setup, foundational and polish
  tasks carry none

## Path conventions

The tracing code is one new package, `services/chat/src/chat/observability/`, the only module
allowed to import `langfuse` or `opentelemetry` (research R14). Chat tests are colocated at
`services/chat/tests/`, harness tests at `evals/harness/tests/`. Paths below are repo-relative.

## Things that are easy to get wrong here

1. **The SDK's client is a singleton per public key** (research R1, R9). Every test that builds a
   tracer passes a unique public key and its own `InMemorySpanExporter`, or it silently reads another
   test's spans. Use the fixture from T005 — never construct `Langfuse(...)` in a test directly.
2. **Blank keys are not missing keys** (R1). `.env.example` ships `ANTHROPIC_API_KEY=` blank, and a
   copied `LANGFUSE_PUBLIC_KEY=` would build a live exporter that 401s. `tracing_enabled` is decided
   in `Settings` from stripped values and passed to the SDK explicitly; the SDK never reads
   `LANGFUSE_*` from the environment itself.
3. **Never pass the global tracer provider** (R1). Always the isolated provider from
   `build_tracer`; `set_tracer_provider` succeeds once per process and would leak into every later
   test.
4. **A cancellation looks like success unless the wrapper says otherwise** (R8). OTel catches only
   `Exception`; `CancelledError` must be handled in `chat.observability`'s wrapper explicitly.
5. **The untraced flag is set in exactly one place** (contracts/tracing-headers.md S6): the stream
   generator in `api/turn.py`, before `asyncio.create_task(run_pipeline())`. Setting it anywhere
   later misses spans; setting it anywhere earlier leaks it.
6. **`get_current_trace_id()` lies in an untraced turn** (R5). It returns a well-formed id that was
   never exported. `turn.traced` is logged only when the root observation `is_recording()`.
7. **One payload, two sinks** (R10). A retrieval span's output is the very dict the log event was
   emitted with. Building a second dict "with the same fields" is the defect FR-006 exists to
   prevent.
8. **`tracing` is not a run condition** (R12). Adding it to `RunConditions` would put it into every
   condition delta and band check. It lives on `Run`.

---

## Phase 1: Setup

**Purpose**: the dependency and the configuration template.

- [X] T001 Add `langfuse>=4.15.4,<5` to `services/chat` with `uv add --package chat 'langfuse>=4.15.4,<5'` (updates `services/chat/pyproject.toml` and `uv.lock`); confirm with `uv tree --package chat | grep -i -e langchain -e instrumentation` that neither `langchain` nor any OTel instrumentation package came with it (research R2, R3)
- [X] T002 [P] Add `LANGFUSE_PUBLIC_KEY=`, `LANGFUSE_SECRET_KEY=`, and commented `LANGFUSE_BASE_URL`/`LANGFUSE_ENVIRONMENT` lines with their defaults to `.env.example`, with a comment that blank keys mean tracing off

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: the shared redaction rule, settings, the tracer factory, the sampler, the mask, the
log bridge and the observation API every story calls. No story can start until this phase is done.

### Test infrastructure

- [X] T003 [P] Extend the Anthropic fakes in `services/chat/tests/conftest.py` with token usage: `FakeFinalMessage` gains a `usage` (`input_tokens`, `output_tokens`, `cache_read_input_tokens`), and `_mock_text_response`/`_mock_tool_use_response` set a real `usage` object instead of leaving a MagicMock attribute (research R3, R10)
- [X] T004 [P] Add a pinned test in `services/chat/tests/test_config.py` that `Settings()` under the test environment has tracing disabled (no Langfuse keys) — so every existing test runs untraced (research R9); observe it fail on the missing property
- [X] T005 Add a `span_exporter` fixture to `services/chat/tests/conftest.py`: builds a tracer through `chat.observability.client.build_tracer` with an `InMemorySpanExporter`, a fresh ULID public key, tracing enabled; installs it with `chat.observability.install(...)`; yields the exporter; flushes, uninstalls and shuts the tracer down after the test; plus a `finished_spans()` helper returning spans by name (research R9)

### Tests for the foundation (write first, observe failing)

- [X] T006 [P] Test the public redaction API in `packages/shared-logging/tests/test_logging.py`: `redact_value(value, known_secrets)` replaces secret values inside nested str/dict/list values, `is_secret_key(name)` matches the existing key pattern, `known_secret_values(settings, secret_fields, secret_url_fields)` returns the same set the log processor uses today; the existing processor tests keep passing unchanged (research R7)
- [X] T007 Test Langfuse settings in `services/chat/tests/test_config.py`: `tracing_enabled` is true only when both keys are non-blank after `strip()`; `"  "` keys → false; `LANGFUSE_BASE_URL` defaults to `https://cloud.langfuse.com`; `LANGFUSE_ENVIRONMENT` defaults to `development` and rejects uppercase or a `langfuse` prefix at startup (data-model.md §3)
- [X] T008 [P] Test in `services/chat/tests/test_logging.py` that `LANGFUSE_SECRET_KEY` is in `_SECRET_SETTINGS_FIELDS` and its value is redacted from a logged event (FR-016)
- [X] T009 [P] Test the tracer factory and sampler in `services/chat/tests/test_observability_client.py`: with tracing enabled an observation is exported; with it disabled nothing is and no exporter is built; the provider is not the global one (`trace.get_tracer_provider()` unchanged); with the untraced `ContextVar` set, spans are non-recording and none is exported, **including** one opened with an explicit seeded `trace_context` (the flag outranks the parent's sampled bit, research R4)
- [X] T010 [P] Test masking in `services/chat/tests/test_observability_masking.py`: a span whose input, output, metadata and status message contain each configured secret value (Anthropic, Voyage, Langfuse secret key, `ADMIN_SECRET`, the credential parts of `DATABASE_URL`/`QDRANT_URL`) and a secret-named key exports **no attribute and no span event** containing any of them; patient text and a display name in the same span are exported unchanged; a mask function that raises drops the batch and exports nothing (contracts/trace-shape.md C5, FR-014, FR-015)
- [X] T011 [P] Test observation outcomes in `services/chat/tests/test_observability_observations.py`: `step(...)` that completes → level `DEFAULT`; raises `ValueError("boom <secret>")` → level `ERROR`, status message `ValueError: boom ***REDACTED***`, exception re-raised; raises `asyncio.CancelledError` → level `WARNING`, status `cancelled`, re-raised; `record(step, event, payload)` logs `event` with exactly `payload` and sets the observation output to the same object; children opened in two `asyncio.gather` tasks nest under the enclosing step (contracts/trace-shape.md C4, research R8, R10)
- [X] T012 [P] Test the log bridge in `services/chat/tests/test_observability_log_bridge.py`: a WARNING+ record on the stdlib `opentelemetry` or `langfuse` logger is logged through structlog as `tracing.export_failed` with `logger` and `message`; an INFO record is not; a record whose message contains a secret is redacted (research R8)
- [X] T013 [P] Test the dependency boundary in `services/chat/tests/test_tracing_boundary.py`: walk `services/chat/src/chat` with `ast`; any module outside `chat/observability/` importing `langfuse` or `opentelemetry` fails the test (research R14)

### Implementation for the foundation

- [X] T014 Make the redaction public in `packages/shared-logging/src/shared_logging/logging.py`: expose `redact_value`, `is_secret_key`, `known_secret_values` (renaming the private `_redact_value`, `_SECRET_KEY_PATTERN` check and `_known_secret_values`), export them from `packages/shared-logging/src/shared_logging/__init__.py`, and have the existing processor call the public functions so the log and the trace run one rule (research R7) — T006 passes
- [X] T015 Add `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`, `LANGFUSE_ENVIRONMENT` and the derived `tracing_enabled` property (with the environment validator) to `services/chat/src/chat/core/config.py`, and add `LANGFUSE_SECRET_KEY` to `_SECRET_SETTINGS_FIELDS` in `services/chat/src/chat/core/logging.py` — T004, T007, T008 pass
- [X] T016 Create `services/chat/src/chat/observability/sampling.py`: the module-level untraced `ContextVar[bool]` (default `False`) and the `Sampler` returning `Decision.DROP` when it is set, else delegating to `ParentBased(ALWAYS_ON)` — the flag checked first (research R4)
- [X] T017 Create `services/chat/src/chat/observability/masking.py`: a `mask_otel_spans(*, params)` built from the settings' known secret values, returning `OtelSpanPatch`es that apply `shared_logging.redact_value` to every string (and string-sequence) attribute and replace values under secret-named keys (research R7) — T010's attribute clauses pass
- [X] T018 Create `services/chat/src/chat/observability/client.py`: `build_tracer(settings, *, span_exporter=None) -> Tracer` constructing `Langfuse(public_key=…, secret_key=…, base_url=…, tracing_enabled=settings.tracing_enabled, environment=settings.LANGFUSE_ENVIRONMENT, tracer_provider=TracerProvider(sampler=…), mask_otel_spans=…, span_exporter=…)` — every value from `Settings`, none from the SDK's own env lookup (FR-017, research R1) — T009 passes
- [X] T019 Create `services/chat/src/chat/observability/log_bridge.py`: a stdlib `logging.Handler` attached to the `langfuse` and `opentelemetry` loggers at WARNING that re-emits each record through structlog as `tracing.export_failed` (research R8) — T012 passes
- [X] T020 Create `services/chat/src/chat/observability/__init__.py` with the domain-shaped API: `turn_trace(turn_id, *, chat_id, session_id, directive, input)` (opens the root with `trace_context` seeded from `turn_id`, sets the trace attributes of data-model.md §2 before any child, holds the root handle in a `ContextVar` for the turn's duration, and reports whether it is recording), `step(name, *, as_type="span", input=None)`, `generation(name, *, model, input, model_parameters)` yielding a handle with `record_completion(output, usage, stop_reason, completion_start_time=None)`, `tool_call(name, arguments)`, `record(step, event, payload)`, `install(tracer)`/`uninstall()` holding the one active tracer in module state — the API reads only that, never the SDK's `get_client()`, which returns a disabled client once a process holds more than one (research R4) — and the outcome handling of contracts/trace-shape.md C4 (Exception → ERROR with redacted `Type: message`; CancelledError → WARNING `cancelled`; always re-raise). Every function is a no-op that still runs its body when tracing is off. If T010's event clause fails because the SDK records exception events, suppress them here (research R7 open risk) — T010, T011 pass
- [X] T021 Test and wire the lifespan in `services/chat/tests/test_main.py` then `services/chat/src/chat/main.py`: `service.configured` gains `tracing_enabled` (update the pinned field set in `test_main.py`, keep the no-secret assertion); the lifespan builds the tracer via `build_tracer`, installs the log bridge, installs the tracer with `chat.observability.install(...)`, and registers `chat.observability.uninstall` and `await asyncio.to_thread(tracer.shutdown)` on its exit stack; `RunConditions.from_event` still ignores the new field (assert in `evals/harness/tests/test_record.py`) (FR-018, FR-019, contracts/run-record-tracing.md)
- [X] T022 Run `make lint typecheck` and `uv run pytest services/chat/tests/test_config.py services/chat/tests/test_logging.py services/chat/tests/test_main.py services/chat/tests/test_observability_client.py services/chat/tests/test_observability_masking.py services/chat/tests/test_observability_observations.py services/chat/tests/test_observability_log_bridge.py services/chat/tests/test_tracing_boundary.py packages/shared-logging/tests/test_logging.py evals/harness/tests/test_record.py --import-mode=importlib`; all pass

**Checkpoint**: a tracer exists, masks, samples per context, never touches the global provider, and
is off whenever keys are blank. Nothing yet opens an observation.

---

## Phase 3: User Story 1 — Open the trace of a turn and see why it went that way (Priority: P1) 🎯 MVP

**Goal**: every turn exports one trace whose tree matches contracts/trace-shape.md C1–C4, with every
model call's model and tokens and every retrieval decision equal to its log event.

**Independent Test**: with the `span_exporter` fixture, drive one mixed FAQ-and-booking turn
through the app with fakes; the exported tree has the root, the four nodes, `request[0]`'s five
retrieval steps and generation, the roster read and one tool call, and each FAQ step's output equals
its log event's fields.

### Tests for User Story 1 (write first, observe failing)

- [X] T023 [P] [US1] Test the turn root in `services/chat/tests/test_turn_tracing.py`: one trace per turn with `trace_id == create_trace_id(seed=turn_id)`; `session.id` = chat id, `user.id` = session id, trace name `turn`, environment `development`, metadata `turn_id`; root input = the patient message, output = the `turn.completed` payload, metadata `intent_classified` = the `intent.classified` payload, `turn_outcome=completed`; `turn.traced` logged once with that `trace_id`; a silenced (paused) message exports nothing and logs no `turn.traced` (C1, C2, C3, FR-001, FR-002, FR-007, FR-010)
- [X] T024 [US1] Test failure and cancellation roots in `services/chat/tests/test_turn_tracing.py`: a turn whose generation raises exports its trace with the failing generation at `ERROR` and root `turn_outcome=failed`; a turn superseded by a staff post has its root at `WARNING` `cancelled` and `turn_outcome=cancelled`, not `failed` (FR-009, C4)
- [X] T025 [P] [US1] Test node observations in `services/chat/tests/test_node_logging.py`: `node_span("x")` opens an observation named `x` under the current one, sets its output to the `result` payload `node.completed` carries, and marks failed/cancelled per C4; the existing `node.*` log assertions are unchanged (FR-003)
- [X] T026 [P] [US1] Test the classifier generation in `services/chat/tests/test_classify_intent.py`: `classify_intent.model` is a generation under `classify_intent` with `model=CLASSIFICATION_MODEL`, the system prompt and messages as input, and `usage_details` from the fake's usage (FR-004)
- [X] T027 [P] [US1] Test the FAQ generation and retrieval steps in `services/chat/tests/test_answer_faq.py`: for a two-request turn, two `request[<position>]` spans under `answer_faq`, each with `faq.embed`, `faq.search` (type `retriever`), `faq.similarity_gate`, `faq.rerank`, `faq.rerank_gate`, `faq.verdict` and, when answered, `answer_faq.model` with `completion_start_time`; each step's output **equals** the fields of its `faq.*` log event captured from the same turn, with `faq.retrieval_completed`'s fields as the `retrieval_completed` metadata of `faq.similarity_gate` and `faq.search`'s output the returned pool (contracts/trace-shape.md C3); an empty corpus exports no embed/search/gate spans; an abstention exports no `answer_faq.model` (FR-005, FR-006, C3)
- [X] T028 [P] [US1] Test the rerank step in `services/chat/tests/test_reranking.py`: a completed rerank sets the step output to the `faq.reranking_completed` fields; an unavailable rerank sets level `WARNING`, status = the `RerankFailureReason`, output = the `faq.reranking_unavailable` fields (C3, C4)
- [X] T029 [P] [US1] Test the booking generations in `services/chat/tests/test_handle_booking.py`: one `handle_booking.model[<iteration>]` generation per loop iteration under `handle_booking`, input including the tool definitions, output the response content blocks, usage recorded; a `max_tokens` stop is level `WARNING` (FR-004, C4)
- [X] T030 [P] [US1] Test tool observations in `services/chat/tests/test_tool_registry_tracing.py`: `ToolRegistry.dispatch` opens `tool:<name>` (type `tool`) with the arguments as input and the `ToolResult` as output; the `_read_roster` call of `list_practitioners` appears as `tool:list_practitioners` before the first `handle_booking.model[0]`; status `unknown`/`unavailable` → `WARNING`; `ToolArgumentError`, `UnknownToolError` and a raising handler → `ERROR`; two tool calls dispatched concurrently in one iteration both nest under `handle_booking` (FR-008, R11)
- [X] T031 [P] [US1] Test the small-talk and compose generations in `services/chat/tests/test_small_talk.py` and `services/chat/tests/test_compose_answer.py`: `small_talk.model` and `compose_answer.model` generations with model, input, output and usage; no `compose_answer.model` for a single-specialist or all-abstained turn (FR-004, C3)
- [X] T032 [P] [US1] Test non-interference in `services/chat/tests/test_tracing_non_interference.py`: the same scripted turns (FAQ, booking, merged, small talk, a stopping reason, an all-abstained turn) run once with tracing off and once on yield identical stream events, stored message, `request_outcomes`, escalation and log events except `turn.traced`/`tracing.export_failed`; with an exporter that raises on every export and with one that blocks, the turn's events and records are unchanged, its `done` is not delayed beyond the untraced run's, and `tracing.export_failed` is logged (FR-011, FR-012, FR-013, C6, SC-003, SC-004)

### Implementation for User Story 1

- [X] T033 [US1] Open the node observation in `node_span` in `services/chat/src/chat/agent/node_logging.py` via `chat.observability.step`, setting its output from the same `result` payload `node.completed` logs (R2) — T025 passes
- [X] T034 [US1] Open the turn root in `run_pipeline` in `services/chat/src/chat/api/turn.py`: `turn_trace(turn_id, ...)` from T020 (R5); log `turn.traced` with the trace id only when the root is recording; set `turn_outcome` on the completed path, in the `TurnPipelineError`/`Exception` branches before `_settle_the_failure`, and on cancellation; add a `chat.observability` helper that writes to the root handle `turn_trace` holds (a no-op outside a turn) so that `TurnCompletion.emit()` in `services/chat/src/chat/agent/compose_answer.py` and `classify_intent_node` in `services/chat/src/chat/agent/graph.py` put the very payloads they log onto the root (FR-007, R10) — T023, T024 pass
- [X] T035 [P] [US1] Wrap the classifier call in `services/chat/src/chat/agent/classify_intent.py` in `generation("classify_intent.model", ...)`, recording output and `response.usage` — T026 passes
- [X] T036 [US1] In `services/chat/src/chat/agent/answer_faq.py`: open `request[<position>]` inside `_answer_one`'s `bound_contextvars(segment=…)` block; wrap embed and search (in `services/chat/src/chat/rag/retriever.py`, `faq.search`'s output being the returned pool), and the two gates and the verdict in `_run_pipeline`, in steps whose output is set through `record(...)` from the payload each `faq.*` event is emitted with — `_log_retrieval` and the `faq.similarity_gate` event both inside the similarity-gate step, the former as its `retrieval_completed` metadata; wrap the generation in `generation("answer_faq.model", ...)` with `completion_start_time` from the first streamed chunk — T027 passes
- [X] T037 [P] [US1] In `services/chat/src/chat/rag/reranking.py`, open the `faq.rerank` step around `rerank_chunks` and emit `faq.reranking_completed` / `faq.reranking_unavailable` through `record(...)`, the unavailable path at `WARNING` — T028 passes
- [X] T038 [P] [US1] Wrap each loop iteration's `messages.create` in `services/chat/src/chat/agent/handle_booking.py` in `generation(f"handle_booking.model[{iteration}]", ...)`, input including `tools`, `WARNING` on `max_tokens` — T029 passes
- [X] T039 [P] [US1] Open `tool_call(name, arguments)` inside `ToolRegistry.dispatch` in `services/chat/src/chat/agent/tools/registry.py`, output the `ToolResult`, `WARNING` for `unknown`/`unavailable` (R11) — T030 passes
- [X] T040 [P] [US1] Wrap the calls in `services/chat/src/chat/agent/small_talk.py` and `services/chat/src/chat/agent/compose_answer.py` in `generation("small_talk.model", ...)` / `generation("compose_answer.model", ...)` with `completion_start_time` — T031 passes
- [X] T041 [US1] Run T032 and fix any divergence it reports at its source; then run `uv run pytest services/chat/tests --import-mode=importlib -q` — the whole chat unit suite passes with the fakes of T003

**Checkpoint**: MVP. A hand-driven turn against a configured stack produces the trace quickstart
Scenario 1 describes.

---

## Phase 4: User Story 2 — From a surprising case in an eval run, open its trace (Priority: P2)

**Goal**: eval turns carry run and case ids and the `eval` environment; each case file names its
turns' trace ids.

**Independent Test**: a harness test drives two fixture cases against a fake stack whose log carries
`turn.traced`; each case file's `traces` maps its patient message id to the logged trace id, and the
requests carried both eval headers.

### Tests for User Story 2 (write first, observe failing)

- [X] T042 [P] [US2] Test eval headers in `services/chat/tests/test_turn_tracing.py`: `X-VisitDoc-Eval-Run` + `X-VisitDoc-Eval-Case` → trace metadata `eval_run_id`/`eval_case_id` and environment `eval`; neither → no eval metadata, environment from settings; exactly one, or a malformed run id or case id → 422 with no message stored and no turn run (contracts/tracing-headers.md, FR-026)
- [X] T043 [P] [US2] Test the case record in `evals/harness/tests/test_record.py`: `CaseRun.traces` defaults to `{}`; a case file written before this phase (fixture without the field) still reads; `extra="forbid"` still rejects unknown fields (data-model.md §4)
- [X] T044 [P] [US2] Test in `evals/harness/tests/driver/test_logslice.py` that a turn group's `turn.traced` event yields its `trace_id`, and a group without one yields none
- [X] T045 [P] [US2] Test in `evals/harness/tests/driver/test_turn.py` that `post_turn` sends `X-VisitDoc-Eval-Run` and `X-VisitDoc-Eval-Case` on every request, and no `X-VisitDoc-Trace` header when tracing is requested
- [X] T046 [P] [US2] Test in `evals/harness/tests/driver/test_run.py` that a driven case's file carries `traces` = {patient message id: trace id} from its slice, and that a case re-driven on resume carries only the traces of the turns driven in the resumed attempt (spec edge case)

### Implementation for User Story 2

- [X] T047 [US2] Parse the eval headers in the `/chat` route in `services/chat/src/chat/api/turn.py` into a `TraceDirective` (from `chat.observability`), validated per contracts/tracing-headers.md, and pass its metadata and environment to the root's trace attributes — T042 passes
- [X] T048 [P] [US2] Add `traces: dict[str, str] = Field(default_factory=dict)` to `CaseRun` in `evals/harness/src/golden_harness/record.py` — T043 passes
- [X] T049 [P] [US2] Read `turn.traced` in `evals/harness/src/golden_harness/driver/logslice.py` — T044 passes
- [X] T050 [US2] Send the eval headers from `post_turn` in `evals/harness/src/golden_harness/driver/turn.py` (run id and case id threaded from `_drive_cases` in `evals/harness/src/golden_harness/driver/run.py`), and fill `CaseRun.traces` from each turn's slice in `driver/run.py` — T045, T046 pass

**Checkpoint**: quickstart Scenario 3 works: a case file leads to its trace in one step.

---

## Phase 5: User Story 3 — Take an untraced run when traces would be wasted (Priority: P3)

**Goal**: `make eval-run TRACE=0` drops exactly its own turns' traces, records why, and compares as
the same conditions.

**Independent Test**: the chat tests S1–S6 pass against the in-memory exporter, and a harness test
shows a `--no-trace` run records `untraced_by_request`, sends `X-VisitDoc-Trace: off` on every turn,
and compares against a traced run with an empty condition delta.

### Tests for User Story 3 (write first, observe failing)

- [X] T051 [P] [US3] Test the sampler invariant in `services/chat/tests/test_tracing_headers.py`, one test per clause S1–S6 of contracts/tracing-headers.md (untraced turn exports zero spans; a concurrent traced turn on another chat exports fully; the next turn on the same chat is traced; a superseded `off` turn exports nothing; the header never turns tracing on in a disabled service; an AST check that only `api/turn.py` sets the flag), plus `X-VisitDoc-Trace: yes` → 422 (FR-022, FR-024, SC-007)
- [X] T052 [P] [US3] Test `RunTracing` and `Run.tracing` in `evals/harness/tests/test_record.py`: three values; default `untraced_service_off`; the committed baseline `evals/baselines/01M321DWRXSVSY7GW9RY3CR9YW/run.json` still reads and reads as `untraced_service_off` (FR-025, data-model.md §4)
- [X] T053 [P] [US3] Test in `evals/harness/tests/driver/test_run.py`: a run started with tracing requested against `tracing_enabled: true` records `traced`, against `false` or an event without the field records `untraced_service_off`, and with `--no-trace` records `untraced_by_request` either way; with `--no-trace` every turn is sent `X-VisitDoc-Trace: off`; a restart stating a different `tracing_enabled` stops a `traced` run with `ServiceRestartedError` naming the field but not an `untraced_by_request` run; a restart with the same conditions and same `tracing_enabled` is still passed over (FR-023); `resume_run` refuses a `traced` run when the service now says `false`, and resumes an `untraced_by_request` run still sending `off` (contracts/run-record-tracing.md)
- [X] T054 [P] [US3] Test the CLI in `evals/harness/tests/test_cli.py`: `run --no-trace` parses; `run --resume <id> --no-trace` is a usage error (contracts/run-record-tracing.md)
- [X] T055 [P] [US3] Test in `evals/harness/tests/comparison/` (the existing condition-delta and render test modules) that a traced and an untraced run over the same cases and conditions compare with an empty condition delta, that the rendered report shows each run's `tracing` beside its id, and that `band` accepts five runs mixing traced and untraced (FR-028)
- [X] T056 [P] [US3] Add `"langfuse"` and `"opentelemetry"` to `_FORBIDDEN` in `evals/harness/tests/scoring/test_purity.py` and `evals/harness/tests/comparison/test_purity.py` (FR-029, R13)

### Implementation for User Story 3

- [X] T057 [US3] Parse `X-VisitDoc-Trace` in the `/chat` route in `services/chat/src/chat/api/turn.py` into the `TraceDirective`, and set the untraced `ContextVar` from it in `_event_stream` immediately before `asyncio.create_task(run_pipeline())` — nowhere else (contracts/tracing-headers.md S6) — T051 passes
- [X] T058 [US3] Add `RunTracing` and `Run.tracing` to `evals/harness/src/golden_harness/record.py` — T052 passes
- [X] T059 [US3] In `evals/harness/src/golden_harness/driver/run.py`: take `tracing_requested` into `drive_run`, derive `Run.tracing` from it and the starting `service.configured` event, send `X-VisitDoc-Trace: off` from `post_turn` in `evals/harness/src/golden_harness/driver/turn.py` for an `untraced_by_request` run, extend `_RestartWatch` and `resume_run` with the `tracing_enabled` rule — T053 passes
- [X] T060 [US3] Add `--no-trace` to the `run` subparser in `evals/harness/src/golden_harness/cli.py` (a usage error with `--resume`) and pass it to `drive_run` — T054 passes
- [X] T061 [P] [US3] Show each run's `tracing` beside its id in `evals/harness/src/golden_harness/comparison/render.py` without touching `comparison/conditions.py` or `comparison/band.py` — T055 passes
- [X] T062 [US3] Map `TRACE` in the `eval-run` target of `Makefile`: `TRACE=0` → `--no-trace`; unset or `1` → nothing; any other value → `$(error …)` naming `0` and `1`; update the target's comment block (contracts/run-record-tracing.md). The Makefile has no test suite, so check it by dry run: before the edit, observe `make -n eval-run TRACE=0` print no `--no-trace` and `make -n eval-run TRACE=yes` succeed; after it, the first prints `--no-trace`, `make -n eval-run` and `TRACE=1` print none, and `TRACE=yes` fails naming `0` and `1`
- [X] T063 [US3] Run `uv run pytest evals/harness/tests services/chat/tests/test_tracing_headers.py --import-mode=importlib -q`; all pass

**Checkpoint**: quickstart Scenario 4 works.

---

## Phase 6: Polish & cross-cutting concerns

- [X] T064 [P] Rewrite the Phase 2d bullet "Traces reach Langfuse over OpenTelemetry, not through the logs" in `docs/ROADMAP.md` to say node spans come from `node_span`, not the callback handler, and why (plan.md Complexity Tracking); settle the "how the harness's choice reaches the chat service is the spec's to settle" clause with the header
- [X] T065 [P] Update `README.md`'s "Tracing with Langfuse: technology choices": drop "nothing is built yet"; add the node-span decision, the per-turn sampler, export-time masking and the settings to set, each with its tradeoff (FR-031, constitution VI)
- [X] T066 [P] Update `.claude/CLAUDE.md`: the `make eval-run` paragraph gains `TRACE=0`; the Key design decisions list gains a tracing entry (only `chat.observability` imports the SDK; `tracing` is not a run condition; the untraced flag's one setter); fix the Makefile's `eval-run` comment accordingly
- [X] T067 Sibling sweep: grep `services/chat/src` for every `messages.create`/`messages.stream`, every `registry.dispatch`, and every `faq.*` event emission, and confirm each sits inside a `generation`/`tool_call`/`record` — no call site left untraced (memory: sibling sweep after fixes)
- [X] T068 Run `make lint typecheck test-unit`; all pass
- [X] T069 Walk quickstart Scenarios 1–6 against a real Langfuse Hobby project and note any divergence in `specs/014-langfuse-tracing/quickstart.md`
- [X] T070 Measure and commit `specs/014-langfuse-tracing/evaluation/units.md`: units per turn shape and for one full traced golden-set run, with build and date, then replace the "15–25 per turn" and "roughly 3k" estimates in `docs/ROADMAP.md` and `README.md` with the measured numbers (FR-030, SC-009) — done 2026-09-22 per the user's choice of the offline split: per-shape units measured from the test exporter and the full-run figure weighted from them; the observed full-run total is T084

---

## Dependencies & execution order

### Phase dependencies

- **Setup (Phase 1)**: none.
- **Foundational (Phase 2)**: after T001. Blocks every story.
- **US1 (Phase 3)**: after Phase 2. The MVP.
- **US2 (Phase 4)**: after Phase 2; its chat half (T042, T047) needs T034's root from US1. Its harness
  half (T043–T046, T048–T050) needs only Phase 2.
- **US3 (Phase 5)**: after Phase 2; T051/T057 need T034 (the root and the stream generator's
  shape). The harness half needs T050's header plumbing in `post_turn`.
- **Polish (Phase 6)**: after the stories it documents; T069–T070 need all three.

### Within each story

Tests first and observed failing; then the implementation tasks, each naming the test it turns green.
`api/turn.py` is edited by T034, T047 and T057 in that order — not in parallel.

### Parallel opportunities

- Phase 2: T006–T013 are independent test files; T016, T017, T019 are independent modules once
  T014/T015 land.
- US1: T026–T031 are six test files; T035, T037, T038, T039, T040 are five source files.
- US2: T043–T046 are four harness test files; T048/T049 two source files.
- US3: T052–T056 are independent test files.
- Polish: T064–T066 are three documents.

### Parallel example: User Story 1

```bash
# the call-site tests together:
T026 test_classify_intent.py   T027 test_answer_faq.py   T028 test_reranking.py
T029 test_handle_booking.py    T030 test_tool_registry_tracing.py   T031 test_small_talk/compose
# then the call sites together:
T035 classify_intent.py   T037 reranking.py   T038 handle_booking.py   T039 registry.py   T040 small_talk/compose
```

## Implementation strategy

1. **MVP**: Phases 1–3. A hand-driven turn is fully traced; eval runs are traced too (they are just
   turns), but without run/case tagging and without an off switch — acceptable for a first commit,
   since nothing yet depends on either.
2. **US2**: eval traces become findable from a case. Commit.
3. **US3**: the off switch and the run's record of it. Commit.
4. **Polish**: documentation, the real-Langfuse walk-through, and the measured unit cost. The last is
   the only step that spends Langfuse units on purpose.

## Phase 7: Convergence

- [X] T071 Apply the key-name redaction rule to structured `input`, `output` and `metadata` values before they are serialized onto a span (recursing as `shared_logging.redact_value` does for the log, leaving `usage_details` alone), with a test that a secret-named key nested in a step's input or output is masked even when its value is not a configured secret, in `services/chat/src/chat/observability/masking.py` / `services/chat/src/chat/observability/__init__.py` and `services/chat/tests/test_observability_masking.py` per FR-014, contracts/trace-shape.md C5 (partial)
- [X] T072 Fill `CaseRun.traces` from the `turn.traced` events of every posted attempt's log slice, including cases recorded as run errors or outcome unknown, so a missing entry in a traced run means only "silenced", in `evals/harness/src/golden_harness/driver/run.py` with tests in `evals/harness/tests/driver/test_run.py` per FR-027, data-model.md §4 (partial)
- [X] T073 Record `turn_outcome=cancelled` on the root for every reply outcome other than `STORED` (a takeover after deregistration, or not generated), not only for `CancelledError`, in `services/chat/src/chat/api/turn.py` with a test in `services/chat/tests/test_turn_tracing.py` per FR-009, contracts/trace-shape.md C4, US1/AC6 (partial)
- [X] T074 Add the turn-level masking test contracts/trace-shape.md C5 describes — a failing generation and tool arguments and results carrying every configured secret, asserting over all exported attributes and span events — in `services/chat/tests/test_observability_masking.py` per FR-015, SC-008 (partial)
- [X] T075 Make every lost export produce a `tracing.export_failed` line despite OpenTelemetry's 20-second duplicate filter, in `services/chat/src/chat/observability/` per SC-004 (partial) — resolved 2026-09-22 by restating SC-004 instead (spec Clarifications): export failures are logged, not one line per loss; no code change
- [X] T076 Guard `emit` in `services/chat/src/chat/observability/log_bridge.py` with try/except calling `self.handleError(record)` so the bridge can never raise into the SDK's thread or a turn, with a test in `services/chat/tests/test_observability_log_bridge.py` per FR-012 (partial)

## Phase 8: Convergence

- [X] T077 Mark the turn root level `WARNING` with status `cancelled` on every reply outcome other than `STORED`, matching the `CancelledError` path, and assert level and status in `test_a_turn_whose_reply_was_not_stored_is_marked_cancelled`, in `services/chat/src/chat/api/turn.py` and `services/chat/tests/test_turn_tracing.py` per contracts/trace-shape.md C4, FR-009 (partial)
- [X] T078 Update `evals/harness/README.md` with `make eval-run TRACE=0` / `--no-trace` and its refusal with `--resume`, `run.json`'s `tracing` and the case file's `traces`, and the rule that a restart changing `tracing_enabled` stops a traced or service-off run and a resume refuses it, per FR-031, Constitution VI (partial)
- [X] T079 Stop the mask from JSON-decoding a payload that was a plain string when it was recorded — apply the key-name rule to structured (dict/list) values where `chat.observability` records them, and keep only the configured-secret-value pass in the export-time mask — so a patient message that happens to be JSON is exported verbatim, with a test and the `masking.py` docstring and README corrected, in `services/chat/src/chat/observability/` and `services/chat/tests/test_observability_masking.py` per FR-014 Clarifications, FR-006 (partial)

## Phase 9: Convergence

- [X] T080 Make `test_an_exporter_that_fails_every_export_changes_nothing_and_is_logged` deterministic: its traced/untraced thread comparison depends on the row order of two messages with equal `created_at`, in `services/chat/tests/test_tracing_non_interference.py` or `chat_repository.list_messages` per C6, FR-011, SC-003 (partial)
- [X] T081 Correct the tracing bullet in `.claude/CLAUDE.md` to the two-stage masking T079 built — the key-name rule on structured values at record time, the configured-secret-value pass at export, strings recorded verbatim — per T079 (partial)
- [X] T082 Bring `specs/014-langfuse-tracing/contracts/trace-shape.md` C5, `plan.md`'s masking decision and `research.md` R7 in line with the two-stage masking T079 built per T079 (partial)
- [X] T083 Reword `TurnOutcome.FAILED`'s docstring in `services/chat/src/chat/observability/__init__.py` to what contracts/trace-shape.md C4 says — the pipeline raised, the path `_settle_the_failure` handles — since it is also set after a delivered reply per FR-009, C4 (partial)

## Phase 10: Follow-up

- [ ] T084 After the next full traced `make eval-run`, read the project's unit count before and after (Settings → Usage, nothing else sending traces), record the observed full-run total in `specs/014-langfuse-tracing/evaluation/units.md`, and replace the expected ~1.3k in `README.md` and `docs/ROADMAP.md` with it per FR-030, SC-009
