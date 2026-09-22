# Data Model: Tracing with Langfuse (Phase 2d)

No database table changes. The model is in four places: a per-request directive inside the chat
service, the observation tree a turn exports, the tracing settings, and two new fields in the
harness's run artifacts.

---

## 1. `TraceDirective` (chat service, per request)

Parsed from `POST /chat`'s headers by the route, before the turn's task is created. Frozen.

| Field | Type | Source | Rule |
|---|---|---|---|
| `traced` | `bool` | `X-VisitDoc-Trace` | absent → `True`; `off` → `False`; any other value → 422 |
| `eval_run_id` | `str \| None` | `X-VisitDoc-Eval-Run` | a run-id shape (ULID); present iff `eval_case_id` is |
| `eval_case_id` | `str \| None` | `X-VisitDoc-Eval-Case` | a golden-set case-id shape; present iff `eval_run_id` is |

Derived: `environment` = `"eval"` when `eval_run_id` is set, else `Settings.LANGFUSE_ENVIRONMENT`.

**Lifecycle**: set into the untraced `ContextVar` (`traced=False` → flag set) inside the stream
generator, **before** `asyncio.create_task(run_pipeline())`, so the task's copied context carries it
to every span in the turn and to nothing outside it. Never stored, never logged beyond `turn.traced`.

A turn is actually exported only when `Settings` enable tracing **and** `traced` is `True`. The
directive never turns tracing on in a service that has it off.

## 2. The observation tree of one turn

Full attribute contract: [contracts/trace-shape.md](./contracts/trace-shape.md). Shape:

```text
turn                                   span        root; trace_id = create_trace_id(seed=turn_id)
├── classify_intent                    span        node_span
│   └── classify_intent.model          generation
├── answer_faq                         span        node_span
│   └── request[<position>]            span        one per FAQ request, concurrent
│       ├── faq.embed                  span        (skipped on an empty corpus)
│       ├── faq.search                 retriever
│       ├── faq.similarity_gate        span
│       ├── faq.rerank                 span        output = reranking_completed | reranking_unavailable
│       ├── faq.rerank_gate            span
│       ├── faq.verdict                span
│       └── answer_faq.model           generation  only when the request is answered
├── handle_booking                     span        node_span
│   ├── tool:list_practitioners        tool        the roster read, before the loop
│   ├── handle_booking.model[<i>]      generation  one per loop iteration
│   └── tool:<name>                    tool        one per dispatched call, concurrent within an iteration
├── small_talk                         span        node_span
│   └── small_talk.model               generation
├── hand_off                           span        node_span
└── compose_answer                     span        node_span
    └── compose_answer.model           generation  only when a composing call is made
```

**Trace-level attributes** (set by `propagate_attributes` before any child starts): `session_id` =
chat id, `user_id` = a SHA-256 digest of the app session id (never the id: it is the session cookie's value), `trace_name` = `"turn"`, `environment`, `metadata` =
`{turn_id, eval_run_id?, eval_case_id?}`.

**Root observation**: `input` = the patient message(s) the turn answers; `output` = the
`turn.completed` payload; `metadata.intent_classified` = the `intent.classified` payload;
`metadata.turn_outcome` ∈ `completed | failed | cancelled`.

**States of any observation**:

| Outcome | level | status_message |
|---|---|---|
| completed | `DEFAULT` | — |
| a tool result with status `unknown` / `unavailable`, a truncated generation | `WARNING` | the status or stop reason |
| raised an `Exception` | `ERROR` | the redacted error text |
| cancelled (`CancelledError`) | `WARNING` | `"cancelled"` |

A paused/silenced message runs no turn and has no tree (FR-010).

## 3. Settings (chat `core/config.py`)

| Setting | Type | Default | Notes |
|---|---|---|---|
| `LANGFUSE_PUBLIC_KEY` | `str` | `""` | |
| `LANGFUSE_SECRET_KEY` | `str` | `""` | joins `_SECRET_SETTINGS_FIELDS` (FR-016) |
| `LANGFUSE_BASE_URL` | `str` | `"https://cloud.langfuse.com"` | the one place the destination is named (FR-017) |
| `LANGFUSE_ENVIRONMENT` | `str` | `"development"` | lowercase, must not start with `langfuse` — validated at startup |

**Derived**: `tracing_enabled` = both keys non-blank after `strip()`. Blank keys mean off, never a
live exporter that 401s (research R1). Stated in `service.configured` as `tracing_enabled`.

## 4. Harness run artifacts

### `RunTracing` (new enum, `record.py`)

| Value | Meaning |
|---|---|
| `traced` | the run asked for tracing and the service stated `tracing_enabled: true` at the run's start |
| `untraced_by_request` | the run was taken with `--no-trace` / `TRACE=0` |
| `untraced_service_off` | the run asked for tracing but the service stated `tracing_enabled: false`, or the event carried no such field — every run taken before this phase |

`untraced_by_request` wins when both apply: the run's own choice is the fact it controls.

### `Run` (`run.json`) — one new field

| Field | Type | Default |
|---|---|---|
| `tracing` | `RunTracing` | `untraced_service_off` |

Not part of `RunConditions`, so never in a condition delta, a band's sameness check, or the
restart/resume comparison of conditions.

**Invariant**: the value is true of every turn the run drove. Enforced by `_RestartWatch` stopping
the run (`ServiceRestartedError`) when a restart states a different `tracing_enabled`, and by
`resume_run` refusing when the running service's `tracing_enabled` disagrees with the stored value
(for `traced` / `untraced_service_off`; an `untraced_by_request` run resumes regardless, and the
resumed half is also sent untraced).

### `CaseRun` (case file) — one new field

| Field | Type | Default |
|---|---|---|
| `traces` | `dict[str, str]` | `{}` |

Patient message id → trace id, one entry per turn of the case whose `turn.traced` event appears in
the case's log slice. In an untraced run it is empty; in a traced run a turn without an entry ran no
graph (silenced) — nothing else. An export that fails after the root opened still leaves the entry:
`turn.traced` records that the trace was sent, not that Langfuse accepted it. On resume, a re-driven case's
`traces` are those of the turns actually driven, never carried over from the abandoned attempt.

## 5. New log events

| Event | Emitted | Fields |
|---|---|---|
| `turn.traced` | once per exported turn, when its root observation opens | `trace_id` (+ the bound `turn_id`) |
| `tracing.export_failed` | by the stdlib bridge, per WARNING+ record from the SDK/exporter | `logger`, `message` |

Neither changes an existing event; FR-011's "other than log lines reporting tracing's own state"
covers exactly these two.
