# visit-doc
A conversational AI-assistant for a for medical clinics that automates appointment booking via chat and get grounded answers to policy/FAQ questions.

See `docs/ROADMAP.md` for the full design and phased build plan.

## Repository layout

A `uv`-workspace monorepo:

```
services/
├── chat/          # FastAPI core backend: agent, RAG, chat, auth
├── scheduler/      # FastAPI + own Postgres, gRPC server
└── frontend/       # React + Vite SPA
packages/
├── shared-models/  # cross-service Pydantic schemas
└── shared-proto/   # chat<->scheduler gRPC contract
```

## Installation

Prerequisites: [`uv`](https://docs.astral.sh/uv/) (Python 3.12 is installed automatically by `uv`
if you don't have it).

```bash
uv sync                    # install every service/package into one shared venv
uv run pre-commit install  # install git hooks (lint/type-check before each commit)
```

## Getting started

The `chat` service needs a local Postgres and Qdrant (`docker-compose.yml`, repo root), and a
`.env` file at the repo root with `DATABASE_URL`, `QDRANT_URL`, `ANTHROPIC_API_KEY`,
`VOYAGE_API_KEY` (see `specs/001-grounded-faq-chat/quickstart.md` for the full walkthrough).

```bash
make db-up             # start Postgres + Qdrant
make run-scheduler-dev # migrate, then serve the scheduler (HTTP :8001, gRPC :50051)
make run-chat-dev      # migrate, then serve the chat service (:8000)
make run-frontend-dev  # Vite (:5173)
```

`chat` and `scheduler` must be built from the same commit. The gRPC contract's `Weekday`
numbering changed in 007 (Monday `0` → `1`) in place, inside the unchanged `scheduling.v1`
package, so a mismatched pair does not fail — a new scheduler's Monday reads as an old chat's
Tuesday and every practitioner's schedule is presented a day out, with no error anywhere.

Each service owns its own database, named for it: `visitdoc_chat` and `visitdoc_scheduler`. Each
has its own Alembic history, and neither has a foreign key into the other — they reference each
other by opaque id only. Both live in the one local Postgres container (see the tradeoff below);
`SCHEDULER_DATABASE_URL` points at the scheduler's, `DATABASE_URL` at chat's.

Each service's test suite runs against its own `_test`-suffixed database (`visitdoc_chat_test`,
`visitdoc_scheduler_test`), with the schema kept at head automatically by a session-scoped fixture
in each `conftest.py` — so manual `curl`/dev work and automated tests can't pollute each other.
`docker-compose.yml` creates all four automatically on a fresh volume; on a pre-existing one,
create the missing ones by hand (see
`specs/005-scheduling-and-booking/quickstart.md` for the exact commands). CI
(`.github/workflows/ci.yml`) uses its own fully ephemeral Postgres/Qdrant service containers, so it
needs no such step.

Qdrant gets the same treatment: tests use a `faq_chunks_test` collection (derived from
`QDRANT_COLLECTION_NAME`, default `faq_chunks`) in the same Qdrant instance, created on demand by
the existing idempotent `ensure_collection` — no volume/init-script step needed, since a collection
is just an API-created resource, not something that has to pre-exist like a Postgres database.

See the [`Makefile`](Makefile) for shortcuts (`make sync`, `make lint`, `make format`,
`make typecheck`, `make precommit`, `make install-hooks`, `make run-chat`, `make run-chat-dev`,
`make run-scheduler`, `make run-scheduler-dev`, `make run-frontend-dev`, `make db-up`,
`make db-down`).

## Grounded FAQ Chat: technology choices

Phase 0's walking skeleton (`specs/001-grounded-faq-chat/`) made several notable technology
choices, each with a tradeoff — full rationale and alternatives considered live in
[`research.md`](specs/001-grounded-faq-chat/research.md):

- **Embeddings**: Voyage AI (`voyage-3-lite`), since Claude has no embeddings endpoint. Chosen
  over a local model (avoids a heavy ML runtime) and over OpenAI (keeps the stack Claude-centric).
- **Postgres drivers**: `asyncpg` for the app, `psycopg` v3 (sync) for Alembic migrations —
  the conventional SQLAlchemy 2.0 pairing, rather than forcing Alembic's sync runner through
  `asyncpg` via `run_sync`.
- **Agent orchestration**: a plain async function (`agent/answer_faq.py`), not LangGraph — a
  single retrieve→gate→generate path has no branching to justify a graph framework yet.
  LangGraph lands in Phase 1 once real branching (parallel specialist nodes) exists.
