# Research: Tracing with Langfuse (Phase 2d)

Every SDK fact below was read from the `langfuse` 4.15.4 wheel (released 2026-09-16) and the
OpenTelemetry 1.44 wheels, and the load-bearing ones were confirmed by a scratch experiment against
this repo's own pins (langgraph 1.2.10, langchain-core 1.5.3) in a throwaway venv. Nothing was
installed into the workspace. File references into the SDK are `langfuse/<path>:<line>` as of that
wheel.

---

## R1. SDK version and client construction

**Decision**: `langfuse>=4.15.4,<5` as a dependency of `services/chat` only. One client per process,
built in the lifespan from `Settings`, with an **isolated** `TracerProvider` passed in
(`tracer_provider=`), never the global one.

**Rationale**: v4 is current (v4.0.0 2026-03-10); v3's `start_as_current_span`/`update_current_trace`
are gone, so code written against v3 examples would not import. Passing our own provider is what
R4 needs (a custom sampler), and it keeps Langfuse from calling `set_tracer_provider`, which OTel
allows once per process and which would leak into every test after the first.

Constructor facts that shape the design (`_client/client.py:313-397`):

- Missing keys (`None`) → a warning and a `NoOpTracer`; nothing raises, `flush`/`shutdown` no-op.
- **Empty-string keys are not "missing"** (`is None` check) — `LANGFUSE_PUBLIC_KEY=` in `.env` would
  build a live exporter that 401s. So the chat service decides *enabled* itself (both keys non-blank)
  and passes `tracing_enabled=` explicitly; it never lets the SDK read `LANGFUSE_*` env vars.
- Clients are **singletons per public key** (`resource_manager.py:137-178`). A second client with the
  same key silently reuses the first one's exporter — a trap for tests (R9).
- `timeout` (default 5 s) bounds the exporter; `flush_at`/`flush_interval` default to OTel's 512 / 5 s.

**Alternatives considered**: pinning v3 (dead end, no migration value); letting the SDK read its own
env vars (the empty-string trap, and a second source of configuration beside `Settings`, which
FR-017 forbids).

## R2. Graph-node spans: `node_span`, not Langfuse's `CallbackHandler` — a roadmap deviation

**Decision**: every graph node's observation is opened by the existing `node_span` context manager
in `agent/node_logging.py`, which already wraps the body of all six nodes and emits
`node.started/completed/cancelled/failed`. The Langfuse `CallbackHandler` is **not** used.
`docs/ROADMAP.md` and `README.md` are corrected in the same change.

**Rationale**: The roadmap says "LangGraph nodes are spanned by Langfuse's callback handler". Two
measured facts make that the wrong mechanism here:

1. **It cannot parent anything inside a node.** The handler is a synchronous `BaseCallbackHandler`
   without `run_inline`, so langchain-core runs it in an executor under `copy_context()`
   (`langchain_core/callbacks/manager.py:413-422`); its attach of the node's span never reaches the
   node's coroutine. In the experiment, a span opened inside a node came out as a **sibling** of the
   graph's span, not its child — and with no enclosing turn span, as a separate root trace. FR-003
   (nesting) and FR-005 (retrieval as a branch of its request) would be unsatisfiable: every
   retrieval step, generation and tool call would hang loose under the turn.
2. **It sees nothing this graph does.** Our nodes call the Anthropic SDK directly, not through a
   LangChain chat model, so the handler records chain start/end only — no model, no tokens.
   Everything FR-004 asks for would be manual anyway.

It would also add the `langchain` package (v1) as a new dependency — the handler raises
`ModuleNotFoundError` with `langchain-core` alone (`langchain/CallbackHandler.py:40-85`) — to buy a
node span `node_span` already marks the boundaries of. One hook at the node boundary, in the same
coroutine as the node body, nests everything under it by plain contextvar propagation, which the
experiment confirmed across `asyncio.gather` and `create_task`.

**Alternatives considered**: the handler plus a manual span per node (two spans per node, the
handler's still unparented from the node's children); the handler alone with flat traces (fails
FR-003/FR-005).

## R3. Model calls: manual generation observations, not an Anthropic instrumentor

**Decision**: each of the five Anthropic call sites (`classify_intent.py:255`,
`answer_faq.py:457`, `handle_booking.py:615`, `small_talk.py:115`, `compose_answer.py:378`) wraps its
call in one `generation(...)` context manager from `chat.observability`, which opens an
`as_type="generation"` observation carrying `model`, `input` (system + messages), and
`model_parameters` (max_tokens, temperature where set), and on completion records `output`,
`usage_details={"input": …, "output": …, "cache_read_input_tokens": …}` from the SDK's own
`usage` object, and `completion_start_time` for streamed calls.

