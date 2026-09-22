# Contract: the shape of a turn's trace

What `chat.observability` exports for one turn. Tests assert against exported OTel spans read
from an `InMemorySpanExporter` (research R9); the attribute names below are the ones Langfuse's
span processor writes (`langfuse.observation.type`, `langfuse.observation.level`,
`langfuse.observation.input`/`output`/`metadata.*`, `session.id`, `user.id`,
`langfuse.trace.name`, `langfuse.environment`).

## C1. One trace per turn

- Every turn whose request is traced and whose service has tracing on exports exactly one trace; every
  exported span belongs to that trace. (FR-001)
- `trace_id == Langfuse.create_trace_id(seed=turn_id)`. (R5)
- A silenced (paused or escalated) message exports no span. (FR-010)
- A turn whose request carries `X-VisitDoc-Trace: off` exports no span. (FR-022)

## C2. Trace-level attributes

Set on every span of the trace:

| Attribute | Value |
|---|---|
| `session.id` | chat id |
| `user.id` | `sha256(app session id)[:32]` — a digest, never the id: the id is the session cookie's value, a bearer credential |
| `langfuse.trace.name` | `turn` |
| `langfuse.environment` | `eval` for eval turns, else `LANGFUSE_ENVIRONMENT` |
| metadata `turn_id` | the turn id |
| metadata `eval_run_id`, `eval_case_id` | present exactly for eval turns |

## C3. Observations, by name

| Name | Type | Parent | input | output / other |
|---|---|---|---|---|
| `turn` | span | — | the patient message text(s) answered | the `turn.completed` payload; metadata `intent_classified` = the `intent.classified` payload, `turn_outcome` |
| `<node>` for each of `classify_intent`, `answer_faq`, `handle_booking`, `small_talk`, `hand_off`, `compose_answer` | span | `turn` | — | the payload `node.completed` carries as `result` |
| `request[<position>]` | span | `answer_faq` | the request's `query` and `text` | its `RequestOutcome` |
| `faq.embed` | span | `request[…]` | the query | model, vector dimension |
| `faq.search` | retriever | `request[…]` | query, pool size | the pool as returned: `pool_returned` and each candidate's `entry_id`, `chunk_index`, `similarity_score` |
| `faq.similarity_gate` | span | `request[…]` | — | = `faq.similarity_gate` fields; metadata `retrieval_completed` = `faq.retrieval_completed` fields |
| `faq.rerank` | span | `request[…]` | — | = `faq.reranking_completed` fields, or `faq.reranking_unavailable`'s at level WARNING |
| `faq.rerank_gate` | span | `request[…]` | — | = `faq.rerank_gate` fields |
| `faq.verdict` | span | `request[…]` | — | = `faq.verdict` fields |
| `<node>.model` (`classify_intent.model`, `answer_faq.model`, `small_talk.model`, `compose_answer.model`) and `handle_booking.model[<iteration>]` | generation | its node, or `request[…]` for `answer_faq.model` | system prompt + messages (+ tool definitions for booking) | model, `model_parameters`, output content, `usage_details` {`input`, `output`, `cache_read_input_tokens` when reported}, `completion_start_time` for streamed calls |
| `tool:<name>` | tool | `handle_booking` | the arguments | the `ToolResult` |

"= `<event>` fields" means the value is **the same dict** the log event was emitted with (R10), so
equality between the trace and `.run/chat.log` holds by construction. A test asserts it for each of
the six FAQ events. `faq.retrieval_completed` sits on the similarity gate, not the search, because it
is emitted only once the gate has run: each candidate's `considered` flag is the gate's decision
(`answer_faq.py:581-590`), and by then the search step has closed. (FR-006, SC-002)

Steps that do not run export no span: `faq.embed`/`faq.search`/`faq.similarity_gate` on an empty
corpus, `faq.rerank` onwards when the similarity gate kept nothing, `answer_faq.model` for an
abstention, `compose_answer.model` for a single-specialist or all-abstained turn.

## C4. Levels and outcomes

| Situation | level | status_message |
|---|---|---|
| normal completion | `DEFAULT` | — |
| tool result status `unknown` or `unavailable` | `WARNING` | the status |
| generation stopped by `max_tokens` | `WARNING` | `max_tokens` |
| rerank unavailable (fallback path) | `WARNING` | the `RerankFailureReason` |
| an `Exception` raised through the observation | `ERROR` | redacted `str(exc)`, prefixed with the type name |
| `asyncio.CancelledError` through the observation | `WARNING` | `cancelled` |

The `turn` root's `metadata.turn_outcome` is `completed`, `failed` (the pipeline raised, the path
`_settle_the_failure` handles) or `cancelled` (superseded by a staff post). (FR-009)

## C5. Masking

Masking runs `shared_logging`'s redaction in two stages. When `chat.observability` records a
structured value (a dict, list, tuple or pydantic model) as an input, output or metadata, values
under secret-named keys are replaced; a string is recorded as it is, so a patient message that reads
as JSON is never re-encoded. Before export, every occurrence of a configured secret value — the
Anthropic, Voyage and Langfuse secret keys, `ADMIN_SECRET`, and the credential parts of
`DATABASE_URL`/`QDRANT_URL` — becomes `***REDACTED***` in every string attribute of every span.
`usage_details` and `model_parameters` are not key-name redacted (`cache_read_input_tokens` would
match). Patient text, prompts, model output and names are not masked. If the mask raises, the batch
is dropped, never exported unmasked. (FR-014, FR-015)

Test: a turn whose generation raises an error containing each secret value, and whose tool arguments
and outputs contain each one, exports **no attribute and no span event** containing any of them.

## C6. Non-interference

- Tracing on vs off: the same scripted turn (fixed fakes) yields identical stream events, stored
  message, request outcomes, escalation and log events apart from `turn.traced` and
  `tracing.export_failed`. (FR-011)
- An exporter that raises on every export, or blocks for longer than the turn: the turn's stream and
  records are unchanged and its `done` is not delayed by it; `tracing.export_failed` is logged.
  (FR-012, FR-013)
