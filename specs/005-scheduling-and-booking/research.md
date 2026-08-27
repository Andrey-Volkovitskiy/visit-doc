# Phase 0 Research: Scheduling Service and End-to-End Booking (Phase 1c)

**Feature**: `005-scheduling-and-booking` | **Date**: 2026-08-12

Every NEEDS CLARIFICATION raised while filling plan.md's Technical Context is resolved below. Two
decisions (#1, #2) were taken by the user during planning and each deviates from `docs/ROADMAP.md`
as written; both carry a Complexity Tracking row in plan.md and a documentation-amendment task.

---

## #1 — The capability seam is a tool registry, not an MCP server

**Decision**: Agent capabilities are exposed through an in-process **tool registry** —
`services/chat/src/chat/agent/tools/`, a set of `(name, description, JSON schema, handler)` records
passed straight to the Anthropic Messages API's `tools=` parameter. No MCP protocol, no MCP server,
no MCP client in this phase.

**Rationale**: Constitution Principle IV requires that "every capability the agent can invoke MUST
be exposed behind a tool-call interface, so agent reasoning stays decoupled from how each capability
is implemented" — a registry satisfies that in full. The booking node knows only tool names and
JSON schemas; that a `book_appointment` call becomes a gRPC round trip to a separate service is
entirely the handler's business, and swapping the handler for an MCP client later changes no agent
code. MCP's added value over this is *cross-process reuse* (a third-party client driving the same
tools), which nothing in this phase consumes. Chosen by the user during planning.

**Consequence**: `docs/ROADMAP.md`'s Phase 1c bullet "MCP tool servers — `search_faq`,
`check_availability`, `book_appointment`" no longer describes what gets built. Per Constitution
Principle VI (documentation updated in the same change that makes it stale) and the Governance
clause on amendments, that bullet MUST be rewritten in this feature's implementation to describe the
tool-registry seam and to state that the MCP transport moves to a later phase. This is a deviation
from a binding ROADMAP statement and is recorded in plan.md's Complexity Tracking.

**Alternatives considered**:
- *FastMCP server mounted in the chat process, agent connects as an MCP client over loopback
  streamable-HTTP*: matches the ROADMAP literally and demonstrates MCP, but adds two dependencies,
  an extra network hop inside a single process, and JSON-RPC error plumbing between the agent and
  handlers that already live in the same address space — machinery with no second consumer.
- *MCP server hosted by `scheduler`, chat connects to it*: would make the gRPC seam redundant for
  exactly the calls the ROADMAP introduces gRPC for, collapsing the one deliberate service contract
  this phase exists to build. Rejected outright.
- *Anthropic's remote MCP connector*: requires a publicly reachable server URL; not viable for a
  local-only phase.

**Note**: `search_faq` does **not** become a registry tool. Decision #2 keeps FAQ answering on its
own graph node with its existing retrieval → groundedness → generate pipeline (spec FR-034: the FAQ
path is unchanged), so nothing needs to invoke retrieval as a tool.

---

## #2 — Mixed-intent turns fan out to parallel specialists and merge

**Decision**: `classify_intent` becomes a real router. It selects the specialist node(s) implied by
the classified intents and LangGraph runs the selected ones **concurrently**; a `compose_answer`
node then generates the single user-visible reply from their results.

```
                       ┌──> answer_faq ─────┐
START ──> classify_intent                    ├──> compose_answer ──> END
                       └──> handle_booking ─┘
```

Selection rule (from `IntentLabel`, spec 004):