**Rationale**: Langfuse recommends OpenLLMetry's `opentelemetry-instrumentation-anthropic`, which does
cover `messages.stream`. Rejected because it is a process-wide monkey-patch, binds to the *global*
provider unless told otherwise, maps attributes on its own terms, and its spans are invisible to the
legacy `mask` hook. Five call sites is a small, fixed number, and the dependency-inversion rule
already puts provider knowledge inside those functions — the right place for the generation too.

Nothing reads `.usage` today. Real responses always carry it; the test fakes do not
(`FakeFinalMessage` models only `stop_reason`, MagicMock `create` responses return a MagicMock), so
the fakes gain a `usage` (research item for tasks, not a runtime branch — see R10).

**Alternatives considered**: the OpenLLMetry instrumentor (above); `@observe` on the call-site
functions (captures every argument, including clients, by default; records a cancellation as ERROR).

## R4. Per-turn "untraced" in one process: a flag-reading sampler

**Decision**: the isolated `TracerProvider` is built with a custom `Sampler` that returns
`Decision.DROP` whenever a module-level `ContextVar[bool]` says the current turn is untraced, and
otherwise delegates to `ParentBased(ALWAYS_ON)`. The flag is set from the request (R6) before the
turn's task is created, so the task's copied context carries it to every span in the turn.

**Rationale**: In the experiment an untraced turn exported **zero** spans, including spans created
from executor threads (they copy the contextvar). Dropped spans are non-recording, and Langfuse's
wrappers check `is_recording()` and do nothing, so an untraced turn pays almost nothing. The flag must
be checked **before** the parent's sampled bit: a seeded `trace_context` (R5) forces the remote
parent to sampled (`client.py:1759`).

Rejected mechanisms, each measured:

- **Not opening a root span** — `@observe` and any stray observation then start their own root
  traces.
- **OTel's `suppress_instrumentation`** — only instrumentations and exporters honour that key; the
  SDK `Tracer` ignores it, so Langfuse spans are unaffected.
- **`should_export_span`** — works, but every span is still built, serialized and masked, and the
  predicate runs per span on the export thread.
- **A second client with `tracing_enabled=False`** — the per-key singleton hands back the first
  client; `get_client()` with two clients returns a disabled one.
- **`sample_rate`** — process-wide, and ignored when a provider is passed in.

## R5. Trace identity and trace-level attributes

**Decision**: the turn's root observation is opened inside `run_pipeline` (the turn's task) with
`trace_context={"trace_id": Langfuse.create_trace_id(seed=turn_id)}`, and `propagate_attributes(...)`
sets, before any child starts:

| Attribute | Value |
|---|---|
| `session_id` | the chat id — so a conversation's turns group together (FR-002) |
| `user_id` | the app session id |
| `trace_name` | `"turn"` |
| `environment` | `"eval"` when the request names an eval run, else the configured environment (default `"development"`) |
| `metadata` | `turn_id`; plus `eval_run_id`, `eval_case_id` for eval turns |

When the trace is actually exported, the service logs **`turn.traced`** with `trace_id`, bound to
the turn like every other turn event.

**Rationale**: A seeded id makes the trace findable from any log line of the turn (they all carry
`turn_id`) without the service holding a mapping. `propagate_attributes` is v4's replacement for
`update_current_trace` and must run before children start (`propagation.py:114`). `environment` is a
first-class filter in the Langfuse UI, which is exactly FR-026's "distinguishable by a filter".
`turn.traced` is the harness's source for a case's trace ids (FR-027): its **absence** is what
"this turn was not traced" looks like, so no value on the record has to mean two things.
`get_current_trace_id()` is *not* usable for that — in a sampled-out turn it still returns a
well-formed id that was never exported.

**Alternatives considered**: returning the trace id in `ChatDoneEvent` (puts an observability id on
the patient-facing wire contract, and is present for untraced turns unless special-cased); the harness
computing `sha256(turn_id)[:16]` itself (duplicates an SDK detail in a package that must not import
the SDK, and says nothing about whether the trace was exported).

## R6. How the harness's choice reaches the chat service

**Decision**: request headers on `POST /chat`, read by the route and turned into a `TraceDirective`:

- `X-VisitDoc-Trace: off` — the turn is not traced. Absent means traced (when the service is).
  Any other value → **422**, so a typo cannot silently trace a run that asked not to be.