- **Streaming transport**: NDJSON over a plain `fetch` + `ReadableStream`, not SSE/`EventSource`
  (which can't carry a POST body) or WebSocket (unnecessary for one request/response stream).
- **Chunking**: fixed-size (~1,000 chars, ~150-char overlap), preferring paragraph/sentence
  boundaries over mid-word cuts — simple and defensible at this phase's scale; semantic chunking
  and reranking are deferred to Phase 1.
- **Groundedness gate**: a pre-generation similarity-threshold check on retrieval, not a second
  LLM call (LLM-as-judge) — satisfies the constitution's mandatory-abstention principle without
  doubling latency/cost on every question; a fuller check lands in Phase 1/2.
- **Citations**: derived structurally from retrieval (which chunks were actually placed in
  Claude's context), not self-reported by the model — avoids hallucinated citations, and lets a
  reviewer directly diff the streamed answer against the verbatim `chunk_text` it cites.

## Structured Logging: technology choices

`specs/002-structured-logging/` instruments `chat`'s agent/RAG pipeline so a turn's full decision
trace can be reconstructed from logs alone — full rationale and alternatives considered live in
[`research.md`](specs/002-structured-logging/research.md):

- **Structured logging library**: `structlog`, not stdlib `logging` + a custom `Formatter` or
  `loguru` — its processor-chain architecture lets truncation/redaction/rendering be centralized in
  one place (`core/logging.py`) and swapped later (e.g. for a Langfuse-ready renderer) without
  touching any of the ~20 call sites that actually log.
- **Correlation IDs**: ULID (`python-ulid`), not a hyphenated UUID4 — same collision resistance,
  but shorter, separator-free (one double-click selects the whole ID in a terminal), and
  lexicographically sortable by creation time. Bound per-request via `structlog.contextvars`
  (`core/correlation.py`), scoped to the current `asyncio` task so concurrent requests never see
  each other's bound `turn_id`/`operation_id`.
- **Severity tiers**: the three standard log levels (`info`/`error`/`critical`) rather than a
  bespoke severity field — `structlog`'s `ConsoleRenderer` already styles by level, so only
  `critical`'s extra prominence over `error` needed a small style override, not a new mechanism.
- **Sub-step logging granularity**: one summarized entry per sub-step (e.g. `faq.chunks_embedded`
  reports a chunk count), not one entry per chunk — keeps log volume proportional to pipeline steps
  rather than content length, while a failure is still fully attributable via the operation's
  `failed_step`.

## Conversational Chat History: technology choices

`specs/003-conversational-chat-history/` (ROADMAP Phase 1a) turns the single-turn `/chat` endpoint
into a persisted, multi-turn chat — full rationale and alternatives considered live in
[`research.md`](specs/003-conversational-chat-history/research.md):

- **`Session` kept separate from `Chat`**: the anonymous visitor identity (the cookie) and the
  chat thread it currently owns are two rows, not one, even though this phase only ever has one
  `Chat` per `Session`. Costs one small table now, but avoids a breaking migration later when a
  `Patient` layer is expected to sit between a `Session` and its chat(s) (spec.md Future Direction).
- **Flat, sender-tagged `Message` log**: no paired request/response turn — each patient or
  assistant message is its own row, ordered by `created_at` ascending (via a dedicated
  `ix_messages_chat_id_created_at` index, not by ULID id — id order isn't reliably equivalent to
  `created_at` order across concurrent writers), with `sender` an open string set (not a DB
  enum) so ROADMAP Phase 1d's `staff` sender can be added later with zero schema migration.
- **`reply_to_message_ids` ties a reply to every turn it actually answers**: an assistant
  message's `reply_to_message_ids` (JSONB list, not a single FK) records every patient message
  id it answers, in order — necessary because a merged burst (FR-014) is answered by exactly one
  assistant message, so a scalar `reply_to_message_id` pointing only at the triggering message
  silently lost the earlier ones. History-building never has to infer that pairing from row
  order — a stray or out-of-order write can no longer corrupt a different turn's history.
- **Append-only persistence, no pending/update phase**: a patient message is inserted the moment
  it's validated; an assistant message is inserted once, in full, only on success. A failed or
  cancelled attempt simply never gets an assistant row — no rollback, no partial-state cleanup.
- **Cancel-and-restart via an in-process registry**: `agent/generation_registry.py` is a plain
  `dict[chat_id, tuple[turn_id, asyncio.Task]]`, not Redis/pub-sub — this phase runs one `chat`
  process, so process-local state is sufficient, and it avoids infrastructure a single-instance
  deployment has no use for yet. The `turn_id` half exists purely so a cancellation can be logged
  (`turn.cancelled`) against the specific patient turn that got superseded, not just an opaque task.
- **`message.persisted`/`turn.cancelled`/`turn.message_received` diagnostic logging**: every
  inserted `Message` logs `message.persisted` (chat id, message id, sender,
  `reply_to_message_ids`), every superseded generation logs `turn.cancelled` (chat id, the
  cancelled turn's id, the superseding turn's id), and `turn.message_received` — logged as soon
  as a turn's pipeline starts, before retrieval — also carries `reply_to_message_ids`, so a log
  reader can already tell from that first line whether a merged burst or a single message is
  being processed, without waiting for the later `message.persisted` line. A patient seeing no
  reply to an earlier message is traceable to an explicit cancellation event in the logs, not
  silently indistinguishable from the message never having been sent.
- **Multi-turn context as Messages-API history, not prompt concatenation**: prior messages are
  passed as a proper alternating `user`/`assistant` list, matching how Claude is trained to use
  multi-turn context, rather than hand-rolled into the current message's prompt string.
- **Retrieval scoped to the merged trailing patient-message run**: a burst of unanswered messages
  (e.g. "When can I see" + "Dr. Josh?") is merged into one retrieval query, reusing the same merge
  pass already needed for the Messages-API history — not a second mechanism or an extra LLM call.
- **Session id generation**: `python-ulid`'s bare `ULID()` constructor turned out to be monotonic
  *by default* in the installed version — same-millisecond calls increment the previous randomness
  by 1 rather than resourcing it, which would fail the non-guessable-identifier requirement for a
  value used as a bearer cookie. `chat_repository.create_session` instead uses an explicit
  `ulid.ULIDGenerator(policy=ulid.PureRandomPolicy())`, verified empirically against the library's
  source before relying on it.

## LangGraph + Intent Classification: technology choices

`specs/004-langgraph-intent-classification/` (ROADMAP Phase 1b) wraps the existing FAQ-answering
pipeline in a LangGraph `StateGraph` and adds intent classification ahead of any real routing —
full rationale and alternatives considered live in
[`research.md`](specs/004-langgraph-intent-classification/research.md):

- **Sequential graph shape (`classify_intent_node -> answer_faq_node -> END`), not parallel**:
  classification completing before generation starts spends part of the latency budget on
  classification, but it's what gives ROADMAP Phase 1d's eventual routing decision (which
  specialist node(s) to launch) a graph edge to attach to — a parallel shape has no such decision
  point. `answer_faq()`'s own retrieve/gate/generate/stream logic is unchanged; the graph forwards
  its events via LangGraph's custom stream-writer mechanism rather than rewriting it to fit a
  return-a-state-update shape, preserving the existing token-by-token NDJSON streaming contract.
- **Classification shares the FAQ pipeline's existing cancel-and-restart task, not a separate
  fire-and-forget one**: both nodes run inside the one `asyncio.Task`
  `agent/generation_registry.py` already tracks and cancels per chat. A message whose turn is
  superseded before classification finishes gets no classification record at all — its content
  still reaches the *surviving* message's own classification call via the bounded context window
  below, so nothing is lost, only avoided as wasted work.
- **Native JSON Outputs (`output_config.format`), not forced tool-use**: Claude's JSON Outputs
  mechanism (constrained decoding against a schema) is the documented fit for classification
  specifically, distinct from Strict Tool Use's agentic-tool-calling framing. The request schema is
  built from `IntentLabel`'s own JSON schema with `classification_failed` filtered out of its
  `enum`, so the model can never produce that value structurally — it's assigned only by
  orchestration code (`classify_intent_node`) when the call itself fails or returns something
  invalid, never silently conflated with a real classification outcome.
- **One `IntentLabel` enum for both what the model can say and what gets logged**, rather than two
  near-duplicate enums — the schema-level `enum` exclusion above already enforces the model-facing
  restriction at the one place it actually matters (the API boundary), so a second type would only
  duplicate one concept for no added guarantee.
- **Haiku 4.5 for classification, unchanged Sonnet 5 for generation**: routing/classification steps
  use the cheapest model capable of the task, reserving the stronger model for generation — the
  same shared `AsyncAnthropic` client, just a different `model=` argument.
- **Classification context bounded to the last 5 turns (`history.py::bound_to_last_n_turns`)**, not
  the unbounded history `answer_faq`'s generation call uses: classification is cost-sensitive in a
  way generation quality isn't. `bound_to_last_n_turns` lives alongside `split_into_bursts`,
  `derive_reply_to_message_ids`, and `to_claude_messages` in `history.py` rather than a
  classifier-only module, since all four are about interpreting `Message` rows as turns/bursts — a
  future caller (e.g. Phase 1d's specialist nodes) can reuse it the same way.
- **Log-only, not a new table**: `intent.classified` (turn id + label(s) only — no raw message
  text, unlike the pre-existing `turn.message_received`) is the classification record. Formal
  tracing infrastructure (Langfuse) arrives in a later phase; a persisted table now would be scope
  beyond what reviewing classification quality via structured logs actually requires yet.
- **`turn.message_received` moved earlier, out of `answer_faq()`**: it now fires from `api/turn.py`
  once, before the graph task is even created — unconditionally, for every incoming message,
  including one whose turn is later cancelled. This keeps it answering "what did the system start
  processing" regardless of which stage (classification or generation) is currently active, a
  distinction that didn't exist before this feature inserted a stage ahead of generation.


## Scheduling and End-to-End Booking: technology choices

`specs/005-scheduling-and-booking/` (ROADMAP Phase 1c) turns `services/scheduler` into a real
service and gives the agent its first write capability — full rationale and alternatives considered
live in [`research.md`](specs/005-scheduling-and-booking/research.md):

- **A separate scheduling service with its own database, talking gRPC**: the one deliberate service
  boundary in this phase. It owns patients, practitioners, working schedules, and appointments, and
  the chat service holds nothing but opaque ids into it. The cost is a real network hop on the
  booking path and a failure mode to design for; the payoff is that the invariants that matter most
  — no double booking, no appointment outliving its patient — are enforced in one place, by the
  datastore that owns them.
- **Double booking prevented by two PostgreSQL exclusion constraints, not application code**:
  `EXCLUDE USING gist (patient_id WITH =, tsrange(starts_at, ends_at) WITH &&)` and the same keyed
  by practitioner. A read-then-write check cannot survive two transactions racing for one slot; an
  exclusion constraint can, and it is what makes "exactly one of them wins" true rather than
  likely. `tsrange` is half-open, so back-to-back appointments do not conflict — without which a
  contiguous slot grid would be entirely unbookable. Requires the `btree_gist` extension, and a
  `CREATE TYPE timerange AS RANGE (subtype = time)` for the equivalent rule on working ranges,
  since PostgreSQL ships no range type over bare `time`.
- **"Inside the schedule" and "on the grid" are creation-time checks, deliberately *not*
  constraints**: editing a practitioner's schedule must not reject the edit because an existing
  appointment would violate it — those appointments are grandfathered, keeping the times they were
  agreed at. A CHECK or trigger enforcing them continuously would reject exactly the edit that has
  to succeed.
- **No timezone anywhere**: `TIMESTAMP WITHOUT TIME ZONE`, naive Python `datetime`s, and offset-free
  ISO-8601 strings on the wire. `TIMESTAMPTZ` would silently rotate every value through the
  server's zone, and `google.protobuf.Timestamp` denotes an absolute instant — putting a local
  wall-clock time in one asserts a zone that does not exist. Every past/upcoming/horizon judgement
  is made against a `local_now` the client sends, never a server clock, so the scheduler has no
  reason to call one at all. The cost is that two devices in genuinely different zones would read
  the same stored times as different moments — out of scope for a single-session demo, and the
  reason the earlier stored-timezone design was dropped.
- **Capabilities reach the agent through an in-process tool registry, not MCP**: `(name,
  description, JSON schema, handler)` records rendered straight into the Anthropic Messages API's
  `tools=`. The booking node knows only names and schemas, so swapping a handler for an MCP client
  later changes no agent code — which is the decoupling that actually matters. MCP's added value
  over this is cross-process reuse by a third-party client, which nothing in this phase consumes;
  the ROADMAP's Phase 1c bullet was amended accordingly.
- **A derived idempotency key, not a random one**: `uuid5` over exactly
  `(patient_id, practitioner_id, starts_at)`. A random key protects only the transport retry that
  generated it; a derived one also collapses the case that actually bites — a lost confirmation
  where the *model* re-issues the same booking several turns later, which would otherwise be
  reported to the patient as a conflict with their own appointment. Because the key is a function
  of the three fields the scheduler re-checks it against, a key presented with a *different*
  request can only mean the derivation broke, and is answered with an error rather than by
  replaying a booking the patient never asked for.
- **Domain refusals are data, transport failures are status codes**: a booking the service
  evaluated and declined comes back as a successful RPC carrying one of eight typed reasons; only
  an unreachable service raises. Collapsing both into a status code would force the chat service to
  reverse-engineer "explain why to the patient" from "we could not reach the service" out of a
  string. The caller applies a 2-second deadline and at most two attempts, retrying only
  `UNAVAILABLE` and `DEADLINE_EXCEEDED`.
- **Both services' databases share one local Postgres container**: database-per-service is about
  schema and migration ownership, not container count, and the boundary that matters — no shared
  tables, no cross-database joins, no shared migration history — holds either way. The tradeoff is
  a shared failure domain in development that a real deployment would not have. Acceptable because
  the degraded-mode behavior this phase must demonstrate is exercised by stopping the *scheduler
  process*, not its database; moving to a separate container later is a compose-file change with no
  code impact.
- **Parallel specialists with a merge step, pulled forward from Phase 1d**: a message like "what
  should I bring, and can I book Friday?" is ordinary phrasing, and once there are two real
  specialists, routing it to one ships a visibly partial answer. The extra cost is bounded to
  mixed-intent turns: a single-specialist turn streams from its own specialist and the merge node
  is a no-op, so the FAQ path keeps its existing latency and behavior.
- **Every model call bounded to the last five turns**: generation used to read unbounded history,
  which was fine when a chat was short-lived and a turn made one model call. Now a session holds
  many long-lived chats and a mixed-intent turn can make several calls, with up to six more inside
  the booking loop. Storage is unaffected — only what is sent to a model is windowed.


## Rescheduling and Cancellation: technology choices

`specs/006-reschedule-and-cancel/` (ROADMAP Phase 1d, part 1) gives the agent its first *mutating*
capabilities — full rationale and alternatives considered live in
[`research.md`](specs/006-reschedule-and-cancel/research.md):

- **Cancellation retains the record and sets a status; it does not delete the row**: `appointments`
  gains `status ∈ {standing, cancelled}`, so a cancelled appointment keeps its identifier, its
  practitioner and its times, and a patient can still ask what they cancelled. The alternative
  shapes were worse in the same way: a `deleted_at` names the row's fate rather than the
  appointment's, and a separate `cancelled_appointments` table loses the identifier's continuity
  and makes every "which appointment is this?" lookup read two tables. There is deliberately no
  `cancelled_at` column — nothing reads it, and a column no requirement consumes is one that gets
  populated, never read, and eventually trusted.
- **The consequences of retaining it are carried by *partial* constraints, not application
  filters**: both overlap exclusion constraints and the idempotency key's uniqueness gain
  `WHERE (status = 'standing')`. This is the whole of "a cancelled slot is bookable again
  immediately" — an unconditional exclusion constraint would go on rejecting any booking of that
  time however carefully the application filtered, because the row still holds its `tsrange`. The
  key follows the same predicate, which amends 005's FR-064: a booking key now lives as long as the
  appointment *stands*, not as long as its record exists, so cancelling frees it in the same
  statement. PostgreSQL has no partial UNIQUE *constraint*, so that one becomes a partial unique
  **index** — a real difference, not a spelling, since the insert-conflict handler must then match
  on the index name. It also buys "an appointment never blocks its own change" for free on the
  write path, because an exclusion constraint compares distinct rows.
- **A change is one conditional `UPDATE` whose `WHERE` clause *is* the staleness guard**: identity,
  session scope, eligibility rules and the guard travel in one predicate, and nothing is read
  first. A check performed before the write leaves a window in which two changes both pass and the
  second silently overwrites the first — and the pairing that matters most, a cancellation racing a
  move, collides with no other appointment, so the datastore cannot catch it either. The guard has
  two arms, the state the patient was shown *or* the state the request asks for, which is what
  makes a re-sent change report its original outcome instead of a false conflict. Old and new
  values come back from that same statement, via a `FOR UPDATE` CTE holding the pre-image, because
  reading the "before" separately would describe a state a concurrent change may already have
  replaced — a false record rather than a missing one. The lock earns its place: an unlocked
  `FROM appointments AS old` self-join carries no row mark, so the loser of two identical moves —
  the ordinary shape of the caller's own retry — reads a pre-image from before the winner committed
  and logs a second change record for one transition.
- **Neither change RPC carries an idempotency key**: a key exists to stop a *second row* coming
  into being, and neither operation can create one. A key derived from the target state would
  actively introduce a replay bug — 09:00 → 10:00 → 09:00 → 10:00 derives the first move's key on
  the third and would replay it, leaving the appointment at 09:00 while reporting success. The
  target-state arm of the guard is what makes a re-send safe instead.
- **A tool result status of `unknown`, distinct from `unavailable`**: 005 had both meanings but
  separated them only in prose, so they were one value to every consumer that read `status`. That
  was tolerable when the only write was a booking whose derived key made a retry safe; it is not
  once a write can be a cancellation, because `unavailable`'s explanation says "nothing was
  booked", and a deadline we stopped waiting on is no evidence the server stopped working. The
  distinction is now visible to the turn's outcome derivation, to the composing step's truth
  constraint, and to the tests.

## Escalation and the Staff Console: technology choices

- **Additive chunk revisions, and one publishing commit.** A FAQ save chunks and embeds *before*
  either store is written, writes its chunks under a **new revision**, and publishes with one local
  `UPDATE` whose `WHERE` carries the revision it expects to supersede. Nothing is deleted,
  overwritten or reverted. The previous design deleted an entry's chunks and re-upserted them, and
  its failures were repaired by a best-effort compensating write — which could half-succeed and
  swallow its own failure, leaving Postgres claiming content Qdrant had no vectors for. Under
  additive revisions there is nothing to compensate for: a failure before the commit changed
  nothing observable, and a failure *of* the commit is the change not happening, so the entry goes
  on answering exactly the text it was answering a moment ago. The trade is named rather than
  hidden — **leaked storage instead of a lost answer**. Chunks nothing vouches for are removed by a
  per-entry sweep that is idempotent, never load-bearing, and **silent**: it raises no event at all,
  because a sweep is not an operation that can fail — the chunks it removes were already
  unreachable. It deletes only revisions **older** than one that was live, named explicitly rather
  than addressed as "everything but the live one": an entry's published revisions strictly increase,
  so a predicate that deletes by what it does not name would destroy the chunks of a save that
  published while the sweep was on its way.
- **Session-scoped retrieval, as a filter term rather than a post-check.** Each entry names one
  live revision, revision ids are minted per save and never shared, so filtering a search to *this
  session's live revisions* scopes both the session and the revision in a single `MatchAny` term.
  A shared corpus stopped being tenable the moment the console gained a delete button: one
  visitor's edit would otherwise change what every other visitor is answered. The predicate is on
  the search itself and never on its results, which is the difference between a chunk that is not
  retrieved and one that is retrieved and discarded — only the second shows up as a leak in
  citations, groundedness, or a log.
- **Polling one endpoint, not a push channel.** Both panes are kept in step by a 2-second poll of
  `GET /console/conversations`. This deviates from the ROADMAP's wording ("a live push") and is a
  correctness argument rather than a shortcut: every mark and every silence is *stored state*, so a
  poll reads the truth and self-heals — a dropped answer costs one interval of staleness. A dropped
  push leaves a pane wrong indefinitely with nothing to correct it, and being wrong about which
  conversation needs a person is the failure the whole surface exists to prevent. One endpoint
  serves both sides: the staff list renders it, and the patient pane refetches its open thread when
  that conversation's newest message advances past what it holds.
- **Two silences, two columns.** `escalated_at` answers *may the assistant speak*; `attention_since`
  answers *has a person acted*. They are cleared by different things and disagree in both
  directions — a failed tool call emphasizes a conversation without silencing it, and the console's
  switch ends a silence without clearing the emphasis, because taking a conversation is not
  answering it. One column carrying both passes almost every test and fails exactly those two.
- **The pause is a stored deadline, compared against the database's clock.** Everywhere else in
  this system a date-time judgement is made against the visitor's `local_now`; this one is not,
  because it is a deadline between two people. A client clock would let a patient end a staff
  member's pause, and two open tabs would count down differently. `assistant_paused_until > now()`
  is evaluated in SQL, and the countdown a tab renders is the server's own arithmetic.
- **A second transport across the chat↔scheduler boundary.** Practitioner administration is proxied
  over the scheduler's existing `/practitioners` REST API while everything else crosses that
  boundary as gRPC. The browser cannot call it directly — the session that authorizes a change is
  in an `HttpOnly` cookie — and re-encoding that CRUD as three RPCs would put a second copy of one
  contract across the boundary, where every future rule change would land in two places or diverge
  in one. The proxy makes **one attempt and never retries**: a retried `POST` would create two
  practitioners, so an unknown outcome is reported as unknown.