| Classified intents | Specialists launched |
|---|---|
| contains `booking` only | `handle_booking` |
| contains `faq_question` only | `answer_faq` |
| contains both | both, concurrently |
| `call_staff` / `unknown` / `classification_failed` only | `answer_faq` (today's default path; escalation is Phase 1d) |

**Rationale**: Chosen by the user during planning, in preference to a strict single-path router. A
patient who writes "what should I bring, and can I book Friday?" gets both halves answered in one
coherent reply instead of a visibly partial one. It also makes the graph's branching real, which is
the thing LangGraph was adopted for.

**Consequence**: This pulls forward work the ROADMAP assigns to Phase 1d ("Parallel specialist nodes
with a merge step for mixed-intent messages") and contradicts spec.md's own Assumption that
"concurrent specialist handling with a merge step is Phase 1d". Both `docs/ROADMAP.md` (1c and 1d
bullets) and that spec.md assumption MUST be amended in this feature's implementation. Recorded in
plan.md's Complexity Tracking as a scope-addition deviation from Principle I.

**Single-specialist turns must not pay for the merge.** A second generation call on every FAQ answer
would break FR-034 ("answered by the existing grounded FAQ path, unchanged") and add latency to the
common case. So the router also computes `merge_required = len(selected) > 1` and writes it into
graph state:

- `merge_required is False` — the sole specialist streams its own tokens through the stream writer
  and emits the terminal `ChatDoneEvent` itself, exactly as `answer_faq` does today. `compose_answer`
  still runs (both specialists edge into it) but detects a single result and returns without
  emitting anything.
- `merge_required is True` — each specialist runs in *collect* mode: it produces a result object in
  state and emits **no** events. `compose_answer` then makes one Sonnet-5 call that composes the
  final reply from both results, streaming its tokens and emitting the terminal event.

**Groundedness and truthfulness survive the merge** (Constitution V, FR-028):
- Citations are still derived structurally from the chunks `answer_faq` actually retrieved, carried
  through state into the terminal event — never re-reported by the composing model.
- If `answer_faq` abstained, its result carries the abstention, and `compose_answer`'s prompt
  requires that half to be reported as "no confident answer" rather than filled in from model
  knowledge.
- The booking result carries an explicit machine-readable outcome (`booked` / `refused` /
  `unavailable` / `awaiting_confirmation`) alongside the booking specialist's own text, and
  `compose_answer` is instructed to preserve that claim exactly. A test asserts a failed booking is
  never composed into a success.

**Concurrent state writes**: the two specialists write *disjoint* state keys (`faq_result`,
`booking_result`), so no LangGraph channel reducer is needed — `InvalidUpdateError` only fires when
concurrent branches write the same key.

**Alternatives considered**:
- *Strict single-path routing* (booking wins, FAQ half deferred to a follow-up question): simplest,
  keeps 1d's merge in 1d, but produces a partial answer to a perfectly ordinary sentence.
- *FAQ wins on mixed*: silently drops the actionable half — the worse of the two failure modes.
- *Always merge, including single-specialist turns*: uniform, but doubles the generation cost of
  every FAQ answer and violates FR-034's "unchanged".

---

## #3 — The booking specialist is a bounded tool-use loop, not a hand-written state machine

**Decision**: `handle_booking` runs a multi-step Anthropic tool-use loop on Sonnet 5: send the
conversation plus the tool definitions, execute any `tool_use` blocks the model returns, append the
`tool_result` blocks, and repeat until the model returns a turn with no tool calls. Bounded to 6
iterations per turn; hitting the bound ends the loop with a plain "I couldn't complete that" reply
rather than looping forever.

**Rationale**: A booking conversation is genuinely open-ended — the patient may name a specialty, a
practitioner, a day, a vague time, or change their mind mid-sentence, and the number of capability
calls needed varies per turn (list practitioners → check availability → book). Encoding that as an
explicit state machine means re-deriving intent from free text at every state, which is precisely
what the model is better at. The loop keeps all scheduling knowledge in tool handlers and all
dialogue policy in the prompt.

**Confirmation before booking (FR-027)** is a prompt-level rule reinforced by tool design: the
`book_appointment` tool's description states it creates a real, uncancellable appointment and must
only be called after the patient has confirmed a specific practitioner and start time. The
`awaiting_confirmation` outcome exists so a turn that ends without a booking is explicitly
distinguishable from one that failed.

**Alternatives considered**: a deterministic slot-filling state machine (rejected: brittle against
natural phrasing, and it would put dialogue policy in Python where it can't be evaluated by Phase
2's harness); a single-shot tool call with no loop (rejected: cannot chain list → availability →
book within one turn, which acceptance scenario US1-1 requires).

---

## #4 — Terminal event shape gains an answer source

**Decision**: `ChatDoneEvent` gains `answer_source: Literal["faq", "booking", "merged"]`, and
`grounded` becomes `bool | None` (None when no FAQ specialist ran). `message` keeps its existing
meaning — set only when the FAQ half abstained and there is no streamed text to show.

**Rationale**: The frontend currently decides what to render with `event.grounded ? accumulated :
event.message`. A booking reply is streamed text that is not RAG-grounded, so under today's shape it
would have to lie (`grounded=true`) or be rendered as an abstention (`grounded=false`, wrong text).
The clean rule is "render `message` if present, otherwise the accumulated tokens", with
`answer_source` carrying the provenance for logging, persistence, and Phase 2's eval harness.

**Persistence**: `Message.grounded` stays NULL for a booking-only reply and carries the FAQ half's
verdict for a merged one; `Message.citations` is unchanged. No new column — `answer_source` is
derivable from the stored `grounded`/`citations` pair and is not worth a migration.

---

## #5 — Local wall-clock times end to end: naive timestamps, ISO-8601 strings on the wire

**Decision**:
- **Scheduler database**: `TIMESTAMP WITHOUT TIME ZONE` for appointment start/end, `TIME WITHOUT
  TIME ZONE` for working-range bounds. Overlap ranges are `tsrange`, not `tstzrange`.
- **Python**: naive `datetime` / `time` objects throughout. Any value carrying `tzinfo` is rejected
  at the boundary (Pydantic validator on every DTO, explicit check on gRPC ingress).
- **gRPC wire format**: local date-times travel as **ISO-8601 strings with no offset and no `Z`**
  (`"2026-08-14T09:00:00"`), validated on both ends.

**Rationale**: FR-033/FR-043 forbid storing or converting a timezone anywhere. `TIMESTAMPTZ` would
silently rotate every value through the server's `TimeZone` setting — the exact conversion the spec
forbids. On the wire, `google.protobuf.Timestamp` is by definition an absolute instant (seconds
since the Unix epoch, UTC); putting a local wall-clock time in one asserts a zone that does not
exist. A plain ISO-8601 local string is unambiguous precisely *because* it carries no offset, and it
stays readable in logs and `grpcurl` output.

**Alternatives considered**: a structured `LocalDateTime { int32 year; ... }` proto message —
type-safe and impossible to misparse, but verbose at every construction site and no more correct
than a validated string; `int64` minutes-since-an-arbitrary-epoch — compact, unreadable, and invites
exactly the epoch confusion this decision exists to avoid.

---

## #6 — Overlap is a database constraint; schedule bounds and grid are creation-time checks

**Decision**: Two PostgreSQL exclusion constraints on `appointments` (requires the `btree_gist`
extension):

```sql
EXCLUDE USING gist (patient_id      WITH =, tsrange(starts_at, ends_at) WITH &&)
EXCLUDE USING gist (practitioner_id WITH =, tsrange(starts_at, ends_at) WITH &&)
```

The "inside a working range" (FR-018) and "on the slot grid" (FR-019) rules are enforced in
application code inside the booking transaction, **not** by a constraint or trigger.

**Rationale**: FR-016/FR-017 demand datastore-level enforcement, and an exclusion constraint is the
only thing that survives two concurrent transactions racing for the same slot (SC-002) — Constitution
Principle III names this case explicitly. The schedule-bounds and grid rules are deliberately *not*
invariants: FR-022 grandfathers existing appointments when a practitioner's schedule or duration is
edited, so a stored appointment may legitimately violate both rules forever after. A CHECK or trigger
that enforced them continuously would reject the very edit FR-022 requires to succeed. They are
therefore predicates evaluated once, at creation, against the practitioner's settings *as of that
moment* — which is exactly what FR-018's wording says.

**Grandfathered rows still block overlaps** (FR-023) for free: the exclusion constraints know nothing
about schedules, only about intervals. The availability walk drops the slots they overlap for exactly
the same reason — its overlap filter also sees plain intervals. What the walk excludes separately is
only their *own* now-off-schedule time, and even that is not a filter: the grid is generated from the
practitioner's current ranges, so that time is never a candidate to begin with.

---

## #7 — Working-range non-overlap uses a custom `timerange` type

**Decision**: The initial scheduler migration creates a range type over `time`:

```sql
CREATE TYPE timerange AS RANGE (subtype = time);
ALTER TABLE working_ranges ADD CONSTRAINT working_ranges_no_overlap
  EXCLUDE USING gist (practitioner_id WITH =, weekday WITH =,
                      timerange(start_time, end_time) WITH &&);
```

**Rationale**: FR-006 requires a practitioner's ranges on one weekday to be non-overlapping. PostgreSQL
ships `tsrange`/`daterange`/`int4range` but no range type over bare `time`; `CREATE TYPE ... AS RANGE`
is the supported, one-line way to get one, and it keeps the column readable as `TIME` in every query
and log line.

**Alternatives considered**: storing `start_minute`/`end_minute` as `int` and using `int4range` — no
new type needed, but every read, every log line, and every fixture then deals in minutes-from-midnight,
and the grid arithmetic that already exists gains a second unit system to convert between. Rejected as
a readability loss for no correctness gain.

---

## #8 — The idempotency key is derived, not random

**Decision**: The chat service derives each booking attempt's key deterministically:

```python
uuid5(_BOOKING_NAMESPACE, f"{patient_id}|{practitioner_id}|{starts_at.isoformat()}")
```

It is stored on `appointments.idempotency_key` with a UNIQUE constraint. `BookAppointment` first
looks the key up:

| Lookup result | Behavior |
|---|---|
| miss | validate and insert; the key is recorded **only** on success (FR-064) |
| hit, and the stored row's patient/practitioner/`starts_at` match the request | return that appointment as a **success**, `idempotent_replay = true` (FR-051) |
| hit, and any of the three differ | **reject** with `INVALID_ARGUMENT`; return neither the stored appointment nor a new one (FR-063) |

**The mismatch case is a caller bug, not a domain refusal** (research.md #9's split). Because the key
is derived from exactly the three fields that must match, a mismatch is *impossible* from a correct
caller — it can only mean the deriving code broke, or a non-chat client invented its own key. That is
why it is a status code rather than a `BookingFailure`: there is nothing for the patient to choose
differently, and no plain-language reason to give them beyond "that didn't go through, nothing was
booked". The chat client treats `INVALID_ARGUMENT` as non-retryable, the tool returns its
`unavailable` result (which states plainly that nothing was created, so FR-028 holds), and the
scheduler logs `booking.key_mismatch` at error level so the defect is visible rather than absorbed.

**Rejecting rather than replaying matters more than it looks.** Under a trust-the-key rule, an
attempt carrying a used key but a different `starts_at` would receive the *original* appointment as a
success — and the assistant would then confirm a time the patient never chose. That is a false
confirmation, the exact failure FR-028 and SC-008 exist to prevent, reached without the model
hallucinating anything. This is the same "verify the request matches the key" rule mainstream payment
APIs apply, and for the same reason.

**Scope and lifetime** (FR-064): the key is globally scoped — one UNIQUE column, not scoped per
session or per patient — which is safe because the derivation already embeds the patient's ULID, so
two sessions cannot collide. It lives exactly as long as its appointment: the row's deletion (patient
or practitioner cascade) takes the key with it, and no stale-replay hazard follows, because a repeat
of that booking would then fail on the missing patient or practitioner anyway. No TTL, no reuse
window, no cleanup job.

**Rationale**: FR-051 requires a caller-supplied key, and a randomly generated one would satisfy the
letter of it — but only protects the single transport-level retry that generated it. A derived key
additionally collapses the case the spec's clarification actually worries about: a lost confirmation
where the *model*, several turns later, re-issues the same booking. Identical parameters always
produce an identical key, so "book Dr. X at 09:00 on Tuesday" is idempotent no matter how many
attempts, tool calls, or retries reach the service. It also makes the retry in decision #9 safe by
construction rather than by discipline.

**Why a lookup and not just the UNIQUE constraint's error**: returning the *original* appointment is
required (FR-051), so the row has to be read either way; doing it first keeps the happy path free of
an exception round trip, and it is where the FR-063 match check naturally sits. The UNIQUE constraint
remains as the race guard — two simultaneous inserts with one key mean the loser catches
`IntegrityError`, re-reads, and re-runs the same match check.

**Alternatives considered**: a random UUID per tool call, recorded in graph state (protects the
transport retry only, and makes FR-063's match check meaningless because nothing ties the key to the
request); trusting the key without comparing the request (one less read, at the cost of the false
confirmation described above); an idempotency table separate from `appointments` (an extra row and an
extra expiry/cleanup story for information the appointment row already holds, and FR-064's
"lives as long as the appointment" answer falls out for free without one).

---

## #9 — Failure taxonomy: domain outcomes are data, transport failures are status codes

**Decision**:
- A booking the service *evaluated and refused* is a **successful RPC** carrying a typed failure:
  `BookAppointmentResponse` is a `oneof { Appointment appointment; BookingFailure failure }`, with
  `BookingFailure.reason` an enum — `PRACTITIONER_BUSY`, `PATIENT_BUSY`, `OUTSIDE_SCHEDULE`,
  `OFF_GRID`, `IN_PAST`, `BEYOND_HORIZON`, `PRACTITIONER_NOT_FOUND`, `PATIENT_NOT_FOUND`.
- Those eight are the whole set (spec FR-065), and an attempt breaking several rules reports the
  first that holds in a fixed precedence: not-found → in-past → beyond-horizon → outside-schedule →
  off-grid → busy. The validator's evaluation order *is* the precedence, which is why the two busy
  reasons sit last: they are the only ones the database decides, at insert.
- Only genuine transport/infrastructure failures use gRPC status codes (`UNAVAILABLE`,
  `DEADLINE_EXCEEDED`, `INTERNAL`).
- Per call: **2-second deadline, at most 2 attempts** (one retry), retried **only** on `UNAVAILABLE`
  and `DEADLINE_EXCEEDED` — never on a status that implies the server processed the request.

**Rationale**: FR-029 requires the assistant to explain *why* a booking was refused, and FR-046
requires a different, explicitly-degraded reply when scheduling is unreachable. Collapsing both into
a gRPC error status would force the chat service to reverse-engineer the difference from a status
code and a string. Modelling the refusal as data makes the tool handler's mapping total and testable,
and keeps "the service is down" as the only thing that produces an exception. The 2s/2-attempt budget
is FR-047 verbatim; worst case is ~4s plus overhead, inside SC-013's 5-second promise.

**Retry safety**: booking is the only write among the retried calls, and decision #8's derived key
makes a duplicate attempt return the original appointment rather than a second one.

---

## #10 — One idempotent provisioning RPC, called with the chat row already committed

**Decision**: A single `EnsureSessionProvisioned(session_id, chat_id)` RPC creates the patient for
`chat_id` if none exists, **and** creates one default practitioner if the session has none. It is
idempotent on both counts and returns the session's patient and practitioners.

Call sites and ordering:
1. **Chat creation** (`POST /chats`, including a first visit): the `Chat` row is inserted and
   committed **first**, then the RPC runs under the standard budget. Success writes `patient_id` back
   onto the chat row; any failure is logged and the chat stays with `patient_id = NULL`.
2. **Any later turn** whose chat still has `patient_id IS NULL`: the same RPC is attempted once,
   before the graph runs (FR-045). Success back-fills the name; failure leaves the chat unnamed and
   the booking tools degraded.

**Rationale**: FR-044 makes chat creation the priority — committing the chat row before any
cross-service call means an unreachable scheduler cannot fail it, and FR-042's "one practitioner on
first arrival" collapses into the same round trip. Idempotency on `patients.chat_id` (UNIQUE) is
what makes FR-045's later retry safe (US2-4: no duplicates). Guarding practitioner creation on "the
session has none" is what keeps US4's second, third, … chat from seeding a practitioner each
(FR-042 is per-session, not per-chat).

**Alternatives considered**: separate `CreatePatient`/`CreatePractitioner` RPCs orchestrated by the
chat service (two round trips, two failure points, and the "only if the session has none" check
becomes a read-then-write race across a service boundary); a fire-and-forget background task
(chat creation returns before the name exists, so the very first list render is unnamed even when
scheduling is perfectly healthy).

---

## #11 — Cross-service deletion runs scheduler-first

**Decision**: `DELETE /chats/{chat_id}` calls `DeletePatientForChat` on the scheduler **before**
deleting the local chat row. If that call fails, the whole deletion fails and reports an error; the
chat is not removed locally.

**Rationale**: FR-055 states an appointment must never outlive its patient. Of the two orderings,
only this one has a benign failure mode:
- *Scheduler first*: a crash between the two steps leaves a chat whose `patient_id` points at a
  deleted patient. The next interaction's provisioning path (decision #10) simply creates a fresh
  patient — recoverable, and nothing outlives anything.
- *Local first*: a crash leaves a patient and its appointments alive in the scheduler with no chat
  and no way to reach them — exactly the orphan FR-055 forbids.

This is the same rule the project already applies to Qdrant and Postgres (`.claude/CLAUDE.md`:
"deindex from Qdrant *before* deleting the Postgres row"): the derived/dependent side goes first,
so the source of truth is the last thing to disappear.

**Deleting while scheduling is down**: the deletion is refused with an explicit error. FR-044's
never-block guarantee covers chat *creation* and FAQ answering, not deletion, and the alternative
(deleting locally anyway) is the orphan case above.

**In-flight turns** (FR-055, spec clarification 2026-08-12): deletion first cancels the chat's
registered generation task via `generation_registry`, so the streaming reply is abandoned with
nothing recorded. A reply that wins the race and inserts anyway fails on `messages.chat_id`'s foreign
key, which the insert path already tolerates.

---

## #12 — Name allocation: deterministic pool walk, UNIQUE constraint, retry on conflict

**Decision**: `next_available_name(session_id, pool)` reads the session's existing names, then walks
the pool in order looking for the first unused entry; once every entry is taken it walks again with
`" 2"` appended, then `" 3"`, and so on. Insert is guarded by `UNIQUE (session_id, full_name)`; an
`IntegrityError` re-runs the allocation and retries (bounded).

**Rationale**: FR-011/FR-013 demand full determinism — the same creation sequence in a fresh session
must produce the same names (SC-007) — which rules out "pick a random unused name" and any
count-based shortcut (a session that renamed a patient would skip or collide). Read-then-insert races
are handled by the constraint rather than by locking the session, keeping concurrent chat creation
in one session correct without serializing it.

**Pool location**: `services/scheduler/src/scheduler/domain/name_pools.py`, as two module-level
tuples (100 writers, 20 physicians). The scheduler owns patients and practitioners, so it owns their
naming; no cross-service transfer of the pools is needed.

---

## #13 — "Most recently active chat" is decided by the server's ordering

**Decision**: `GET /chats` returns the session's chats already ordered by the FR-056 rule, and the
frontend simply opens the first one:

```sql
ORDER BY (last_message_at IS NULL), last_message_at DESC, created_at DESC
```

where `last_message_at` is `MAX(messages.created_at)` per chat, via a `LEFT JOIN` + `GROUP BY`.

**Rationale**: FR-056's fallback is subtler than `COALESCE(last_message_at, created_at)`: a chat with
messages always outranks one with none, even if the empty chat was created more recently. Sorting
NULLs to the back first, then by recency within each group, expresses that exactly. Keeping the rule
server-side means one implementation and one test, rather than the ordering living in the SPA where
it would need re-deriving on every render.

---

## #14 — The scheduler is one process hosting both a gRPC server and a REST app

**Decision**: `services/scheduler` runs a single process: `uvicorn` serves the FastAPI admin app on
`:8001`, and a `grpc.aio` server is started and stopped inside that app's `lifespan` on `:50051`.
Both share the same async SQLAlchemy engine.

**Rationale**: The two surfaces serve the same domain objects and the same database session factory;
splitting them into two deployables would double the process/config/migration story for a phase whose
whole point is that there is exactly *one* new service. `grpc.aio` is asyncio-native, so it shares
the event loop with FastAPI without a thread pool. `lifespan` already owns startup/shutdown ordering,
which is where a gRPC server's `start()`/`stop(grace)` belongs.

**Alternatives considered**: two entrypoints in one image (needs a supervisor, and Alembic/settings
duplicated); gRPC in a thread with the sync `grpc.server` (a second concurrency model inside one
service, and every handler would need to hop back to the loop to touch the async engine).

---

## #15 — Scheduler gets its own logical database in the shared dev Postgres container

**Decision**: A `visitdoc_scheduler` database (plus `visitdoc_scheduler_test`) created by a new
`docker/postgres-init/02-create-scheduler-dbs.sql`, inside the existing `visitdoc-postgres`
container. Its own Alembic tree at `services/scheduler/alembic/`, its own `DATABASE_URL`, and no
cross-database foreign keys — the two schemas reference each other only by opaque id.

**Rationale**: Database-per-service is about *schema and migration ownership*, not about container
count — the boundary that matters (no shared tables, no cross-database joins, no shared migration
history) holds either way. One container for local development keeps `make db-up` a single command
and the developer's memory footprint halved, and moving to a separate container later is a
compose-file change with no code impact.

**Tradeoff to document in the README**: a shared container means a shared failure domain in
development, which the deployment story would not have. Acceptable because the degraded-mode
behavior this phase must demonstrate (FR-044 to FR-047) is exercised by stopping the *scheduler
process*, not its database.

---

## #16 — The admin REST surface lives on the scheduler, authenticated by the session id

**Decision**: FR-048's programmatic interface is served by the scheduler's own FastAPI app
(`/practitioners`, `/patients`), with the session id supplied by the caller in an `X-Session-Id`
header. Every handler scopes its query to that session; a missing header is a `401`, and an id
belonging to another session simply matches nothing (`404`).

**Rationale**: The ROADMAP places the admin surface on the Scheduling service, and it owns the data.
The session id is already a non-guessable, bearer-style credential in this codebase (`chat`'s
`create_session` mints it from `PureRandomPolicy` entropy precisely so it can act as one), so
presenting it *is* the authorization — which is what makes US5-5 ("a session belonging to another
user… the attempt is refused") hold without inventing an auth system this phase does not have.

**Why not proxy it through chat**: chat's session cookie is `HttpOnly` and scoped to chat's origin,
so a proxy would be the only way a *browser* could reach the admin API — but FR-048 explicitly ships
no UI. For the curl/script caller it actually targets, a proxy would add a hop and a second set of
DTOs for no gain.

---

## #17 — `shared-models` finally earns its place

**Decision**: The cross-service vocabulary moves into `packages/shared-models`: the `Specialty`
StrEnum (#25), the `BookingFailureReason` StrEnum, `Weekday`, and the local-date-time parse/format
helpers that decision #5's wire format depends on.

**Rationale**: These are the exact types both services must agree on, defined once — the ten
specialties the scheduler validates against and the chat service's tool schemas enumerate, a
`BookingFailureReason` the scheduler emits and the chat service maps to a patient-facing explanation,
a weekday convention both ends index working ranges by, and one implementation of the no-offset
date-time format both ends validate. Duplicating any of them guarantees drift. The package exists for
this and has been empty since the walking skeleton.

`Specialty` is the clearest case: it is a closed set neither service owns alone, carried on the wire
as a plain string (#25), so the enum in this package is the *only* thing standing between the two
services and a silently-unrecognized value.

**Boundary**: `shared-models` stays pure Pydantic/enums with no I/O and no SQLAlchemy. Persistence
models stay in each service's own `domain/models.py`; the generated protobuf types stay in
`shared-proto`.

---

## #18 — The turn id crosses the service boundary as gRPC metadata

**Decision**: The chat service's gRPC client attaches the currently-bound correlation id as a
`x-turn-id` metadata entry on every call; the scheduler installs a server interceptor that reads it
and binds it into `structlog.contextvars` for the life of the handler.

**Rationale**: `core/correlation.py` already scopes the turn id to the asyncio task on the chat side;
without propagation, a booking's scheduler-side log lines are unjoinable to the turn that caused
them, which is precisely what Phase 2's tracing work will need. Metadata is gRPC's designated channel
for this and keeps the id out of every request message and every handler signature — the same reason
`contextvars` was chosen over a parameter on the chat side.

The scheduler's logging module mirrors `services/chat/src/chat/core/logging.py` (one processor chain,
a wrapping `get_logger()`, the two secret-constant lists), per the style guide's instruction to
mirror rather than reinvent. Its `DATABASE_URL` goes into the URL-secret list on day one.

---

## #19 — The chat HTTP surface becomes a `/chats` resource

**Decision**:

| Before | After |
|---|---|
| `GET /chat` (the session's one chat's messages) | `GET /chats/{chat_id}/messages` |
| `DELETE /chat` (clear the session's chat) | `DELETE /chats/{chat_id}` (chat + messages + patient + appointments) |
| — | `GET /chats` (the session's chats, FR-056 order) |
| — | `POST /chats` (new chat + its patient) |
| `POST /chat {message}` | `POST /chat {chat_id, message, local_now}` |

`GET /chat` and `DELETE /chat` are **removed**, not deprecated in place.

**Rationale**: FR-039 states the single delete operation *replaces* clear-chat, and FR-035 makes "the
session's chat" a concept that no longer exists — an endpoint whose entire meaning was "the one
chat" cannot be kept honest. The frontend is the only consumer and is updated in the same change, so
there is no compatibility surface to preserve. `POST /chat` keeps its path because it remains the
streaming turn endpoint; it gains a required `chat_id` and a required `local_now` (FR-032).

**Existing sessions keep working** (spec Dependencies): the migration adds `chats.patient_id` as
nullable, so every pre-existing chat is simply an unnamed chat in a one-element list, and the first
turn in it provisions its patient through decision #10's lazy path.

---

## #20 — `local_now` is a required, timezone-naive request field, and reaches every clock decision

**Decision**: `POST /chat` requires `local_now: datetime`, rejected with `422` if it carries
`tzinfo`. It flows into graph state, into the booking specialist's system prompt (so "next Tuesday"
resolves, FR-032), and as an explicit field on **every** availability/booking RPC, where the
scheduler uses it — never `datetime.now()` — for the past check (FR-020), the horizon check
(FR-021), and the upcoming-appointments filter (FR-031).

**Rationale**: FR-058 makes this the single clock for every temporal judgement, and the only way to
guarantee the server never quietly substitutes its own is to give the scheduler no reason to call a
clock at all. Passing it per-RPC rather than caching it per session also keeps the scheduler
stateless with respect to time, which is what makes its handler tests deterministic — every
past/horizon case is expressible by varying one field.

**`created_at` columns are exempt**: they are audit metadata written by `server_default=func.now()`,
not a temporal judgement about a user's day, and no requirement compares them to a local time.

---

## #21 — Availability is computed in the scheduler, for a practitioner *and a patient*, over a bounded window

**Decision**: `CheckAvailability(session_id, practitioner_id, patient_id, from_local_date,
to_local_date, local_now)` returns the bookable start times, computed as: for each working range on
each weekday in the window, walk slots of exactly `appointment_duration_minutes` from **each range's
own** start, keep only whole slots that fit inside that one range (so contiguous ranges each restart
the walk at their junction, and a range shorter than one duration contributes nothing), then drop any
slot that overlaps an appointment held by **that practitioner**, overlaps an appointment held by
**that patient with any practitioner**, starts at or before `local_now`, or falls beyond
`local_now + 90 days`. The requested window is capped server-side (14 days, and at most 50 returned
starts) — now required rather than merely chosen, spec FR-067, with an over-long window clamped and
the response marked `truncated` rather than refused.

**Boundaries are shared with the booking validator, not merely similar to it**: `starts at or before
local_now` is dropped because FR-020 refuses a start of exactly `local_now` (so that everything
bookable is also listable under FR-031's strictly-after filter), and `local_now + 90 days` is
inclusive because FR-021 is. Each boundary is written once, in the shared path, precisely so the two
cannot drift by an equals sign.

**`patient_id` is a required argument, not an optional filter** (spec FR-024, revised 2026-08-13).
Availability is patient-relative: the same practitioner's free slots legitimately differ between two
patients in one session, because each patient's own commitments remove different slots. The original
formulation filtered only by the practitioner's appointments, which broke FR-025/SC-009 in a case the
spec tests directly — a patient already booked at 10:00 with Dr. A would be *offered* 10:00 with
Dr. B, and the booking would then be refused by FR-016's patient-overlap rule (US1-5). The chat
service always has `patient_id` to hand (it is an ambient tool argument, contracts/agent-tools.md),
so closing the hole costs one field.

**Rationale**: The scheduler is the only place where the schedule, the duration, and the appointment
rows are visible in one transaction — the same argument the spec used to give it the whole domain —
so computing slots anywhere else means shipping the calendar across the boundary. Sharing one code
path with the booking validator is what makes SC-009/FR-025 true by construction rather than by
matching two implementations, and *that shared path is exactly why the patient filter has to live
here*: an availability rule the booking validator does not also apply is how the two drift apart. The
caps keep a vague patient request ("sometime next month") from returning hundreds of slots into a
model's context.

**Half-open intervals** (FR-061): a slot is dropped only when it genuinely overlaps, treating each
appointment as `[start, end)`. A 10:00 slot is therefore offered to a patient whose previous
appointment ends at exactly 10:00 — under the closed reading every slot would block its neighbour and
a contiguous grid would be entirely unbookable. The same semantics back the exclusion constraints
(#6), so the offer path and the write path cannot disagree about what "overlap" means.

**Grandfathered appointments** are excluded from the offered slots but still consulted for the
overlap filter (FR-023) — they are ordinary rows to the overlap query and merely fall outside the
current working ranges the walk generates.

**What FR-025 does not promise**: an offered slot can still be taken by someone else before this
patient confirms. That race is the one acceptable cause of an offered-then-rejected time (spec
FR-025 as revised, SC-002), and it is resolved by the exclusion constraint, not by the availability
computation — which is why the constraint, not the filter, is what guarantees no double booking.

---

## #22 — Testing shape

**Decision**:
- **`services/scheduler/tests/`** (new unit tier) hits a real `visitdoc_scheduler_test` database,
  mirroring chat's conftest: env override before any `scheduler.*` import, session-scoped
  `alembic upgrade head`, and the `engine.dispose()`-per-test fixture — both halves of the event-loop
  fix in `docs/testing-strategy.md`, which that document already warns this service will need.
- **gRPC handler tests** run the servicer against an in-process channel, not a socket.
- **`services/chat/tests/`** fakes the scheduling stub at the client-module boundary, so tool
  handlers, degraded-mode behavior, and the retry budget are all testable without a scheduler.
- **`tests/integration/`** finally gets real content: chat's gRPC client against a real scheduler
  servicer and a real scheduler database — the booking round trip, the idempotent retry, and the
  deletion cascade across both stores.
- Every test touching a turn keeps mocking `AsyncAnthropic` per `docs/testing-strategy.md`, including
  the booking loop's tool-use responses, and asserts on unmocked artifacts (rows written, gRPC
  requests actually issued) rather than on canned model text.

**Rationale**: This is the first feature with a genuine cross-service surface, which is exactly what
`tests/integration/` was reserved for. Keeping the chat unit tier fake-backed preserves its current
speed and its independence from a second running service, while the integration tier proves the
contract the fakes stand in for.

---

## #23 — Every specialist node bounds its own context to the last 5 turns

**Decision**: `answer_faq` calls `bound_to_last_n_turns(bursts, n=5)` before building its Claude
messages, exactly as `classify_intent_node` already does. `handle_booking` applies the same bound.
The router keeps writing the **full** `bursts` into graph state; each node windows for itself.

**Rationale**: Today `answer_faq_node` receives the full, unbounded chat history — a deliberate
choice recorded in spec 003 ("no fixed cap on context growth"), taken when a chat was a short-lived,
single-thread FAQ exchange. Three things in this phase invalidate it:

- **Chats now last.** A session holds many chats the user switches between (FR-035/FR-038) rather
  than one that gets cleared, so histories grow monotonically with no clear-chat to reset them. Cost
  and time-to-first-token grow with them, on every turn, forever.
- **Turns got more expensive.** A mixed-intent turn already makes three model calls — classify, two
  specialists — plus `compose_answer`, and the booking specialist may make up to six within its own
  loop (#3). Sending an unbounded history into each multiplies the growth by the number of calls.
- **The window is already this project's answer to this question.** Spec 004 fixed it at 5 turns for
  classification, and `history.py` already owns `bound_to_last_n_turns` as a general-purpose helper
  precisely so other callers could reuse it. Having generation alone read unbounded history is the
  inconsistency, not the bound.

Five turns is enough for the follow-up behavior spec 003 exists to provide ("what about for a
child?" after a policy question) and for a booking negotiated across several turns, which is the
longest genuinely context-dependent exchange this phase produces.

**This supersedes spec 003's "no fixed cap on context growth" assumption**, which must be annotated
in that spec — the cap now exists, at the generation boundary. Nothing about storage changes: the
full history is still persisted and still returned by `GET /chats/{id}/messages`; only what is sent
to a model is windowed.

**Where the bound is applied**: inside each node, never by the router. Keeping `bursts` whole in
state means a future node with a different context requirement (a summarizer, an eval probe) is not
silently starved by a decision another node made, and it preserves the property spec 004 established
— `api/chat.py` produces raw `bursts` and never a node-shaped, pre-formatted history (spec 004
research #9).

**Alternatives considered**: bounding once in the router and storing only the window (simpler, but
bakes one node's context policy into shared state and loses the raw history other nodes may need);
a token-budget bound instead of a turn count (adapts to message length, but is non-deterministic
across model/tokenizer changes and would make the classification and generation windows incomparable
— spec 004 rejected the same option for the same reason); leaving generation unbounded and revisiting
under Phase 2's cost metrics (defers a change that is cheaper to make now than after the eval
baselines are recorded against unbounded context).

---

## #24 — Every node logs its own lifecycle; `turn.completed` moves to the one node that owns the turn

**Decision**: A uniform four-event lifecycle per graph node — `node.started`, `node.completed`,
`node.failed`, `node.cancelled` — carrying `node`, `duration_ms`, and a node-specific `result`
payload. Domain-level intermediate phases keep their own event names inside that envelope
(`faq.retrieved`, `turn.groundedness_verdict`, `booking.tool_called`/`tool_result`/`tool_failed`,
`booking.loop_exhausted`). `turn.completed` is emitted **once per turn, by `compose_answer` only**.
Full field list in [contracts/log-events.md](./contracts/log-events.md).

**Rationale**: Until this feature a turn was a straight line — one classification, one FAQ answer —
so `turn.completed` doubled as the FAQ node's result and nothing was lost. Branching breaks that
three ways at once:

- **Two nodes now run concurrently**, so "what did this turn do" is no longer a single sequence. A
  per-node record is the only thing that says which branch produced which part of the answer, and
  what each one cost.
- **A node can now fail without the turn failing.** `handle_booking` may exhaust its loop or hit an
  unreachable scheduler while `answer_faq` succeeds. `turn.error` describes a turn that died;
  `node.failed` describes a branch that did, which is a different fact and needs a different event.
- **The booking node has real internal structure** — up to six tool iterations, each with a call, a
  result, and a possible error. Logging only its final outcome makes the most failure-prone code in
  the phase the least observable, and Phase 2's eval harness (tool-selection correctness) needs
  exactly the intermediate record.

**Why `turn.completed` moves.** It lives in `answer_faq` today, which was correct when the FAQ node
always ended the turn. It no longer does: on a mixed turn `answer_faq` is one of two inputs to a
reply it does not write. Leaving the event there would emit "turn completed" before the turn's actual
answer existed, and would emit nothing at all for a booking-only turn. `compose_answer` is the join
node — it runs on every path, merging or not (#2) — so it is the one place that can emit
`turn.completed` exactly once, after the user-visible reply exists. `answer_faq`'s own outcome moves
into its `node.completed` result, where it now belongs.

**How `node` reaches the intermediate events**: an async context manager (`node_span("answer_faq")`)
binds `node` into `structlog.contextvars` for the node's duration, times it, and emits the
lifecycle event — so `faq.retrieved` and every `booking.tool_called` inherit the node name without
any call site passing it, exactly as `turn_id` already works. This is safe under the fan-out because
asyncio copies the context into each new task: LangGraph's parallel branches each mutate their own
copy, so a sibling node can never observe or clobber another's binding. `node.cancelled` exists
because a superseded turn (spec 003 FR-015) cancels mid-node, and that must be distinguishable from
a failure — the same distinction spec 004 already drew for a cancelled classification.

**Placement**: `agent/node_logging.py`, a new module. The contextvar-binding primitive belongs to
`core/correlation.py` and is reused, but timing plus lifecycle-event emission is a different concern
from correlation-id scoping, and the node lifecycle is an agent-layer idea — `core/` should not need
to know what a graph node is.

**Cost**: four extra log lines per node per turn, plus two per tool call. Acceptable at this
project's scale, and the events are exactly the spans Phase 2's Langfuse tracing will attach to —
this is the structured record that work needs, written once, rather than a second instrumentation
pass later.

**Alternatives considered**: folding everything into a single enriched `turn.completed` (one line
per turn is tidy, but it cannot describe two concurrent branches, cannot report a branch that failed
while the turn succeeded, and is written only after everything is over — useless for a hung tool
call); per-node bespoke event names with no shared envelope (`faq.completed`, `booking.completed`,
`compose.completed` — readable, but nothing can query "every node that failed this turn" without
enumerating names, and each new node invents its own field set); emitting `turn.completed` from
`api/chat.py` instead of `compose_answer` (the API layer would have to reconstruct the outcome from
streamed events it deliberately does not interpret).

---

## #25 — Specialty is a fixed list of ten, carried as a validated string

**Decision** (spec FR-005/FR-060, revised 2026-08-13, superseding both this decision's own earlier
free-form form and the original two-value set): ten specialties, one per practitioner, exposed
name-sorted so a chooser can be built from the service rather than from a copy in a client.

```python
class Specialty(StrEnum):  # packages/shared-models — values ARE the display names
    CARDIOLOGY = "Cardiology"
    DENTISTRY = "Dentistry"
    DERMATOLOGY = "Dermatology"
    GENERAL_PRACTICE = "General Practice"  # FR-057's default
    GYNECOLOGY = "Gynecology"
    NEUROLOGY = "Neurology"
    OPHTHALMOLOGY = "Ophthalmology"
    ORTHOPEDICS = "Orthopedics"
    PEDIATRICS = "Pediatrics"
    PSYCHIATRY = "Psychiatry"
```

| Layer | Representation |
|---|---|
| `shared-models` | `Specialty` StrEnum — the one source of truth, back in the shared package (#17) |
| Scheduler database | `VARCHAR(64) NOT NULL`, a plain string column — **not** a SQL enum |
| Scheduler validation | must be a member of `Specialty`; anything else is `422` (admin API) / `INVALID_ARGUMENT` (gRPC) |
| Admin API | `enum` of the ten values, plus `GET /specialties` returning them name-sorted (FR-060) |
| gRPC | a `string` field carrying the enum's value — **not** a proto `enum` (see below) |
| Tool results | the value verbatim, e.g. `"Dentistry"` |
| Default | `Specialty.GENERAL_PRACTICE` when a practitioner is created without one (FR-057) |

**The enum's values are the display names**, not snake_case keys. There is exactly one canonical
spelling per specialty, so a separate key-to-label mapping would be a second thing to keep in sync
for no gain — and it would make sorting-by-name a different operation from sorting by value. It also
keeps database rows, log lines, and tool results readable without a lookup.

**Plain string column, not a SQL enum.** This follows the codebase's existing rule (`Message.sender`
in `services/chat/src/chat/domain/models.py`, and `docs/python-style-guide.md`'s worked example):
closed in Python, open in the schema, *so an eleventh specialty needs no migration*. Validation lives
where the enum does.

**String on the wire, not a proto enum.** A proto enum would be a second declaration of the same
closed set, and the two could drift — a mismatch that surfaces as a silently-unrecognized value on
one side rather than a type error. A validated string keeps `shared-models`' `Specialty` the single
authority, both ends validate against it, and adding a specialty is one line touching no generated
code. The cost is that protobuf itself no longer enforces the set; the ingress check does. This is
also why the earlier free-form revision cost nothing to reverse — the wire type never had to change.

**Matching is still the model's job** (FR-052/FR-053). A closed list does not make matching a string
comparison: a patient asking for "a dentist", "my teeth", or "a filling" never types "Dentistry". The
booking specialist reads the session's practitioners and interprets the request against their
specialties. The list makes that *easier* — a bounded vocabulary to map onto — not unnecessary.

**No `OTHER` member and no escape hatch.** FR-005 makes the set exhaustive; a practitioner whose real
specialty is absent is a reason to extend the list, which is a one-line change.

**Alternatives considered**: free-form text (the revision this supersedes — no validation to write,
but "Dentistry"/"dentistry"/"dental surgery" become three distinct specialties in one session, and a
dropdown has nothing to enumerate); a `specialties` table with an admin CRUD surface and a foreign
key (runtime-editable and properly relational, but it is real infrastructure — a table, an API, a
join — for a list of ten values that changes about never); a proto enum mirroring the StrEnum
(protobuf-enforced, but two declarations of one set and regeneration on both sides to add a value).

---

## #26 — The chat database is renamed `visitdoc_chat`

**Decision** (spec FR-059): `visitdoc` → `visitdoc_chat`, and `visitdoc_test` → `visitdoc_chat_test`,
alongside the new `visitdoc_scheduler` / `visitdoc_scheduler_test`. Every reference is updated in
this same change:

| Where | Change |
|---|---|
| `.env` / `.env.example` | `DATABASE_URL`'s database segment |
| `docker-compose.yml` | `POSTGRES_DB: ${POSTGRES_DB:-visitdoc_chat}` |
| `docker/postgres-init/01-create-test-db.sql` | creates `visitdoc_chat_test` |
| `docker/postgres-init/02-create-scheduler-dbs.sql` | new (#15), unaffected by the rename |
| `.github/workflows/ci.yml` | the `test` job's `POSTGRES_DB` |
| `docs/testing-strategy.md` | the "Test databases (chat)" section's worked example |
| `.claude/CLAUDE.local.md` | the `docker exec … psql -d <db>` recipe |
| `README.md` | any setup instruction naming the database |

**Rationale**: `visitdoc` was an accurate name while there was one database. This phase adds a second
one, and a scheme where one service's store is named for the project and the other for its service
reads as an accident — the generic name implies a shared or default database, which is exactly the
opposite of the database-per-service boundary this phase exists to establish (#15, Constitution III).
Renaming now costs a `sed` over configuration; renaming later costs the same plus every stale
tutorial, script, and habit accumulated in between.

**No code changes and no Alembic migration.** The name appears only in configuration: `chat`'s own
`Settings.DATABASE_URL` and Alembic's `env.py` both read it from the environment, and the test
harness derives `visitdoc_chat_test` automatically from whatever base name `DATABASE_URL` carries
(`conftest.py`'s `_with_test_suffix`) — so the test database name follows the rename with no edit at
all. Nothing in `chat`'s source contains the string.

**Existing local data**: a developer with a populated volume runs
`ALTER DATABASE visitdoc RENAME TO visitdoc_chat;` (no active connections) — or `make db-reset` if
they do not care. Both are one line in the quickstart. There is no deployed environment, so there is
nothing else to migrate.

**Alternatives considered**: keeping `visitdoc` for chat and accepting the asymmetry (zero work, but
bakes "chat is the default service" into every developer's mental model, which is precisely the
assumption the scheduler split is meant to break); a neutral `visitdoc` bootstrap database with both
services' databases created by init scripts (tidier in principle, but leaves an empty database whose
only purpose is to be the one Postgres creates on startup).