- `X-VisitDoc-Eval-Run: <run_id>` and `X-VisitDoc-Eval-Case: <case_id>` — attached to the trace's
  metadata and switching its environment to `eval`. Both or neither; validated against the run-id
  and case-id shapes; → 422 otherwise.

**Rationale**: FR-022 says the choice travels with the run's own requests and never through a
service setting, and FR-024 says it affects exactly the turns that carry it. A header is scoped to one
request by construction. It stays off `ChatRequest`, the patient-facing body, so the published schema
gains no eval-only fields. Anyone can send `X-VisitDoc-Trace: off` for their own turn; the only thing
that buys is that *their* turn is not observed, which is no escalation of anything.

**Alternatives considered**: body fields on `ChatRequest` (pollutes the patient contract); a
session-level flag set by an admin call (a second request whose failure half-applies the choice, and
it scopes to a session rather than to the requests that asked); a service env var (per stack — the
thing FR-022 forbids).

## R7. Masking: export-stage `mask_otel_spans`, sharing one redaction rule with the log

**Decision** (as built, after convergence tasks T071 and T079): the redaction runs in two stages.
When `chat.observability` records a structured value, `shared-logging`'s `redact_value` replaces
values under secret-named keys; strings are recorded as they are. The client is built with
`mask_otel_spans=` a function that replaces any occurrence of a configured secret's value (the three
API keys, the admin secret, the Langfuse secret key, the credentials inside
`DATABASE_URL`/`QDRANT_URL`) in every string attribute of every span in the batch. The first design
applied both rules at export, which meant JSON-decoding serialized payloads — and a patient message
that happened to read as JSON came out re-encoded, so decoding was dropped. The redaction functions that are private in
`shared_logging/logging.py` today (`_redact_value`, `_known_secret_values`, the key pattern) become
its public API, so the log and the trace run **one** rule.

**Rationale**: The legacy `mask` hook sees only `input`, `output` and `metadata` — not
`status_message`, where an error's text lands, and the spec's own edge case is a secret inside an
exception message. `mask_otel_spans` sees every raw attribute of every span at export time, on the
exporter's worker thread (off the event loop — FR-013). Its failure mode is fail-closed: if it raises,
the **batch is dropped** (`span_exporter.py:304-335`), which loses traces and leaks nothing.

**Open risk, closed by test rather than by reasoning**: span *events* are not attributes, and a
patch cannot touch them. If OTel's `record_exception` adds an exception event with the raw message
and stack, the mask cannot reach it. So `chat.observability` records failures itself — level `ERROR`
and a status message built from the redacted error — and the masking test (FR-015) asserts over
exported **attributes and events** that no configured secret value appears anywhere. If the SDK
records exception events regardless, that test fails and the task is to suppress them at the
observation wrapper, not to widen the mask.

**Alternatives considered**: the legacy `mask` (misses status messages); a mask of our own shape
(two rules that drift — the reason `shared-logging` exists).

## R8. Failures, cancellation, and export behaviour

**Decision**:

- The observation wrapper catches `Exception` → level `ERROR`, redacted status message, re-raise; and
  `asyncio.CancelledError` → level `WARNING`, status message `"cancelled"`, re-raise. The turn's root
  observation additionally carries `metadata.turn_outcome` ∈ {`completed`, `failed`, `cancelled`}.
- Export failures stay in the SDK's background thread: the OTLP exporter retries only 408/5xx/connection
  errors, until its 5 s deadline, and drops the batch on 401/429 with an ERROR on the stdlib
  `opentelemetry` logger. A stdlib-logging bridge forwards WARNING+ records from the `langfuse` and
  `opentelemetry` loggers into structlog as `tracing.export_failed` (`logger`, `message`), so a lost
  trace appears in `.run/chat.log` (FR-012).
- Shutdown: the lifespan registers `await asyncio.to_thread(client.shutdown)` on its exit stack;
  `shutdown` is a blocking sync call bounded by the exporter timeout.

**Rationale**: Langfuse has no "cancelled" status, and OTel's `use_span` catches only `Exception`,
so a cancelled context-manager span ends with status UNSET and **looks successful** — contrary to
FR-009. `@observe` goes the other way and calls a cancellation an ERROR. Neither is what
`_settle_the_failure` distinguishes, so the wrapper sets the level itself. `on_end` only enqueues
onto a 2048-span deque; serialization and attribute conversion are the only caller-thread cost, and
a full queue drops with a warning rather than blocking.

