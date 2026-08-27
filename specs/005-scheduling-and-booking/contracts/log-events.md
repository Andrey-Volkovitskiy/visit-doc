# Contract: structured log events

**Feature**: `005-scheduling-and-booking` | **Date**: 2026-08-12

Extends spec 004's event set. Every event flows through the service's single processor chain
(`core/logging.py`) and inherits the bound correlation id — `turn_id` on the chat side, propagated to
the scheduler as `x-turn-id` gRPC metadata and re-bound there (research.md #18), so one turn's chat
and scheduler lines join on a single key.

Unchanged from spec 004: `turn.message_received`, `intent.classified`, `turn.groundedness_verdict`,
`turn.error`, `message.persisted`, `critical.dependency_unreachable`.

**Moved**: `turn.completed` is no longer emitted by `answer_faq`. It is emitted **once per turn, by
`compose_answer` only** — the join node that runs on every path and is the only place a turn's
user-visible reply is known to exist (research.md #24). Its existing fields are unchanged; it gains
`answer_source`.

---

## Node lifecycle (chat service)

Every graph node — `classify_intent`, `answer_faq`, `handle_booking`, `compose_answer` — emits
exactly one lifecycle pair. `node_span()` binds `node` into `structlog.contextvars` for the node's
duration, so **every** event emitted inside a node carries its name without any call site passing it
(research.md #24). Under the fan-out, `answer_faq` and `handle_booking` lines interleave; `node` plus
`turn_id` is what separates them.

| Event | Level | Fields | When |
|---|---|---|---|
| `node.started` | info | `node` | Entering the node. |
| `node.completed` | info | `node`, `duration_ms`, `result` (per-node, below) | The node returned normally. |
| `node.failed` | error | `node`, `duration_ms`, `error_type`, `error_detail` | The node raised. Distinct from `turn.error`, which describes a turn that died — a branch can fail while the turn still answers. |
| `node.cancelled` | info | `node`, `duration_ms` | The turn was superseded mid-node (spec 003 FR-015). Not a failure; no reply is recorded either way. |

### `result` payload per node

| Node | `result` fields |
|---|---|
| `classify_intent` | `intents`, `specialists` (the node names launched), `merge_required` |
| `answer_faq` | `grounded`, `abstained`, `citation_count`, `answer_chars`, `mode` (`streamed` / `collected`) |
| `handle_booking` | `outcome` (`BookingOutcome`), `appointment_id` (when booked), `iterations`, `tool_calls`, `mode` |
| `compose_answer` | `answer_source`, `merged` (false on the single-specialist no-op path), `grounded`, `booking_outcome`, `citation_count` |

`classify_intent`'s `result` deliberately does not repeat the labels beyond `intents` — the
classification itself is already reported by spec 004's `intent.classified`, unchanged. What is new
here is the *routing decision* it produces.

---

## Intermediate phases (chat service)

Emitted inside a node, inheriting its bound `node` and the turn's `turn_id`.

### `answer_faq`

| Event | Level | Fields | When |
|---|---|---|---|
| `faq.retrieved` | info | `chunk_count`, `top_score`, `entry_ids` | Retrieval returned, before the groundedness gate. Makes the gate's verdict explicable rather than a bare boolean. |
| `turn.groundedness_verdict` | info | unchanged (spec 004) | The gate decided. |

### `handle_booking`

| Event | Level | Fields | When |
|---|---|---|---|
| `booking.tool_called` | info | `tool_name`, `iteration`, `arguments` | About to dispatch a `tool_use` block. |
| `booking.tool_result` | info | `tool_name`, `iteration`, `status`, `reason` (refusals only), `duration_ms` | A handler returned. `status` is the result's own field, never inferred from prose. |
| `booking.tool_failed` | error | `tool_name`, `iteration`, `error_type`, `error_detail` | A handler raised rather than returning a result. The loop reports the failure to the model as a `tool_result` and continues; the node itself does not fail. |
| `booking.loop_exhausted` | warning | `iterations` | The 6-iteration bound was hit; the turn ends with a plain failure reply. |

### `compose_answer`

No intermediate events — it is one generation call, fully described by its `node.completed` result
and the `turn.completed` it emits.

---

## Scheduling client (chat service)

Below the tools, so these carry whichever node's name was bound when the call was made.

| Event | Level | Fields | When |
|---|---|---|---|
| `scheduling.call` | info | `method`, `attempt`, `status`, `duration_ms` | One gRPC attempt finished, successful or not. A retry produces two lines. |
| `scheduling.unavailable` | error | `method`, `attempts`, `error_detail` | The 2s/2-attempt budget was exhausted (FR-047). Accompanied by `critical.dependency_unreachable` with `dependency="scheduler"`, matching the existing `qdrant`/`anthropic_api` pattern. |

## Chat and provisioning lifecycle (chat service)

Outside any node — emitted by the `/chats` routes.

| Event | Level | Fields | When |
|---|---|---|---|
| `chat.created` | info | `chat_id`, `patient_id` (or null), `provisioning_ok` | A chat was created. `provisioning_ok: false` is the FR-044 degraded path. |
| `chat.deleted` | info | `chat_id`, `patient_existed`, `appointments_deleted`, `turn_cancelled` | The cross-service deletion completed (research.md #11). |
| `patient.provisioned` | info | `chat_id`, `patient_id`, `created` | `EnsureSessionProvisioned` succeeded. `created: false` means an earlier attempt had already made it (FR-045, US2-4). |

---

## Scheduler service — new

The scheduler's `core/logging.py` mirrors chat's (one chain, wrapping `get_logger()`, the two
secret-constant lists, with `DATABASE_URL` in the URL-secret list from day one).

| Event | Level | Fields | When |
|---|---|---|---|
| `rpc.received` | info | `method`, `session_id` | Server interceptor, after binding `x-turn-id`. |
| `rpc.completed` | info | `method`, `status`, `duration_ms` | Server interceptor, on the way out. |
| `booking.attempted` | info | `patient_id`, `practitioner_id`, `starts_at`, `idempotency_key` | Before validation. |
| `booking.created` | info | `appointment_id`, `starts_at`, `ends_at` | Row inserted. |
| `booking.replayed` | info | `appointment_id`, `idempotency_key` | The key matched an earlier attempt **and the request matched the stored appointment**; the original was returned (FR-051). |
| `booking.key_mismatch` | error | `idempotency_key`, `stored_appointment_id`, `mismatched_fields` | A used key arrived with a different patient, practitioner, or start time (FR-063). Always a caller defect — the key derivation is broken — never a normal outcome, so it is logged at error level and answered with `INVALID_ARGUMENT`, not a `BookingFailure`. |
| `booking.refused` | info | `reason`, `starts_at` | An evaluated refusal — a normal outcome, not an error. |
| `booking.race_lost` | warning | `reason`, `starts_at` | An exclusion constraint rejected the insert, i.e. two attempts genuinely raced (SC-002). |
| `availability.computed` | info | `practitioner_id`, `from_date`, `to_date`, `slot_count`, `truncated` | A `CheckAvailability` call resolved. |
| `name.allocated` | info | `entity` (`patient`/`practitioner`), `full_name`, `pass_number` | `pass_number > 1` is the exhausted-pool suffix path (FR-013). |
| `name.collision_retried` | warning | `entity`, `attempt` | A concurrent creation took the name first; allocation re-ran (research.md #12). |
| `patient.deleted` | info | `chat_id`, `patient_id`, `appointments_deleted` | Cascade completed. |

---

## Privacy

Spec 004's rule stands: `intent.classified` carries labels only, never message text. The new events
follow it — `booking.tool_called` logs tool arguments (practitioner ids and times, chosen by the
model), never the patient's raw message, and no `node.completed` result carries reply text, only its
length. `turn.message_received`'s existing raw-text content is untouched by this feature.