**Not verified**: what Langfuse Cloud does past 50k units on Hobby (drop, 429, or block). The design
does not depend on it — every export failure is handled the same way — and the quickstart records
what is observed if the allowance is ever reached.

## R9. Testing without a network

**Decision**: tests build the tracer through the same factory the lifespan uses, with an injected
`span_exporter=InMemorySpanExporter()`, a **unique public key per test** (the singleton, R1), and
always an isolated provider. They `flush()` and read `exporter.get_finished_spans()`, asserting on
`langfuse.observation.type`, `langfuse.observation.level`, `session.id`, `langfuse.trace.name`, and
input/output attributes. With `span_exporter` injected, Langfuse wires no URL or credentials.

The default test configuration has no Langfuse keys, so every existing test runs with tracing off and
exports nothing. A new test pins that (`Settings()` under the test environment → tracing disabled).

**Rationale**: No Langfuse doc covers testing; this mechanism is from source and ran in the experiment.

## R10. Recording the same values in the log and the trace (FR-006)

**Decision**: at each retrieval step, the dict the log event is built from is built **once**, then
given to both the structlog call and the step observation's `output`. A small helper in
`chat.observability` — `record(step, event, payload)` — does both, so the two cannot be computed
separately. The same applies to `intent.classified` and `turn.completed` at the trace's top level
(FR-007), and to `booking.tool_result` on the tool observation.

Usage on a generation is read from the provider's response and never defaulted: the fakes gain a
`usage`, because a generation recorded with zero tokens would be a false statement, not a missing one.

## R11. Tool calls: one observation in `ToolRegistry.dispatch`

**Decision**: `dispatch` opens an `as_type="tool"` observation named after the tool, input = the
arguments, output = the `ToolResult`. A result whose `status` is `unknown` or `unavailable` is level
`WARNING`; `ToolArgumentError`/`UnknownToolError`/a handler exception is `ERROR` (R8).

**Rationale**: every call goes through `dispatch`, including `_read_roster`'s `list_practitioners`
read that builds the booking prompt before the model runs — which `handle_booking._dispatch` does
**not** see. Spanning at the registry covers both with one hook, and extends to any tool added later
without a second edit. (This also settles the question raised on FR-008 after the spec was written:
the roster read is traced.)

## R12. The run's tracing state, and keeping it out of the conditions

**Decision**:

- `service.configured` gains `tracing_enabled: bool`. `RunConditions.from_event` already picks only
  its own fields, so the field never becomes a condition; `test_main.py`'s pinned field set is
  updated to include it.
- `Run` gains `tracing: RunTracing`, a three-value enum — `traced`, `untraced_by_request`,
  `untraced_service_off` — defaulting to `untraced_service_off` so every run stored before this phase
  reads as what it was: nothing could have traced it.
- `CaseRun` gains `traces: dict[str, str]` — patient message id → trace id, from the `turn.traced`
  events in the case's log slice; empty by default.
- `_RestartWatch` stops the run if a restart states a different `tracing_enabled`, and `resume_run`
  refuses when the service's current tracing state disagrees with the run's — so the run-level value
  stays true of every turn it drove. Both exempt an `untraced_by_request` run: it sends every turn
  `off`, so the service's state cannot change what it recorded. The CLI gains `--no-trace`; the Makefile maps `TRACE=0` to it and
  rejects any `TRACE` value other than `0` or `1`.
- `comparison` shows each run's `tracing` beside its identity; `condition_delta`, the band and the
  resume/restart comparisons of `RunConditions` are untouched. A test asserts a traced and an
  untraced run compare with an empty delta.

**Rationale**: A boolean `traced` would say "untraced" for two different reasons. The enum says which,
at no cost. `Run` and `CaseRun` are `extra="forbid"`, so both new fields have defaults — older files
still read (the established pattern: `reply_skipped`, `unplantable`).

## R13. Scoring purity

**Decision**: both purity tests' `_FORBIDDEN` tuples (`tests/scoring/test_purity.py`,
`tests/comparison/test_purity.py`) gain `"langfuse"` and `"opentelemetry"`. The harness takes no
dependency on either.

## R14. Dependency boundary inside the chat service

**Decision**: only `chat.observability` imports `langfuse` or `opentelemetry`. Agent, RAG and API code
call its domain-shaped functions (`turn_trace`, `step`, `generation`, `tool_call`, `record`). A test
walks `services/chat/src` with `ast` and fails on any other importer — the same shape as the harness
purity tests.

**Rationale**: the CLAUDE.md dependency-inversion rule — orchestration depends on domain types, not a
provider's wire format — applies to the observability provider exactly as it does to the model
provider.
