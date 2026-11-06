# VisitDoc — Roadmap in detail

The full design and phased build plan, with the reasoning behind each choice. [`ROADMAP.md`](ROADMAP.md)
is the one-page summary of what was built in which phase.

A conversational assistant for a medical clinic: patients book, reschedule, and cancel
appointments through chat, get grounded answers to policy/FAQ questions, and get escalated to
human staff when the assistant can't confidently resolve their request.

This is a portfolio project targeting an **AI developer** role. Effort is concentrated on the
applied-AI core — the agent, RAG, tool use, and evaluation/observability — with platform and
infrastructure work scoped as optional later phases.

---

## Design principles

1. **Thinnest backend that makes the agent real and measurable.** A core backend plus one
   deliberately separated service — Scheduling — with a relational database each and one vector
   database. A single service boundary provides real cross-service design to demonstrate without the
   operational sprawl of splitting everything.
2. **The AI core gets the bulk of the effort** — the agent graph, RAG done properly, and an
   evaluation + observability harness, which is the centerpiece of the project.
3. **Platform layers are added only if time allows**, as deliberate evolution, with a documented
   rationale for why each one is introduced.
4. **Each significant technology choice and its tradeoff is documented in this document**, in a
   "technology choices" section under the phase that made it, so the design reads as intentional.

---

## What the system does

**Patients** open a chat and talk to the assistant in plain language: searching for a doctor or
specialty, checking availability, booking or changing an appointment, or asking a question like
"what should I bring to a first cardiology visit?" The assistant answers FAQs by retrieving
grounded content from clinic policy documents, cites its sources, and **abstains and escalates when
retrieval is weak** rather than confabulating. It hands off to a human whenever it can't confidently
resolve something; urgent or ambiguous requests are prioritized.

**Staff** (Phase 1d) work from a console that notifies them of incoming escalations, lets them take
over a conversation and reply in the patient's own thread, and manages the practitioners and FAQ
entries the assistant answers from. Both sides live on one screen — patient chats on the left, the
staff console on the right — so a visitor drives an escalation and then answers it, as the session's
single staff member, without logging in as anyone.

---

## Architecture — AI-core phase

| Component | Choice | Why |
|---|---|---|
| Core backend | **FastAPI** (Python) | Hosts the agent, RAG, chat, and auth. Python because the agent/RAG/eval ecosystem lives there. A single deployable for everything except Scheduling. |
| Scheduling service | **FastAPI + own PostgreSQL** | The one separated service: owns patients, practitioners, availability, and appointments. A clean, self-contained seam. |
| Inter-service call | **gRPC** | Availability and booking are synchronous request/response the agent needs immediately — a justified use of gRPC and one clean place to demonstrate it. |
| Relational store | **PostgreSQL** (×2 — core + Scheduling) | Structured data with real integrity needs; database-per-service across this one boundary. |
| Vector store | **Qdrant** | Embeddings for retrieval-augmented FAQ answering. |
| Frontend | **React + Vite SPA**, minimal | A streaming chat UI, kept lean. |
| Agent framework | **LangGraph** | Real branching and parallel intent handling, not just a linear chain. |
| Tracing / eval | **Langfuse Cloud** (free Hobby tier) | Trace UI, per-step latency, and token cost with no infrastructure to run. |

**Double-booking is prevented in the Scheduling service at the database level**, using PostgreSQL's
interval/range types and an exclusion constraint in Scheduling's own database rather than relying on
application code to catch the race.

**The data boundary follows the invariants.** Scheduling owns the entire scheduling domain —
patients, practitioners, and appointments — because every hard rule it has to enforce (no
overlapping appointments, nothing booked outside a practitioner's schedule) needs the schedule and
the appointment rows visible in one transaction. The core backend keeps sessions, chats, and
messages. The two databases reference each other only by opaque id, never a cross-database foreign
key: Scheduling's rows carry the owning `session_id` (and a patient its `chat_id`), while the core
backend caches the `patient_id` on its chat row.

---

## Phased build plan

### Phase 0 — Walking skeleton
Prove the entire loop end to end before adding any branching. One chat endpoint, a minimal
streaming chat UI, and an agent step that does exactly one thing: answer an FAQ via RAG (message in
→ retrieval → grounded answer streaming back), implemented as a plain function call to the Claude
API — no agent framework yet, since a single linear step has no branching to justify one.

#### Grounded FAQ Chat: technology choices

Phase 0's walking skeleton (`specs/001-grounded-faq-chat/`) made several notable technology
choices, each with a tradeoff — full rationale and alternatives considered live in
[`research.md`](../specs/001-grounded-faq-chat/research.md):

- **Embeddings**: Voyage AI (`voyage-4-lite`, 1024-dimensional), since Claude has no embeddings
  endpoint. Chosen over a local model (avoids a heavy ML runtime) and over OpenAI (keeps the stack
  Claude-centric). Moved up from `voyage-3-lite` (512-dimensional), which the original Phase 0
  research chose; the dimension is part of the choice, since it is baked into the Qdrant
  collection at creation and a change to it requires recreating the collection and re-indexing.
- **Reranking**: Voyage AI (`rerank-3`), a cross-encoder scoring the question and a chunk
  *together*, re-ordering the shortlist the embedding search produced. Chosen over a second vendor
  (Cohere) because Voyage was already the embedding provider, so it adds no package, no credential
  and no new client lifecycle; over a local cross-encoder because that drags a heavy ML runtime into
  a pure-Python service and would make the fallback path effectively untested; and over asking Claude
  to score, which is a generation call wearing a scoring hat — slower, dearer, and a free-text
  judgement where a threshold needs a number. The tradeoff accepted: one more external call on the
  FAQ path, and therefore one more thing that can be down. That is bounded by a 5-second deadline and
  absorbed rather than propagated — a failed or slow reranker costs the answer its precision stage,
  never the turn (`specs/008-reranked-retrieval-pipeline/`).
- **No similarity floor in front of the reranker** (018): the shortlist is cut by rank alone (the
  dense top 5 and BM25's top 3, below), and the rerank floor is the one gate that decides whether a question is
  answered. A cosine floor of 0.25 sat there before, as a cheap early abstention. Measured on the
  extended corpus it was dropping right answers - "PR-4", "braces", "HC-9", short questions whose
  chunk dense search had ranked *first* but scored 0.22-0.24 against a long section - and removing
  it answered all three while every labelled gap still abstained. The tradeoff accepted: a rerank
  call on every FAQ request, including the few an early floor used to stop, which was four calls in
  a 146-case run. A reranker outage is the one place a floor still applies
  (`UNRERANKED_SIMILARITY_FLOOR`), because with no rerank score nothing else has judged the
  shortlist (`specs/018-hybrid-retrieval/evaluation/findings.md`).
- **BM25 beside dense search, adding candidates rather than fused with it** (018): every chunk
  carries a sparse BM25 vector next to its embedding, Qdrant computing it from the text, and both
  searches run in one call under the same session and live-revision filter. The reranker gets the
  dense top 5 plus BM25's top 3, so an exact plan name, form number or procedure code that dense
  search ranks too low still reaches it, and nothing dense search shortlists can be lost.
  Reciprocal Rank Fusion, the usual way to combine the two, was built first and failed the eval:
  replayed over the baseline, it let chunks both branches returned on common words outrank dense
  search's top hit, and three answered requests abstained. A weighted RRF cannot fix that - any
  weight low enough to protect the dense top is too low for BM25 to promote a chunk into the
  shortlist. The shipped
  form changed no verdict and no top reranked chunk across the 146-case set, live and replayed.
  It also improved nothing: on a 62-chunk corpus dense search already shortlists every cited entry,
  so BM25 is insurance for a larger corpus, not a measured win. The tradeoff accepted: a sparse
  vector per point, a quarter more documents per rerank call (680 against 545 over the set), and
  BM25 term statistics that are computed across every session's chunks - statistics only, since
  the filter keeps every session's text its own
  (`specs/018-hybrid-retrieval/evaluation/findings.md`).
- **Postgres drivers**: `asyncpg` for the app, `psycopg` v3 (sync) for Alembic migrations —
  the conventional SQLAlchemy 2.0 pairing, rather than forcing Alembic's sync runner through
  `asyncpg` via `run_sync`.
- **Agent orchestration**: a plain async function (`agent/answer_faq.py`), not LangGraph — a
  single retrieve→gate→generate path has no branching to justify a graph framework yet.
  LangGraph lands in Phase 1 once real branching (parallel specialist nodes) exists.
- **Streaming transport**: NDJSON over a plain `fetch` + `ReadableStream`, not SSE/`EventSource`
  (which can't carry a POST body) or WebSocket (unnecessary for one request/response stream).
- **Chunking**: by heading section where an entry has markdown headings, every chunk prefixed with
  its heading path (`# Dental care guide` / `## Dental insurance`); fixed-size (~1,000 chars,
  ~150-char overlap, preferring paragraph and sentence boundaries) inside an over-long section and
  for an entry with no heading, which is every Q&A entry - those chunk exactly as they did. Chosen
  over semantic chunking because the documents already mark their own sections, so a model
  deciding where one ends would buy nothing and add a call to every save. Fixed-size windows were
  the Phase 0 choice and stopped being defensible once the corpus held long documents (018): a
  chunk ended on a bare heading, began mid-sentence, and put one section's sentence beside
  another's - "do you take Delta Dental PPO?" was read as being about taking a medicine because the
  blood-thinner paragraph shared its chunk, declined 10 times in 10 replays where section chunks
  answer it 10 in 10. The tradeoff accepted: more, smaller chunks (36 to 62 on the starter corpus),
  so more of the 25-wide pool is spent on one document, and a heading prefix repeated in every
  chunk's embedding (`specs/018-hybrid-retrieval/evaluation/findings.md`).
- **Groundedness gate**: a pre-generation similarity-threshold check on retrieval, not a second
  LLM call (LLM-as-judge) — satisfies the constitution's mandatory-abstention principle without
  doubling latency/cost on every question; a fuller check lands in Phase 1/2.
- **Citations**: derived structurally from retrieval (which chunks were actually placed in
  Claude's context), not self-reported by the model — avoids hallucinated citations, and lets a
  reviewer directly diff the streamed answer against the verbatim `chunk_text` it cites.

#### Structured Logging: technology choices

`specs/002-structured-logging/` instruments `chat`'s agent/RAG pipeline so a turn's full decision
trace can be reconstructed from logs alone — full rationale and alternatives considered live in
[`research.md`](../specs/002-structured-logging/research.md):

- **Structured logging library**: `structlog`, not stdlib `logging` + a custom `Formatter` or
  `loguru` — its processor-chain architecture lets truncation/redaction/rendering be centralized in
  one place (`core/logging.py`) and swapped later without touching any of the ~20 call sites that
  actually log.
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

### Phase 1 — The real agent
The spine of the project, split into sub-phases small enough to build and verify one at a time. Each
builds on the ones before it; a tool-call interface stays the seam that keeps agent logic
decoupled from how a capability is actually implemented, so a later sub-phase can swap an
implementation without touching the agent. That seam is an in-process registry from 1c onward (see
1c); MCP becomes its transport only once something outside this process wants to consume the same
tools.

#### Phase 1a — Multi-turn conversation state
Turn Phase 0's stateless, single-turn exchange into a real conversation before touching the agent
itself: persist conversation history per visitor, and have generation take prior turns into account
so a follow-up question doesn't require repeating context. Still a plain function call under the
hood — no LangGraph yet — but the conversation shape (a flat, ordered log of turns, not a fixed
request/response pair) is now in place for 1d to extend once staff can post into it too.

##### Conversational Chat History: technology choices

`specs/003-conversational-chat-history/` (Phase 1a) turns the single-turn `/chat` endpoint
into a persisted, multi-turn chat — full rationale and alternatives considered live in
[`research.md`](../specs/003-conversational-chat-history/research.md):

- **`Session` kept separate from `Chat`**: the anonymous visitor identity (the cookie) and the
  chat thread it currently owns are two rows, not one, even though this phase only ever has one
  `Chat` per `Session`. Costs one small table now, but avoids a breaking migration later when a
  `Patient` layer is expected to sit between a `Session` and its chat(s) (spec.md Future Direction).
- **Flat, sender-tagged `Message` log**: no paired request/response turn — each patient or
  assistant message is its own row, ordered by `created_at` ascending (via a dedicated
  `ix_messages_chat_id_created_at` index, not by ULID id — id order isn't reliably equivalent to
  `created_at` order across concurrent writers), with `sender` an open string set (not a DB
  enum) so Phase 1d's `staff` sender can be added later with zero schema migration.
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

#### Phase 1b — Adopt LangGraph + intent classification
Replace Phase 0's plain function call with a LangGraph graph, proving the framework swap on its own
before adding new capabilities on top of it. Add **intent classification** into one or more of FAQ /
booking / escalation, using a cheap, fast model and **structured output** rather than free-text
parsing. The graph still has only one real path (FAQ) at this point — branching comes in 1d.

##### LangGraph + Intent Classification: technology choices

`specs/004-langgraph-intent-classification/` (Phase 1b) wraps the existing FAQ-answering
pipeline in a LangGraph `StateGraph` and adds intent classification ahead of any real routing —
full rationale and alternatives considered live in
[`research.md`](../specs/004-langgraph-intent-classification/research.md):

- **Sequential graph shape (`classify_intent_node -> answer_faq_node -> END`), not parallel**:
  classification completing before generation starts spends part of the latency budget on
  classification, but it's what gives Phase 1d's eventual routing decision (which
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
- **Small talk on Haiku too, under its own `SMALL_TALK_MODEL`**: the reply to "Thanks" or "Hi" is
  the one text a patient reads that the cheap model writes. That is safe because the node is given
  nothing factual - no corpus, no tools, no record - and its prompt forbids stating any fact, so
  there is nothing a weaker model could get wrong; the tradeoff is tone, not correctness, and a
  small-talk call costs a fraction of a cent either way. It shares Haiku with the classifier but
  not the setting: the two jobs are judged differently, so each can be changed alone.
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

#### Phase 1c — Scheduling service and end-to-end booking
Stand up Scheduling as a separate FastAPI service with its own PostgreSQL, then wire the agent to it
so a patient can actually book an appointment by chatting. Rescheduling, cancellation, and
escalation are deliberately held back to 1d — this phase proves one write path end to end rather
than all of them at once.

**The service, built and tested standalone against its own contract first:**
- **Owns the whole scheduling domain** — patients, practitioners, and appointments — in its own
  database, referencing the core backend's data only by opaque id (see "The data boundary follows
  the invariants" above).
- **Integrity enforced in the database, not application code.** PostgreSQL exclusion constraints on
  interval/range types stop a patient *or* a practitioner from holding two overlapping appointments,
  and an appointment that falls outside its practitioner's weekly schedule is rejected at write
  time.
- **A practitioner** has a full name, a specialty, a weekly working schedule, and a fixed
  appointment duration (default 60 minutes) that every appointment with them uses.
- **A patient** is permanently one-to-one with a chat and carries a display name drawn from a
  seeded pool of long-dead, internationally recognized writers; practitioners are seeded from a
  comparable pool of historical physicians. Names are unique within a session — falling back to
  numeric suffixes once a pool is exhausted — but may repeat freely across sessions.
- **Everything is session-scoped**: an app user only ever sees the patients, practitioners, and
  appointments belonging to their own session.
- **gRPC API** — `CheckAvailability`, `BookAppointment`, plus the patient/practitioner lifecycle
  calls the core backend needs — with failure handling as part of the design, not an afterthought:
  timeouts, retries, and defined caller behavior when Scheduling is unreachable. Chat creation in
  particular never blocks on it: if Scheduling is down a visitor still gets a working (unnamed) chat
  and grounded FAQ answers, with the patient record created once it recovers.
- **A REST admin surface** for adding, editing, and hard-deleting patients and practitioners (no UI
  this phase), defaulting a new record to the next unused pool name. Deletes cascade: removing a
  chat removes its patient and that patient's appointments; removing a practitioner removes theirs.

**Wiring it to the agent:**
- **An in-process tool registry** — `list_practitioners`, `check_availability`,
  `book_appointment`, `list_my_appointments` — so agent logic stays decoupled from how each
  capability is implemented. The agent knows only tool names and JSON schemas; that a booking call
  becomes a gRPC round trip to a separate service is the handler's business alone, and swapping a
  handler for a different transport changes no agent code. **This replaces the MCP tool servers
  this phase originally called for.** MCP's added value over a registry is cross-process reuse by a
  third-party client, which nothing in this phase consumes, and standing up a server plus a client
  inside one process would add a loopback hop and JSON-RPC error plumbing between an agent and
  handlers already sharing an address space. The MCP transport moves to a later phase, where a
  second consumer can justify it.
- **A booking path in the graph** alongside 1b's FAQ path, so the intent classifier's `booking`
  label finally routes somewhere real, plus the two obvious read-only answers: which practitioners
  this session has, and what this patient has booked.
- **Parallel specialist nodes with a merge step** for mixed-intent messages ("what should I bring,
  and can I book Friday?"), **pulled forward from 1d**. Once 1c has two real specialists, routing an
  ordinary sentence carrying both intents to only one of them ships a visibly partial answer for the
  rest of the phase. A single-specialist turn does not pay for it: the sole specialist streams its
  own reply and the merge step is a no-op, so the FAQ path keeps its existing latency and behavior.
- **Times are plain local times end to end — the app has no concept of a timezone.** Everyone
  reachable from one session shares a single local time, so nothing is ever converted between zones
  and no zone identifier is stored anywhere. The assistant is told the patient's current local date
  and time so relative phrasing ("next Tuesday at 3") resolves against the right day, and schedules,
  slots, and confirmations are all the same plain local times.

**One consequence for the core backend:** a patient is one-to-one with a chat, so "add a patient"
means "add a chat" — 1a's single-chat-per-session model gives way to a real list of chats the app
user can switch between, and the current clear-the-chat action becomes a single delete that removes
the chat, its patient, and that patient's appointments together. This is the one piece of frontend
work in the phase.

##### Scheduling and End-to-End Booking: technology choices

`specs/005-scheduling-and-booking/` (Phase 1c) turns `services/scheduler` into a real
service and gives the agent its first write capability — full rationale and alternatives considered
live in [`research.md`](../specs/005-scheduling-and-booking/research.md):

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
  the Phase 1c section above was amended accordingly.
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

#### Phase 1d — Rescheduling, cancellation, escalation, and the staff console
Complete the agent's conversational surface on top of 1c's booking. Shipped in two parts, because
the two halves share nothing but the tool registry: the first changes appointments, the second
changes who is talking.

**Part 1 — rescheduling and cancellation** of an existing appointment, through the same tool seam
and the same database-level guards that protect booking. **Shipped** as
`specs/006-reschedule-and-cancel/`. Both halves of that sentence held literally: no new seam, and
no new guard mechanism — the same two exclusion constraints, now partial on a `status` column, so
a cancelled appointment stops occupying its slot at the datastore rather than by an application
filter. The agent gains its first *mutating* capabilities, and with them the first outcome the
system must admit it does not know: a write whose answer never arrived is reported as unknown,
never as "nothing happened".

**Part 2 — the escalation path** — adding `escalate_to_staff` to 1c's tool registry — which is where
conversation shape actually becomes multi-party: staff take over and post directly into the same
thread the patient sees (not a separate, assistant-mediated channel), so a conversation becomes a
flat, ordered log of messages from any sender (patient, assistant, or staff). Escalation is a
state on the conversation as a whole, not per-message: once escalated, the assistant stops
generating replies in it until a staff member resolves it or hands it back.

**Part 2 also carries the staff-facing interface, pulled forward from what an earlier draft of
this roadmap put in Phase 2**, together
with the two admin surfaces over data the assistant already depends on. The reason is manual
verification: a handoff to a human that no human can see, a practitioner list editable only with
`curl`, and an FAQ entry whose indexing state is invisible are all things that can be unit-tested
but not *exercised*. It is also what makes 1e's threshold work possible by hand — tuning an
abstention gate means editing the corpus and re-asking questions, repeatedly, in the real UI.
Mostly frontend over APIs that already exist (1c's REST admin surface, the existing FAQ CRUD), but
**not only** what the escalation path needs: two capabilities cross the service boundary as well.
The practitioner screen is proxied through the core backend, because the session that authorizes a
change lives in a cookie the browser cannot read; and resetting a demonstration needs a
`DeleteSession` rpc the scheduler did not have.

- **One screen, both sides.** The app demonstrates the patient experience and the staff experience
  at once: the session's patient chats on the left, the staff interface on the right, so an
  escalation can be watched arriving from the side that raised it. There is no login and no second
  kind of user — the anonymous session remains the only identity, and it owns both panes.
- **Escalated-conversation chat.** A queue of the session's conversations the assistant handed
  off, each opening into the same thread the patient pane shows, with a composer that posts into it
  as staff. The console owns the state transition Part 2 defines above: while a conversation is
  escalated the assistant stays silent, until the staff member resolves it or hands it back.
- **Staff notification.** An escalation is worth nothing if nobody happens to be looking at that
  pane. In-app first — a live push and an unread count on the staff side, raised by a turn the user
  may have been driving from the patient side a second earlier — because that needs no new
  infrastructure. Out-of-band delivery (email, SMS) is not planned. Phase 4a's Claude connector
  covers the time away from the console instead, by letting the staff member ask rather than
  pushing anything to them.
- **Practitioner management** — a UI over 1c's REST admin surface: add, edit, and delete
  practitioners, with the seeded-name defaults and the cascading deletes the service already
  enforces. No new backend.
- **FAQ entry management** — a UI over the existing FAQ CRUD, and the screen shows **no indexing
  state at all**, because that state no longer exists. A *saved* entry and a *searchable* entry
  used to be different states, kept consistent by a Postgres↔Qdrant ordering; 007 replaced that
  with additive chunk revisions, where the row names the one revision retrieval may search and an
  entry cannot be stored without one. Listed and searchable became the same fact, so an indicator
  could only ever read "yes" — and a signal that can never fire teaches a staff member to rely on
  a warning that would not come. It is still the one admin action that changes what the assistant
  will say, which makes it the natural place to show a retrieval or eval effect later.
- **The session stays the only boundary — there is no staff login.** A session gets exactly one
  staff member, created with it as its patients and practitioners already are, and the app user
  simply acts as that person. Scoping is unchanged from 1c: a session sees and manages only its own
  chats, patients, practitioners, and staff member, and an id from another session resolves to
  nothing. Authentication would buy nothing this scope does not already give, and would cost the
  side-by-side demonstration that is the point of the screen. The staff member is a core-backend
  record, alongside sessions, chats, and messages — nothing about it touches Scheduling's
  invariants.

##### Rescheduling and Cancellation: technology choices

`specs/006-reschedule-and-cancel/` (Phase 1d, part 1) gives the agent its first *mutating*
capabilities — full rationale and alternatives considered live in
[`research.md`](../specs/006-reschedule-and-cancel/research.md):

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

##### Escalation and the Staff Console: technology choices

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
  `GET /console/conversations`. This deviates from this phase's wording ("a live push") and is a
  correctness argument rather than a shortcut: every mark and every silence is *stored state*, so a
  poll reads the truth and self-heals — a dropped answer costs one interval of staleness. A dropped
  push leaves a pane wrong indefinitely with nothing to correct it, and being wrong about which
  conversation needs a person is the failure the whole surface exists to prevent. One endpoint
  serves both sides: the staff list renders it, and the patient pane refetches its open thread when
  that conversation's newest message advances past what it holds.
- **Two silences, two columns.** `escalated_at` answers *may the assistant speak*; `attention_since`
  answers *has a person acted*. They are cleared by different things and disagree in both
  directions — a failed tool call and a question the corpus cannot answer each emphasize a
  conversation without silencing it, and the console's switch ends a silence without clearing the
  emphasis, because taking a conversation is not answering it. Four causes silence — the patient
  asking for a person, and (since 009) an urgent condition, evident distress, and a booking for
  someone else; the other three raise attention and leave the assistant talking. One column
  carrying both axes passes almost every test and fails exactly those cases.
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

##### The Starter FAQ Corpus: technology choices

- **Planted at session creation, not answered from a shared corpus.** Every session owns its corpus
  and now starts with the clinic's own nine question-and-answer entries, written into that session
  exactly as if it had typed them into the console. This supersedes spec 007's FR-039b ("a new
  session's corpus MUST start empty… a starting template is deliberately deferred to later work") —
  the corpus is still per-session, so the reason that requirement existed is untouched: a visitor
  editing or deleting a seeded entry changes only what *they* are answered from. A single shared
  corpus behind a console with a delete button and no login is the design that stayed rejected.
- **All of it or none of it, and never at the cost of the chat.** The seeding follows the same shape
  a save does: every entry is chunked and embedded first, its chunks are written under a revision
  nothing yet names live, and one commit publishes all of them at once. So a failure at any step leaves
  the session with no corpus rather than part of one, and the cost is leaked chunks. It is also
  never fatal — `POST /chats` still returns a working chat when Voyage or Qdrant is unreachable,
  and the empty corpus that results is the state the rest of the system already handles: the console
  shows it as empty, and a question it cannot answer abstains and calls staff. The failure is
  logged as an operation that failed plus a critical dependency event, because nothing about an
  empty corpus read later says whether a session emptied it or a dependency was down.
- **One embedding call for the whole corpus.** `publish_revisions` chunks every entry, embeds all
  their chunks in a single Voyage request, then writes each entry's points. A first-time visitor
  pays one round trip on `POST /chats` instead of nine, and the single-entry save path is the same
  function called with a list of one — so there is one implementation of "chunk, embed, write", not
  two that can drift.

#### Phase 1e — RAG done properly
Upgrade Phase 0's naive embed-and-top-k retrieval into a pipeline with a defensible stage for each
job: chunking, retrieval, reranking.

- **A gated retrieval stage**, replacing Phase 0's naive top-k. It fetches a wide observation pool
  (25 by default) and keeps every chunk at or above a **minimum cosine similarity of 0.25** (0.3 until G081), up to the
  5 highest. The rest are logged with their scores and ignored, so the cap is a threshold that can be
  argued up or down against candidates something actually recorded. If nothing clears the floor the
  assistant cannot answer: it abstains immediately — no reranking call, no model generation.
  *(Shipped in `specs/008-reranked-retrieval-pipeline/`. This bullet was originally titled
  "defensible chunking", but its body always described retrieval, and chunking was deliberately left
  unchanged: 1e is a query-time change end to end, and nothing was re-indexed.)*
- **Reranking, so retrieval can cast wider while the prompt carries less.** Vector search is a
  bi-encoder: a chunk is embedded at index time knowing nothing about the question, so cosine
  distance between two independently placed points is a blunt relevance signal — measured on the
  current corpus, two chunks answering *different* questions already score 0.57 against each other.
  A reranker is a cross-encoder, scoring query and chunk together: far more accurate, and far too
  expensive to run over a corpus, so it re-orders a shortlist the cheap retriever produced. The
  pipeline becomes retrieve wide for recall → rerank → keep the best few for precision, with both
  the prompt context and the citations built from the survivors — so a citation comes to mean "this
  is what the answer stands on" rather than "this was nearby". Reranking results are sorted by
  cross-encoder relevance score and have two caps: a **minimum rerank score of 0.58** and **3 chunks
  maximum**. If nothing clears them the assistant cannot provide a grounded answer — it abstains
  immediately, with no model generation. *(0.58 is measured, not chosen: see the sweep in
  `specs/008-reranked-retrieval-pipeline/calibration/questions.md`. The two classes overlap, so no
  floor separates them cleanly, and every value from 0.520 to 0.636 scores identically on the
  calibration set — 0.58 is that band's midpoint.)*
- **Fallback** — if the reranking model is not available then an error is logged and the answer is
  provided from the cosine step only: the ≤5 chunks that cleared the similarity floor, rather than
  the ≤3 the reranker would have kept. The turn records `answered_unreranked`, so a degraded answer
  is never counted or displayed as a reranked one, and no staff are called — a dependency outage is
  not a corpus gap.
- **Citations back to the source document**, derived structurally from the chunks actually placed in
  context, never self-reported by the LLM. Only the chunks that survived the caps are used for
  generation and reported as citations — the two sets are identical by construction. They are
  rendered in the staff console alone; the patient pane shows the answer, not the clinic's working
  notes underneath it.
- **Every step is logged** — it must be possible to investigate later which chunks passed at every
  step, their scores, and what was decided. Six events carry that: `faq.retrieval_completed`,
  `faq.similarity_gate`, `faq.reranking_completed`, `faq.reranking_unavailable`, `faq.rerank_gate`
  and `faq.verdict`, with each gate reporting what it dropped **by the floor** and what it dropped
  **by the cap** separately — one says the bar is too high, the other that it is too low. The same
  logs are the input Phase 2b computes its metrics and threshold adjustments from; their field
  contract is `specs/008-reranked-retrieval-pipeline/contracts/log-events.md`.
- The turn attribute "Grounded" has no sense any more. If the FAQ node provides an answer then it's
  always grounded. If it can't then it should explicitly run the abstention path and call staff.
  *(Shipped as a six-value `FaqVerdict` rather than a removal: `answered`, `answered_unreranked`,
  and one value per stopping point for the four abstentions — an empty corpus, a search that matched
  nothing at all, nothing clearing the similarity floor, nothing clearing the rerank floor. All four
  abstentions are identical to the patient and to staff; they differ only in the record, which is
  what says whether to add entries, re-index, or move a floor.)*

#### Phase 1f — Small talk, and what escalation is actually for
Not every message is a request. A greeting, an acknowledgement, a thank-you or a reaction asks for
nothing, and today each one takes the FAQ path, abstains, and calls a human — so staff are paged by
"Thanks". Escalation loses its meaning if it fires for messages that had nothing to escalate.

- **A `small_talk` intent and a node to answer it.** One more label in 1b's structured-output enum —
  same cheap model, same single call — routing to a specialist that does no retrieval, calls no
  tool, cites nothing, and returns one short reply in the voice of a polite clinic receptionist.
- **The discriminator is whether the message asks for anything**, not whether it is short or
  friendly. "Hi", "I see", "OMG", "Let me think" ask for nothing; "Hi, I need to cancel tomorrow" is
  a booking message wearing a greeting. When the two readings are both plausible, the message is
  *not* small talk — answering a real request with "You're welcome!" is the worse failure, and the
  FAQ path already knows how to abstain.
- **The same word means different things at different points in a conversation**, so the label is
  read against history, which the classifier already receives. After "Please arrive 15 minutes
  early", "OK" is small talk. After "9am Monday with Dr. Vesalius — shall I book it?", "OK" is a
  booking confirmation.
- **Small talk is dropped whenever any other intent is present.** A specialist answering the real
  request absorbs the pleasantry; a merge step that stitches "You're welcome!" onto a booking
  confirmation buys nothing and adds a path to test. Small talk is the label for a turn that carries
  *only* small talk.
- **The reply is generated, but constrained**: it may not promise a callback, quote a policy, state a
  time, or imply an appointment exists. It has no retrieval behind it, so anything factual in it
  would be confabulated by construction.
- **Escalation is narrowed to its three real causes** — the patient asked for a person, the patient
  asked for something the assistant cannot provide (an out-of-scope request, or a corpus that cannot
  answer), or something failed. A message that requests nothing is none of those and calls nobody.
  `not_authorized` stops meaning "try the FAQ path and see": a request the assistant has no path
  for is escalated deliberately — told in fixed text that a person now has it, without silencing the
  assistant — and a non-request is answered. The middle cause is recorded as two, a corpus gap and a
  request the assistant is not authorized to serve, because one is fixed by writing an FAQ entry and
  the other by nothing.
- **Three situations stop the conversation instead of answering it** — an urgent condition, evident
  distress, and a request to book on another person's behalf. Each calls staff under its own cause,
  shows that cause beside the message in the console, silences the assistant until a person acts,
  and answers the patient with one fixed sentence. Distress is the deliberate exception to the rule
  above: a frightened patient often asks for nothing, and that is exactly when a person is needed.
  The assistant does not triage — recognizing these is a routing decision, and the urgent reply
  points at emergency services rather than assessing anything.
- **Phase 2 gets the cases to measure it with** — small-talk turns in the golden set, and one metric
  that reads directly on this phase: the share of escalations raised by turns containing no request,
  which should be zero.

#### Phase 1g — One message, several requests
*(Shipped in `specs/010-multi-request-turns/`.)*
A patient's sentence is a container, not a unit of work. "What's your address and what should I
bring?" is two questions; "What's your address and what dentist slots are free tomorrow?" is two
requests for two different specialists. Today the whole message is the retrieval query *and* the
whole message is what each specialist is told to answer, and three separate failures fall out of
that one fact: a compound question embeds to a point between both answers' chunks and clears
neither gate, the FAQ node abstains on a booking clause no corpus could ever answer and pages a
human for it, and the booking node — asked to answer the last message — writes the policy half out
of nothing.

- **The classifier returns requests, not labels.** Same single call, same cheap model, same
  structured output: its schema becomes a list of `{intent, text}` segments, and 1b's list of
  intents becomes the set of their labels — so the router's existing selection keeps working
  unchanged. *(`intents` survives as a derived property of the segments, which is what let five
  routing rules go untouched.)*
- **A segment is a standalone restatement, not a substring.** "Do you have parking, and is it
  free?" splits into a second half that retrieves nothing on its own. The segmenter resolves
  pronouns and ellipsis against the message and the history it already reads, so every segment
  stands as a question by itself.
- **Split by answer.** One request stays one segment; a message splits wherever the parts are
  independently answerable, even inside one sentence ("hours and locations" is two), since a
  question split into two FAQ halves is still answered whole in one reply, and the count is capped (3 to start) so a rambling message cannot fan out without
  bound. *(The cap cannot be put in the schema — the API rejects array bounds in a
  constrained-output schema — so it is stated in the prompt and enforced on arrival: an over-long
  result is rejected and the turn falls back to the whole message, never trimmed. A four-request
  message is the one case the shipped classifier does not combine, and it stays the only evidence
  the cap can be argued against: `specs/010-multi-request-turns/evaluation/procedure.md`. Not 2a's
  golden set — that carries no over-cap case, by the decision recorded in its `PROVENANCE.md` — so
  raising the cap would still be tuning to a sample of one.)*
- **Each specialist reads only its own segments.** This is the whole of the fix for the
  mixed-intent failures: the FAQ node never sees the booking clause, so it cannot abstain on it,
  and the booking node never sees the policy question, so it cannot answer it. The booking prompt
  says so explicitly as well — it holds no clinic knowledge and answers nothing outside its
  segments — because the input slice and the instruction fail independently.
- **The retrieval pipeline runs once per FAQ segment, concurrently — never pooled.** Both 1e gates
  and both caps apply per segment: a single shortlist shared by two questions re-creates the
  original defect, with the stronger question's chunks crowding the other out under the 3-chunk
  cap. Citations are derived per segment and deduplicated at the merge.
- **The routing rules of 1c and 1f survive as they are**, now read per segment: `call_staff` still
  takes the whole turn, small talk is still dropped whenever any segment is a real request, and a
  `not_authorized` segment is still escalated deliberately.
- **A single-request message pays nothing.** One segment means one specialist, no merge and no
  composing call — the existing path, byte for byte. *(The routing-time flag that used to answer
  both "do the specialists stream?" and "does the composer merge?" was split in two: the second is
  now decided from the parts that actually exist, because a two-question turn whose FAQ half
  abstains collapses to one part — a constant sentence, which no composing call should paraphrase.)*
- **Every step is logged per segment.** 1e's six FAQ events gain the segment they belong to, so
  eight events from one turn are attributable to the question that produced them, and the
  classifier logs the segmentation it chose. *(By the request's position in the message, bound with
  `structlog.contextvars` for its whole run; the request's text is carried once, on the
  classification event.)*

##### One Message, Several Requests: technology choices

`specs/010-multi-request-turns/` (Phase 1g) makes the **request**, not the message, the unit
of work: the classifier returns an ordered list of `{intent, text}` segments, each specialist reads
only its own, and the retrieval pipeline runs once per question. Three choices carried a real
tradeoff.

- **The fan-out lives inside the FAQ node, not in the graph.** LangGraph's `Send` would give a node
  instance per question — and per-question node spans for free — but it is *n* branches writing one
  state key, which needs a channel reducer. The graph's standing rule is that every specialist writes
  a *disjoint* key precisely so no reducer is needed, and retiring that rule to fan out one
  specialist is a large change for a small gain. So the concurrency is `asyncio.gather` inside
  `answer_faq`: one node, one key, one write, and the routing table, the conditional edges and the
  merge detection are all untouched. The cost is that the fan-out is invisible in the graph's shape —
  paid back by the per-request `segment` field on every retrieval event, which is what makes two
  interleaved pipelines readable in the log.
- **One generation call per question, not one over every question's shortlist.** A single prompt
  carrying two questions and two shortlists is cheaper — one call instead of up to three — but it is
  the same shape as the single *query* covering two questions that this phase exists to undo, and it
  leaves "this answer used only its own evidence" resting on the model's obedience. Answering each
  question from its own shortlist alone makes that structural: another question's chunks are not in
  the prompt to be cross-wired. The cost is bounded by the cap at three generation calls plus one
  composing call, and only a message that genuinely carries several questions pays it.
- **The cap of three is stated, not encoded — because it cannot be encoded.** The intent was to make
  an over-long segmentation unrepresentable, via `minItems`/`maxItems` in the classifier's
  constrained-output schema. The Claude API rejects array bounds in such a schema
  (`For 'array' type, property 'maxItems' is not supported`), which only a live call revealed — every
  unit test stubs that call. So the cap is stated in the prompt and in the schema's field
  description, and enforced *on arrival*: an over-long result is rejected as an invalid
  classification and the turn falls back to the whole message on the FAQ path, exactly as it does for
  any other classification failure. Nothing the patient asked is ever trimmed away. The measured
  consequence is recorded with the labelled sets: a four-request message is the one shape the shipped
  classifier does not combine, and it is the data the cap of three is to be revisited against.

#### Phase 1h — Answer what you can
*(Shipped in `specs/011-answer-what-you-can/`.)*
*(1g stopped exactly here: the turn kept one verdict, and its FAQ half abstained as a whole if any of
its requests could not be answered — no answered half was delivered beside a gap. Each request's own
outcome was already in the log, which is what 1h moved onto the record.)*
Once a turn carries several requests, one verdict for the turn is a value with two meanings: "the
address question was answered" and "the what-to-bring question was not" cannot both be `answered`.
This phase makes the turn report each request's outcome and serve the ones it can.

- **The verdict moves to the request, and nothing stays behind.** 1e's `FaqVerdict` becomes the
  request's, and the turn keeps no verdict of its own — not even a derived summary. A summary is the
  right shape for a log line and the wrong shape for the record: a field that is only sometimes right
  is read as the answer by the first reader who does not know it is a summary. Citations move with
  it, so a citation says which question it supports rather than which turn it came from. *(One
  `messages.request_outcomes` JSONB column — `{position, question, answer, verdict, citations}` per
  request, ordered — replaces `messages.faq_verdict` and `messages.citations`, and the same list is
  what `ChatDoneEvent`, `MessageOut` and `turn.completed` carry. `summarize_verdict` is deleted;
  `turn.completed`'s `outcome` now names the turn's **shape**, and its `answer_source` is gone with
  the duplication that created.)*
- **A turn answers what it can and abstains only where it must** — the answerable half is delivered
  with its citations, and the gap is named as a gap in the same reply. *(`FaqResult.from_segments`
  no longer collapses the half; the FAQ half contributes one part per answered request plus exactly
  one for the gap, which keeps `_actual_parts` bounded by the routing-time `_expected_parts`. A turn
  whose every request abstained still reaches the constant abstention message through the existing
  collapse, with no composing call.)*
- **Escalation carries the unanswered question, not the message.** Staff receive the specific
  request the corpus could not serve, verbatim; one escalation per turn however many segments
  failed. *(`agent/escalation.py` is unchanged and stores nothing new: the unserved requests are
  **derived** from the reply's outcomes at render time, and the staff console draws one block per
  request — its question, its citations, or a line saying it was not answered and went to staff.)*
- **The composer must not let the halves bleed.** An abstention may not be softened by an answered
  segment beside it, and an answered segment may not be extended to cover the gap — the same
  "preserve every claim exactly" constraint the merge already carries, now with abstention as a
  claim. *(Three clauses in the composing system prompt, plus one gap block naming every unanswered
  question and restating the "in your own words, do not quote" rule where the questions themselves
  are. Being model-obeyed, their effect is measured by hand against committed data —
  `specs/011-answer-what-you-can/evaluation/` — and made checkable afterwards from the stored parts
  and reply, which is the record-based check in that procedure.)*
- **Phase 2 gets the cases to measure both phases** — golden-set messages carrying two and three
  requests with a labeled expected segmentation, segmentation accuracy as a metric, and two that read
  directly on this work: the share of *answerable* requests a turn left unserved, and the share of
  abstentions on questions the corpus demonstrably answers. Both should be zero. The first of those
  replaces the metric this bullet used to name — "escalations raised by a message that also contained
  an answerable question" — which does not survive the phase it was written for: after 1h an
  escalation is raised *for the requests that failed* and carries them, so a mixed message still
  raises one, and a count of those would read nonzero on turns that behaved exactly as designed. What
  is worth counting is the answerable request that went unserved, which is the thing partial serving
  can actually get wrong.

##### Answer What You Can: technology choices

`specs/011-answer-what-you-can/` (Phase 1h) moves the verdict, the answer and the citations
**onto the request**, and lets a turn deliver the requests it could answer beside a named gap for
the ones it could not. Four choices carried a real tradeoff.

- **One JSONB list on the message, not a `message_request_outcomes` table.** A table buys
  per-element referential integrity and makes "how often did a turn serve half of what was asked?"
  a SQL question. It also puts a join on every history read, for queries this phase does not make:
  the outcomes are always read *with* the message that carries them — the console renders them under
  a reply — and every aggregate question is answered from the log, which is where Phase 2 reads.
  That is the profile `citations` and `reply_to_message_ids` already have on this table, and both
  are JSONB for the same stated reason. A list also enforces order structurally, which is a
  requirement rather than a convenience. The moment something aggregates outcomes in SQL, the table
  becomes right; that is Phase 2's, and Phase 2 reads logs.
- **The turn-level summary is deleted, not kept "for the log".** `summarize_verdict` reduced one
  verdict per request to the single value the turn reported. Keeping it as the log's summary was the
  obvious way to leave existing queries working — and would have put the value with two meanings
  back one layer down, where a reader has no way to know it is a summary. So the reduction is gone,
  `turn.completed` carries one entry per request, and `outcome` names the turn's *shape* rather than
  a verdict. The cost is paid once, by the queries in
  [`contracts/log-events.md`](../specs/011-answer-what-you-can/contracts/log-events.md), which had to
  be re-pointed in the same change. `answer_source` left that event too: once `outcome` names the
  shape, the two carried one fact, and a contract saying "these two are always equal" is a contract
  nobody re-checks. The wire keeps `answer_source` — a client reading one event asks a different
  question than a log reader scanning a million.
- **The gap is one reply part however many requests fell into it.** One part *per* unanswered
  request is the simpler rule and reads worse: a reply that repeats the same refusal three times
  reads as three failures rather than one gap. It would also break the bound the routing-time
  streaming decision rests on — with *k* requests of which *m* abstained, actual parts are
  `k − m + 1`, never more than the `k` the router counted, which is what keeps a specialist from
  streaming a reply the composer then writes over. The cost is that the composer has to name several
  subjects in one paragraph, which is why the gap block lists each question and the system prompt
  carries three constraints about what may not happen to them.
- **One migration, no backfill, and the downgrade restores no data.** The safe-looking alternative
  is a data migration writing `{position: 0, question: null, …}` per existing row. It would invent
  the one field it cannot know — the classifier's restatement was never stored — leaving a nullable
  `question` for every future reader to branch on, permanently, to describe rows that no longer
  exist. So every session was deleted first (verified empty in `visitdoc_chat`,
  `visitdoc_scheduler` and Qdrant), the revision drops two columns and adds one, and no code path
  may read the old shape. The cost is that the change is only legal on a store you are willing to
  empty — which is exactly what a portfolio project's demo data is, and what a real one's would not
  be.

### Phase 2 — Evaluation & observability
The centerpiece — the ability to *measure* whether the system works, not just demo that it does.
Split into subphases, the first three strictly consecutive: there is nothing to compute a
metric over until the labeled set exists, and nothing to compare between versions until the metrics
do.
Tracing is last because nothing in the eval chain depends on it — the metrics are computed from the
per-request record 1h moved onto the message, not from a trace — so it is the one piece that can
move if something else needs the slot.

#### Phase 2a — The golden dataset
*(Shipped as `evals/golden/`: 135 labelled messages carrying 190 requests, with `schema.json`,
a pinned `corpus.json`, and `PROVENANCE.md`. Data only — no runner, and nothing imports it yet.)*
Realistic patient messages labeled with expected intent(s), expected tool calls, and (for FAQ) the
correct source document. **The label attaches to a request, not to a message**, following 1g and 1h:
a message carries an expected segmentation — how many requests, in what order, and each one's
intent — and the retrieval and source-document labels hang off the individual request. Labeled
per message, the set could not express the traffic those two phases exist to serve, and would have
to score a turn that answered one of two questions as either wholly right or wholly wrong.

- **It lives outside `specs/`**, at `evals/golden/`. The four seed sets belong under `specs/` and
  stay there — frozen records of a shipped phase, excluded from ruff and mypy with the rest of
  `specs/**`. This set is the opposite kind of thing: 2b reads it, 2c compares one build's run
  against another's over it, and it is re-labelled whenever the corpus moves. Living, checked, and
  outliving the phase that introduced it.
- **The label says what the system may emit, not everything a scorer might want.** `intent` is
  scored against `IntentLabel`'s eight values and array order *is* position, so no `position` field
  exists to disagree with it. A request's restatement is carried as an unscored `gist`, because a
  segment has many valid wordings and exact-matching one would measure phrasing. Answerability is
  **two-valued**, not `FaqVerdict`'s six: the four abstentions are identical in behaviour, nothing
  may branch on which one it is, and no hand-labeller can tell a similarity stop from a rerank stop
  without running the pipeline. Tool labels are a required subset, not a sequence.
- **Nothing is stored that the requests already say.** No case names its expected escalation — the
  causes follow from the request intents by a table, the mark from `_PRECEDENCE`, and silence from
  `_SILENCING`. `assistant_failed` is the one cause no case can carry: it is a property of a broken
  run, not of a message.
- **The corpus is pinned**, with a `sha256` over the entry texts, because every source-document
  label is meaningless without the text it names. `DEFAULT_FAQ_ENTRIES` changed three times between
  1h shipping and the set being written and two of those changes moved labels — a referral answer
  that widened from "a specialist appointment" to "any of our practitioners", and a parking line
  that became "fee parking" and so answered "is it free?". When the hash stops matching, the labels
  on the changed entries are stale until someone re-checks them.

Consolidating the seed was the first task and, as intended, the first real test of the per-request
label shape. Four things did not survive it, each the same defect in a different set: 1h's sets
filed *unauthorized* requests as "unanswerable", though a prescription renewal is not a corpus gap
and no entry will ever fix it; 1f labelled whole messages with one intent where two of them carry
two requests; 1g labelled segment *counts* with no intent per segment, and those had to be labelled
by hand rather than lifted from the committed classifier output, which would have made segmentation
accuracy unfalsifiable; and 1e scored one question against one entry where there were two requests
sharing it. `PROVENANCE.md` records all four, plus what was left behind and why.

Two decisions the phase surfaced rather than settled. The count is **135**, not the 50–100 this
bullet used to name: that band was written before the seed was counted, and consolidating four
phases' data without discarding distinct cases overshot it. And the one message carrying more
requests than 1g's cap of 3 allows was **dropped by decision**, so nothing in the set now bears
on whether that cap is right — `specs/010-multi-request-turns/evaluation/` is still where that
datum lives.

#### Phase 2b — Metrics over the labeled set
**Metrics**, computed per request wherever a request is what the system decides about:
intent-classification accuracy and **segmentation accuracy** (against 1g's labels — the expected
number of requests, their order, and each one's intent), tool-selection correctness, retrieval
hit@k / MRR per request, and **end-to-end task success** (did the booking land in the correct
database state?). Two more read directly on 1g and 1h: the share of answerable requests a turn left
unserved, and the share of abstentions on questions the corpus demonstrably answers, both of which
should be zero. All of them are computed from the per-request record 1h moved onto the message —
after 1h there is no turn-level verdict left to compute them from, which is the point: a metric
averaged over a summary field cannot say which of a turn's requests was the one that failed.

##### Metrics Over the Golden Set: technology choices

`specs/012-golden-set-metrics/` (Phase 2b) turns 2a's labels into numbers: a new workspace
member, `evals/harness` (`golden-harness`), drives every golden case through the running stack as a
real turn, stores what it left behind, and scores each request against its label. How to run it,
and what each exclusion means, is in [`evals/harness/README.md`](../evals/harness/README.md). Six
choices carried a real tradeoff.

- **The seam is the published HTTP surface, not an in-process call to the graph.** Calling
  `run_turn` directly would be faster, need no running service, and hand the harness every event as
  a Python object. It would also score what the graph *yielded* — the same data one layer before
  anyone persisted it — so a bug between the graph and the stored row would be invisible to every
  metric, and the escalation writes and silencing rules several families of the set exist to
  exercise would never run. So a case is `POST /chat` and a read of the thread, exactly as a browser
  does it. The cost is a stack that has to be up, events that have to be read back out of a log
  file, and two writes no published surface offers: a case's prior turns are planted straight into
  the chat database (no endpoint posts as the assistant, and the console posts as staff), and a
  chat's scheduler patient is read from it.
- **A JSON renderer in `shared-logging`, not a parser for the console output.** Hit@k and MRR are
  questions about the candidates retrieval *ranked*, which only the log carries, and the produced
  segmentation is only on `intent.classified`. The console renderer writes every value as a Python
  `repr`, and parsing it was tried against real lines: plain data round-trips, but a nested enum
  renders as `<IntentLabel.FAQ_QUESTION: 'faq_question'>` and a datetime as `datetime.datetime(...)`,
  neither of which `ast.literal_eval` accepts — and the same enum renders differently at different
  nesting depths. Beyond the mechanics, a development renderer carries no format guarantee, so a
  structlog release could have moved every retrieval metric and read as "retrieval got worse". The
  renderer is chosen by `LOG_FORMAT` (default `console`) and sits last in the chain, after
  redaction, so the security control is upstream of the change. The cost is a setting the harness
  depends on and cannot set: the service has to be started with `LOG_FORMAT=json`, and a run stops
  before its first turn when it was not.
- **One file per case, not one aggregate run document.** An aggregate is one thing to read, copy and
  commit. Written as a run progresses, though, it is a half-written document the moment a run is
  interrupted, and resuming means parsing it to find where it stopped. Per-case files make resume a
  directory listing, confine a failed write to one case (each is written atomically), and make the
  run what scoring needs it to be: a set of files that are the whole input. The cost is that a run is
  a directory of up to 135 files plus `run.json`, and the committed record carries all of them.
- **The corpus pin is re-taken with its construction written beside it.** `corpus.json`'s original
  `sha256` was "over the entry texts" and matched none of seventeen plausible constructions, although
  the texts themselves had not moved. Brute-forcing further was unbounded and would still not have
  made the algorithm *documented*; dropping the pin would have left every `cites` label naming text
  nobody checks. So the new value names its construction in an `algorithm` field, a test recomputes
  it from `DEFAULT_FAQ_ENTRIES`, and a run verifies the live corpus against it before its first turn.
  The cost is that the old value survives only as unverifiable history in `PROVENANCE.md`.
- **A run's conditions come from a `service.configured` startup event, not the harness's own
  environment.** Reading `.env` in the harness would have needed no change to the service. It would
  also describe the wrong process, silently: a service started with an overridden floor, or still
  running last week's build, would be recorded under settings it was not using, and the
  classification, generation and embedding models appear on no per-turn event at all. So the chat
  service logs its models, thresholds, caps and limits once at startup, and the harness takes the
  latest such event before the run — and stops if one inside a later turn states other values. A
  settings endpoint was the alternative, and a new published surface for what one log line carries.
  The cost is a second change to the service this phase measures, and a set of field names that are
  now a data contract with a reader in another package.
- **Cases run strictly one after another, with each case's appointments cancelled before the next.**
  Running cases concurrently is the obvious answer to a full pass being slow. But every case shares
  one session — which is what lets the corpus be seeded and verified once — and the session is the
  scope the scheduler enforces its practitioner constraints over, so two booking cases in flight
  could contend for one slot and report a scheduling conflict as a booking-loop failure. Sequence
  also makes a turn's log slice exact by byte offset. A session per case would remove the contention
  and pay a corpus seeding and verification per case. For the same shared-calendar reason, once a
  case's post-state is stored the harness cancels whatever that patient holds standing, through the
  scheduler's own `CancelAppointment` — every case with a patient, since a misrouted turn can book
  too. A cancelled appointment leaves the partial exclusion constraint, so the slot is free at the
  datastore and no filter has to agree. The costs: a full pass takes as long as 135 turns in a row,
  which is what a full comparison run costs whenever 2c takes one, rather than something this phase
optimizes; the
  scheduler keeps a `cancelled` row for everything a run touched; and a cleanup that cannot complete
  stops the run, because every later case would be measured against a calendar it no longer
  controls.

#### Phase 2c — Comparing one version against another
2b can say what a build scores. It cannot say whether a change made things better, which is the
question actually asked while working: *I adjusted the rerank floor / the composing prompt / the
classifier — what moved?* This phase answers it with **a command a developer runs deliberately,
against two stored runs**, not with a build gate.

**The per-commit CI gate this phase used to call for is dropped.** A gate on every push would drive
135 live turns per commit, on the slowest path the system has, to decide red or green from numbers
that differ run to run on an unchanged build — so it would spend the most per decision exactly where
the decision is least trustworthy, and the first flaky red would teach everyone to re-run it. CI
keeps what it holds today: the unit tier, which includes the harness's own tests
(`docs/testing-strategy.md`). Nothing about this phase is an argument that a gate is wrong forever —
it is an argument that a gate needs the noise band below, and that the tool is useful long before
the gate is.

- **One command over two stored runs.** `make eval-compare BASE=<run_id> NEW=<run_id>` reads two run
  artifacts and prints what moved: each metric's before and after with both numerators and
  denominators, and the movement's sign. Comparing is, like scoring, **a pure function of stored
  runs** — no stack, no model call, nothing to pay for — so 2b's purity rule (`scoring` may not
  import `httpx`, `grpc` or the driver, enforced by `test_purity.py`) extends to it. Taking the new
  run is the only part that costs anything, and that is `make eval-run`, unchanged.
- **The per-case movement is the output that matters, not the delta.** An unserved-answerable share
  going 6/87 → 7/87 can be one case degrading, or two degrading while one improves, and only the
  second is worth a morning. So the comparison lists the cases that *changed*: each request whose
  verdict moved, each turn whose segmentation or intents moved, each booking whose tool selection or
  final database state moved — with the direction, by case id. A metric that did not move but whose
  cases churned underneath it says something a delta of zero hides.
- **A difference in conditions explains a difference in score, and must never be read as one.** Both
  runs record the corpus hash, the model ids, the floors, caps and pool size exactly as the chat
  service stated them (2b's `service.configured` contract). The comparison reports a condition delta
  at the top, before any metric, and every number below it is read in that light — because when the
  change under test *is* a threshold move, the conditions are supposed to differ, and when it is a
  prompt change, a differing model id means the comparison is measuring the wrong thing. Changed
  **labels** are the one hard stop: 2b already refuses to score a run whose scored-field digests
  moved, and a per-case comparison across a re-labelled set is comparing two different questions.
- **A narrowed run compares only what both runs drove.** `make eval-run CASES=…` during an
  investigation is the cheap loop this tool has to serve, so a 12-case run compares against those 12
  cases of a full-set baseline, with the restriction stated in the report. Silently comparing 12
  cases' aggregate against the whole set's would be the "one value, two meanings" defect in numeric
  form.
- **The noise band is measured before any movement is called a regression.** The first task of the
  phase is the one 2b deliberately did not do: run the set several times against an unchanged build
  and record the per-metric spread. Until that exists the tool reports *movement* and says so
  plainly; once it exists a movement can be marked inside or outside the observed band, and only
  then does "regression" mean anything. This is also what a gate would need first, which is why the
  gate cannot be the phase's opening move.
- **What a baseline is, and where it lives.** 2b committed exactly one run —
  `specs/012-golden-set-metrics/evaluation/` — as evidence rather than a threshold, and kept every
  other run local under `.run/evals/`. That committed run is the first baseline by default;
  promoting a later one stays a deliberate act with a reason given, not something a passing
  comparison does on its own.

A note for later, not a commitment: the comparison being pure and free means *it* could run in CI
over two artifacts at no cost — what cannot run there is producing the new artifact. So if a gate
ever returns, the shape is a scheduled or manually-dispatched run that publishes an artifact, with
the comparison as the cheap half. That is a later decision, with the noise band in hand.

##### Comparing Runs: technology choices

`specs/013-compare-eval-runs/` (Phase 2c) adds two offline commands to the harness:
`make eval-compare` says what moved between two stored runs, and `make eval-band` measures how much
those numbers move across five full runs of one unchanged build. Neither drives a turn, spends a
model call or needs anything running. How to run them is in
[`evals/harness/README.md`](../evals/harness/README.md). Five choices carried a real tradeoff.

- **The comparison re-scores both runs rather than parsing their stored reports.** Reading the two
  `report.json` files is what a person does by hand today and would have been the cheapest thing to
  build. It also freezes the report schema for the life of the project — `Report` and everything
  under it is `extra="forbid"`, so the first field this phase adds makes the record committed under
  `specs/012-golden-set-metrics/evaluation/` a document the current code cannot load — and it
  cannot answer the narrowed case at all: against a four-case run, the baseline's published 6/87 is
  not the honest number, and only recomputing over the four cases both runs recorded produces one.
  2b's own SC-003 guarantees re-scoring reproduces what the stored report says, so this is the same
  opinion recomputed rather than a second one. The cost is reading 2.5 MB twice per comparison,
  which at about 0.1 s a run is not a problem to solve.
- **`score_run` split into a pure `score(...)` and a thin writing wrapper.** The existing entry
  point writes `report.json` and `report.md` beside the run it scored, so comparing against the
  committed baseline *where it sits* — which FR-002 requires, and which is the whole reason the
  baseline is not copied around — would have silently rewritten a frozen record. Copying each run
  to a temporary directory first was the alternative and makes a read cost a 2.5 MB copy while
  leaving the writing behaviour in place for the next caller. A `write=False` flag was the other,
  and a boolean that decides whether a function has a side effect is the shape this project treats
  as a smell. Two functions say it plainly. The cost is one change to 2b's shipped code, with no
  behaviour change: same inputs, same `Report`, same bytes from the same caller.
- **An observed range over five runs, not a statistical interval.** A per-metric standard deviation
  is one number instead of two and is familiar. Five samples give it an enormous error of its own,
  and a reader who sees `σ = 0.01` will treat it as a distribution parameter — so the band publishes
  the five values, their lowest and highest, and per varying case how often each outcome occurred,
  and every rendering that cites it says it is five observations and not a confidence interval. The
  frequency count is what five runs buy over the three first proposed: *"G024 answered in 4 of 5,
  abstained at the rerank floor in 1"* is the sentence that tells a flaky case from a stable one.
  The cost is five full runs — roughly an hour and five times a run's model spend — for a range that
  supports no significance claim.
- **A constant exit code, and no `--fail-on-regression`.** The exit code is the one surface a CI step
  attaches to without anyone deciding to build a gate, and a gate here would decide red or green
  from numbers that move on their own, at 135 live turns per push. So the command exits zero
  whatever it finds, including a movement outside the band, and offers no flag that changes it;
  *refusing to compare* — mismatched labels, no common cases — is a different thing and follows the
  CLI's existing reported-failure path. The cost is that adding a gate later means writing one on
  purpose, which is the point.
- **A sibling `comparison/` package with its own purity test, not an extension of `scoring/`.** Both
  are pure and offline, so folding the comparison into `scoring/` was tempting. They answer
  different questions — one turns a run into metrics, the other turns two scored runs into movements
  — and keeping them apart is what keeps a failing test informative: one in `scoring/` says the
  metrics are wrong, one in `comparison/` says the reading of them is. 2b's purity test globs
  `scoring/*.py` and is not recursive, so the new package inherits none of its protection and gets
  its own copy rather than a widened glob: one test file silently governing two packages is exactly
  what leaves a third one unguarded later. The cost is a second test file that must be kept in step
  with the first.

#### Phase 2d — Tracing with Langfuse
Langfuse for per-step latency, token cost, and the full decision trace for each turn. This is the
observability half rather than the evaluation half: it answers *why* a turn went the way it did,
where 2b answers how often turns go the right way across a labeled set.

- **Langfuse Cloud's free Hobby tier, not a self-hosted instance.** Self-hosting Langfuse is not one
  container but six — web, worker, PostgreSQL, ClickHouse, Redis and S3-compatible storage — which
  would triple the local stack for a phase whose output is a debugging view, against the "thinnest
  backend" principle. Hobby's limits fit the use: 50k units a month (a unit is one trace, span,
  generation or score, so 6–23 per turn as measured in `specs/014-langfuse-tracing/evaluation/`),
  30 days of history, two users. The 30-day window
  costs nothing the eval chain needs, because 2b and 2c read stored runs, never traces.
- **Traces reach Langfuse over OpenTelemetry, not through the logs.** The Langfuse SDK is an
  OpenTelemetry tracer exporting over OTLP; structlog stays exactly as it is, and the harness keeps
  reading `.run/chat.log`. Each graph node is spanned by the graph's own `node_span`, which already
  bounds the node for its lifecycle log events, rather than by Langfuse's LangChain callback handler.
  The handler was the first plan and was measured before it was dropped: it runs on an executor
  thread under a copied context, so nothing opened inside a node can nest under the node's span and
  the trace comes out flat; it sees none of the direct Anthropic calls, which is every call this
  agent makes; and it would add `langchain` as a dependency only to produce a span `node_span`
  already has. Beneath the nodes, Claude calls are recorded as generations carrying model id and
  token usage, each tool call as its own observation, and each FAQ request's retrieval steps —
  embed, search, both gates, rerank, verdict — as spans. Where a step's decision is also a log
  event, the span's output is the very payload that event was logged with, so the trace and the
  log cannot disagree about it.
- **Patient text leaves the machine, so masking is designed in, not added later.** A trace carries
  full messages and prompts to a third party, and the redaction in `shared-logging` is a structlog
  processor that never sees a span. So that same redaction, made public in `shared-logging`
  rather than copied, is applied to every span: its key-name rule to each structured value as it
  is recorded, and its secret-value pass by the SDK's export-time hook to every attribute - the
  same rule as the log, with no per-span opt-in. It masks secrets, not patient text: a trace exists to
  show what the model saw and said, and every message in this deployment is synthetic.
- **An eval run sends Langfuse events by default, but it can be disabled when needed.** `make
  eval-run` traces the turns it drives, so a case that surprises in a run's report has a trace to
  open without re-running it, and `make eval-run TRACE=0` turns tracing off for that run. The case
  for turning it off is the budget: a full run is expected to cost ~1.3k units and a noise band is
  five of them, ~6.3k of the month's 50k, spent on traces nobody opens — a run's metrics never come
  from a trace. The switch is per run, not per stack, so a developer tracing the app by hand keeps
  tracing while an untraced run executes beside it. The choice travels on the run's own requests,
  as an `X-VisitDoc-Trace: off` header on each turn, never as a setting of the service, so the
  service is not restarted or reconfigured for it. The run records whether it was traced, and
  tracing must not change what a turn does — a traced and an untraced run of one build compare as
  the same conditions.

##### Tracing with Langfuse: technology choices

`specs/014-langfuse-tracing/` (Phase 2d) gives every chat turn one trace in Langfuse: the
graph's nodes, each FAQ request's retrieval steps, every Claude call as a generation with its
prompt, output and token usage, and every tool call. Only the chat service is traced, and only
turns. The code is one package, `chat.observability`, the only module allowed to import the
Langfuse or OpenTelemetry SDK (a test enforces it). The full reasoning is in the 2d section above.

To turn it on, set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in `.env` from a Langfuse Cloud
project. `LANGFUSE_BASE_URL` (default `https://cloud.langfuse.com`) and `LANGFUSE_ENVIRONMENT`
(default `development`; eval turns are always filed as `eval`) are optional. Leaving either key
blank means tracing is off; the startup `service.configured` event states which with
`tracing_enabled`.

- **Langfuse Cloud's free Hobby tier, not self-hosted Langfuse.** Self-hosting is the choice with no
  third party in the loop, and it costs six containers — web, worker, PostgreSQL, ClickHouse, Redis
  and S3-compatible storage — for a debugging view, tripling a local stack that is Postgres and
  Qdrant today. Hobby covers the use: 50k units a month, 30 days of history, two users, no card.
  The costs are that patient messages and prompts leave the machine, and that traces older than
  30 days are gone, which loses nothing the eval chain reads.
- **Graph nodes are spanned by `node_span`, not by Langfuse's callback handler.** The roadmap first
  named the handler, and it was measured before it was dropped: it runs on an executor thread
  under a copied context, so a retrieval step, generation or tool call opened inside a node cannot
  nest under the node's span, and the trace comes out flat. It also records nothing about the
  direct Anthropic calls, which are all of this agent's calls, and it would bring `langchain` in as
  a dependency. `node_span` already bounds every node for its `node.started`/`node.completed` log
  events, so it opens the node's observation too, with the `node.completed` payload as its output.
  The cost is that a node is traced only because it uses `node_span` — nothing picks up a new node
  automatically — and that LangGraph's own machinery between nodes does not appear in a trace.
- **Untraced is a sampling decision made per turn, from a request header.** A request carrying
  `X-VisitDoc-Trace: off` exports nothing for its turn while the turn beside it, in the same
  process, exports everything. The service owns an isolated `TracerProvider` whose sampler drops
  every span opened in a context carrying an untraced flag, and that flag is set in exactly one
  place: `launch` in `services/chat/src/chat/api/turn.py` sets it on a copied context that is
  passed only to the turn's own task, so every span the turn opens sees it and nothing else does.
  Tests read the package's syntax trees to pin that single setter and where it sits. The headers are read from the request rather
  than declared as parameters, so the published OpenAPI schema is unchanged, and a malformed one
  is refused with 422 before anything is stored. Four simpler mechanisms were tried and each fails
  a requirement: not opening a root turns strays into root traces, suppressing instrumentation is
  ignored by the SDK's tracer, a second disabled client collides with the SDK's per-key singleton,
  and `sample_rate` is process-wide; filtering at export works but builds, masks and serializes
  every span it then throws away. The cost is a custom sampler — a new mechanism whose
  invariant (set once, before the task, never later and never outside it) has to hold by
  discipline and the tests that pin it.
- **Masking is the log's own redaction, applied in two halves: the key-name rule where a value is
  recorded, the secret-value pass at export over every attribute.** It uses the redaction
  `shared-logging` already owns, made public there rather than copied, so the log and the trace
  cannot disagree about what a secret is. It masks secrets only — API keys, credential parts of
  connection URLs, the admin secret, the Langfuse keys — and not patient text, prompts or model
  output, which are what a trace exists to show (every message in this deployment is synthetic).
  The SDK's older `mask` hook sees only input, output and metadata; an exception's text lands in
  the status message, which it never reaches. So the export-time hook (`mask_otel_spans`), which
  sees every attribute of every span and runs on the exporter thread rather than the event loop,
  replaces every occurrence of a configured secret value in every string attribute — and decodes
  nothing. The key-name rule (a value under a secret-sounding key replaced whole, at any depth) is
  applied by `chat.observability` when an input, an output or metadata is recorded, to structured
  values only — dicts, lists, tuples and pydantic models — because the SDK exports a structure as
  one serialized JSON string, and past that point a payload the code recorded as a dict cannot be
  told from a patient message that happens to read as JSON. A string is recorded as it is, so such
  a message reaches the trace verbatim, as it reaches the log. Usage and model parameters skip the
  key-name rule, since `cache_read_input_tokens` and `max_tokens` are counts it would take for
  tokens. The costs: two places to read to know what is masked, where there was one; and a mask
  that raises drops the whole batch, losing a trace rather than exporting it unmasked.
- **Blank keys build no client at all, and the keys are handed to the SDK explicitly.** The SDK
  would otherwise read `LANGFUSE_*` from the process environment itself, and it treats an empty key
  as a real one — `.env.example` ships the keys blank, so a copied file would build an exporter
  that fails to authenticate on every batch. `Settings` decides `tracing_enabled` from stripped
  values, and a disabled tracer holds no Langfuse client, so it certainly sends nothing. The unit
  tests blank both keys in their `conftest.py`, because the repo's own `.env` may hold real ones,
  and no test reaches Langfuse. The cost is that anyone who sets the keys traces every hand-driven
  turn, spending units whether or not a trace is opened.
- **A lost export is logged, never raised.** Export is batched on a background thread, so a
  Langfuse outage cannot fail or slow a turn. Its only trace is a warning on a stdlib logger, which
  this service would otherwise never read; a bridge re-emits the SDK's and OpenTelemetry's
  warnings as `tracing.export_failed` through the structlog chain, redaction included. (Alembic's
  `fileConfig` disables loggers that already exist when it runs, so the bridge re-enables the ones
  it listens to.) The cost is that the count of these lines is not a count of lost exports: an
  exporter that raises is reported by OpenTelemetry's batch processor, whose logger drops a repeat
  of an identical record within the same 20-second window, while a rejected or timed-out request is
  logged by the exporter itself, retry warnings included.
- **Eval runs are traced by default; `make eval-run TRACE=0` turns it off.** Tracing every run
  means a surprising case in a run's report has a trace to open, rather than having to be re-run
  to get one: each case file maps its patient messages' ids to the trace ids their turns logged
  in `turn.traced`, and every eval trace carries the run id and case id (`X-VisitDoc-Eval-Run`/
  `X-VisitDoc-Eval-Case`) and the `eval` environment. `TRACE=0` sends every turn with
  `X-VisitDoc-Trace: off` — the choice travels on the run's requests, so the service is not
  reconfigured and a developer tracing by hand keeps tracing beside it. `run.json` records
  `tracing` as `traced`, `untraced_by_request` or `untraced_service_off` (runs from before this
  phase read as the last); it is deliberately not a run condition, so a comparison shows it beside
  each run's id and never in its condition delta or band. The cost is the budget: a full
  golden-set run is expected to cost ~1.3k units (6–23 per turn, measured per turn shape in
  `specs/014-langfuse-tracing/evaluation/units.md`; the full-run figure is weighted from those, not
  yet observed) and a noise band is five runs, ~6.3k of the month's 50k, spent on traces that answer
  no question a run asks — its metrics come from the stored run, never
  from a trace. So the runs where no trace will be opened, a noise band above all, are the ones to
  run with `TRACE=0`.

##### Model Spend and Prompt Caching: technology choices

Measured before anything was changed: every model call logs a `model.usage` entry, and
`make eval-cost RUN=<run>` totals a stored run's spend per call site (`evals/harness/README.md`).
Two unchanged runs of the same 15 golden cases cost $0.448 and $0.431 in Claude calls, and the
booking loop was 80% of both - about 2.4 model calls per booking turn, each re-sending ~5.9k
tokens of tool definitions and system prompt. Five choices carried a real tradeoff.

- **Spend is read from the service's own log, not from Langfuse or the Console.** The entry is
  written whether or not a turn is traced, is stored with the case it belongs to, and is priced
  offline by a pure function of the stored run - so a spend report is reproducible and sits beside
  the quality report of the same run. The cost is a second record of usage beside Langfuse's; both
  are built from one dict, so they cannot disagree. Its keys avoid the word "token", which the
  log's redaction rule would blank.
- **Caching in the booking loop only.** A prefix shorter than the model's minimum is silently not
  cached (1,024 tokens on Sonnet 5, 4,096 on Haiku 4.5), and a cache pays only when the same bytes
  are sent again within five minutes. Measured with `count_tokens`: the classifier's system prompt is
  2,126 tokens on Haiku, and the composer's, the FAQ answerer's and small talk's are 982, 210 and
  338. Only the booking loop re-sends a prefix long enough, seconds apart.
- **Three breakpoints, most shared first, and the prompt reordered to match.** A cache entry is
  written only where a marker sits and read only by a request starting with the same bytes. The
  tools (~2.7k) are marked on the last one; the instructions and the clinic's roster are one
  system block with its own marker; the patient's name and local time follow as a second, unmarked
  block, and the request-level marker caches that and the conversation so each iteration reads what
  the previous one sent. Before the split, the name and time sat in the first lines of the system
  prompt, so no turn could read another's copy of it. The cost was a prompt change - the rules now
  refer to "the patient named below" - so every booking case of the golden set was re-run
  (24/24 end to end; of the 23 the baseline also recorded, none moved). Measured: the 15 cases went from $0.44 to
  $0.24 with the first two breakpoints, and with all three 94% of the booking loop's input was read
  from the cache, at $0.0035 a call against about $0.014 uncached.
- **The five-minute cache, not the one-hour.** A one-hour write costs 2x instead of 1.25x. The
  hits within a turn are seconds apart either way; the hits across turns - another patient's turn
  reading the shared prefix - need booking traffic less than five minutes apart. An eval supplies
  that and a quiet clinic may not, and when it does not, a turn's first call writes the prefix at
  1.25x rather than reading it at 0.1x: the within-turn saving remains. Which lifetime pays depends
  on real traffic gaps, which no eval shows.
- **No effort tuning.** Sonnet 5 thinks by default, but thinking was under $0.02 of a run: output
  of every kind was about a tenth of the spend. Lowering `effort` would trade answer quality for a
  saving the measurements do not show.

### Phase 3 — The frontend on its own terms
Every phase so far treated the frontend as the thinnest surface that made backend work
demonstrable — 1c's chat list existed because a patient is one-to-one with a chat, 1d's two-pane
screen existed because an escalation nobody can see cannot be exercised. This is the first phase
where the frontend is the subject rather than the instrument, and it holds the two pieces of
frontend work that were deliberately deferred rather than skipped.

#### Phase 3a — Frontend design pass
The stub this section used to be asked whether a visual pass was worth doing at all on a project
judged on its AI core. It is: the SPA ships no stylesheet whatsoever, so "minimal, kept lean" is
not a restrained aesthetic but the browser's own defaults, and that is the first thing anyone
opening the product sees. The pass is bounded by what the sketch draws and keeps both audiences on
one page — the patient messenger and the staff console side by side, as 1d's two-pane screen
already places them. Splitting them onto separate routes is deliberately *not* done: a single
screen is what lets one person exercise an escalation from both ends, which is the whole reason the
second pane exists.

Two capabilities the sketch draws are **not frontend work**, and neither is a styling task
disguised as one. The design pass renders each as a visible stub rather than omitting it, so the
screen it belongs on is laid out for the real thing and the gap is legible instead of forgotten:

- **A practitioner's standing appointments for the next 7 days.** The Practitioners tab shows them
  grouped by day beneath the selected practitioner. Nothing serves them today: `/console/*` proxies
  practitioner CRUD and `/console/specialties`, and Scheduling's `ListAppointments` is not among
  what it proxies. Needs a console endpoint over that RPC, scoped to the session the way every
  other console read is, with the practitioner and the 7-day window as its predicate rather than a
  filter applied to a wider answer.
  *(Shipped in `specs/016-staff-booking-visibility/`. The week lives on the roster itself, inside
  each practitioner's block behind a Show/Hide bookings toggle, rather than beneath a selected
  practitioner. It is read over the scheduler's REST admin surface
  (`GET /practitioners/{id}/appointments`, proxied as `/console/practitioners/{id}/appointments`),
  not over `ListAppointments`: that RPC is scoped by patient and never by practitioner, so serving
  this read would have meant either listing every patient's appointments and filtering them — the
  filter this bullet rules out — or widening the agent's contract to serve a staff screen. The
  predicate stands as written: session, practitioner, standing status and both ends of the window
  are all in the scheduler's one `WHERE`, and the window itself is computed in chat from the
  browser's own `local_now`.)*
- **A booking outcome a staff member can read.** The sketch's `(i)` marker on a patient message
  opens what the assistant did with that request — for a booking, the change it actually made
  ("Appt with dr. Andreas at 9:00 12.01.2027 is cancelled"). `request_outcomes` cannot carry it:
  011 put the verdict, answer and citations on the request precisely because they describe a *FAQ*
  answer, and `NULL` there means no FAQ half ran. A booking's outcome is a different thing — a
  performed act, not a claim the corpus grounded — so it needs its own shape on the message rather
  than a sixth `FaqVerdict` value or an `answer` string standing in for one. Until it exists the
  marker carries what the turn already records: the FAQ outcomes, and the attention mark that
  covers the red case (a corpus gap, an unauthorized request, an urgent condition).
  *(Shipped in `specs/016-staff-booking-visibility/` as **booking acts**: their own shape, a
  `booking_acts` list on the message beside `request_outcomes` and never inside it. An act hangs
  off the *patient* message the turn answered rather than the reply, is written before the request
  leaves for the scheduler and settled at most once, and an act that never settled is shown as
  "Outcome unknown", never as nothing having happened.)*

##### The Frontend Design Pass: technology choices

`specs/015-frontend-design-pass/` (Phase 3a) gives the SPA its first stylesheet and the
interaction behaviour a designed product implies — a tab strip with overflow, aligned bubbles with
sender grouping, bottom-pinned threads that hold a reader's position, a working indicator, disabled
send controls, an empty-thread greeting, a three-section staff console, and per-request evidence
behind a click-to-expand marker. **No network contract changed**: the feature left
`src/lib/chatStream.ts` and `src/lib/consoleApi.ts` byte-identical to what they were before it,
which is what the claim that this is a presentation-only change rests on. (016, below, then
extended both for booking acts and the practitioner's week.)

- **Tailwind CSS v4 with shadcn/ui, over the smaller option that was recommended and declined.**
  Three controls here carry focus management that is genuinely hard to get right — the staff tab
  set (roving tabindex with arrow keys), the discard confirmation (focus trap with restore-on-close
  and background inerting) and the chat overflow menu (typeahead and collision-aware positioning) —
  and the spec states an accessibility floor three times. shadcn vendors its components into the
  repository *as source*, so they are themed and edited like any other file here and there is no
  wrapper library to fight; Tailwind v4 is CSS-first, so the token contract lives in `@theme` as
  custom properties. The alternative actually recommended in `research.md` was unstyled Radix
  primitives over hand-written scoped CSS: the same accessibility for the same three controls, at
  about four packages instead of a stack, leaving the chosen design direction untouched. It was
  **declined by the user** in favour of the stack a reviewer of a portfolio project expects to see,
  and that is recorded as the reason it is rather than rationalised into a technical one. The costs
  are real and were accepted: markup in all eight components was rewritten, and shadcn's default
  look is recognizable enough that theming away from it is work — which is why
  `specs/015-frontend-design-pass/contracts/tokens.md` maps every one of shadcn's semantic
  variables onto a token and why the vendored dialog and dropdown each had their shipped
  drop shadow removed. An unmapped default is treated as a defect, not a leftover: it is how a
  themed app quietly reverts to looking like the library's demo.
- **Every package this feature added, and why each is here.** Thirteen in all — one stack, its
  peer utilities, and one type package. The Radix components are what shadcn vendors *per
  component*, so the list grows only when a new primitive is vendored.

  | Package | Why |
  |---|---|
  | `tailwindcss`, `@tailwindcss/vite` | The utility engine and its Vite plugin. v4 is CSS-first, so the theme is `@theme` custom properties in one stylesheet rather than a JS config |
  | `@radix-ui/react-tabs` | The staff console's three sections: roving tabindex, arrow keys, correct `aria-selected`/`aria-controls` |
  | `@radix-ui/react-dialog` | The discard confirmations: focus trap, restore-on-close, background inerting, Escape |
  | `@radix-ui/react-dropdown-menu` | The chat overflow: typeahead, collision-aware positioning, outside-press |
  | `@radix-ui/react-switch` | The assistant on/off control, as a real `role="switch"` rather than a styled checkbox |
  | `@radix-ui/react-slot` | `asChild`, which is how a vendored `Button` lends its styling to a menu or dialog trigger without nesting two buttons |
  | `clsx` | Conditional class lists, without string concatenation in markup |
  | `tailwind-merge` | Makes a caller's utility win over a component's default *for the same CSS property*, instead of both landing in `class` and source order deciding |
  | `class-variance-authority` | The `variant`/`size` tables on `Button`, so a variant is a named row rather than a ternary |
  | `lucide-react` | The icon set. Every icon is a tree-shaken component, so the bundle carries only the icons actually imported |
  | `tw-animate-css` | The enter/exit keyframes shadcn v4 expects on dialog and dropdown. Its animations respect `prefers-reduced-motion` through the global block in `app.css` — verified, not assumed, since they arrived with the library |
  | `@types/node` (dev) | `vite.config.ts` and `vitest.config.ts` use `node:path` and `import.meta.dirname` to declare the `@/*` alias |

  **The shared cost, stated once rather than per row**: twelve runtime packages the frontend did
  not have, and about 60 more in `node_modules` transitively. Measured from `vite build` in this
  repository — the JS chunk was **213 KB (66 KB gzipped)** with Tailwind installed but before any
  Radix component was used, and is **368 KB (116 KB gzipped)** now; Tailwind contributes no runtime
  JS, so that difference is the primitives, `lucide-react` and `cva`. The stylesheet went from
  none at all to **32 KB (7 KB gzipped)**, and the three typefaces add 71 KB of `woff2` fetched
  once and cached immutably. For a portfolio demo served from the same host as its API that is a
  cost worth paying for three accessible controls; for a latency-sensitive product it would deserve
  a second look, and the vendored-source model means any one primitive can be replaced in place
  without touching a call site.

  Two dependencies were **not** taken. `@ibm/plex` (see the typeface entry below) and
  `@testing-library/user-event`, which would have made driving the Radix controls in tests a
  one-liner: `tests/press.ts` does the same job in eight lines of `fireEvent`, against a suite
  whose hundred-odd other `fireEvent.click` call sites would have been inconsistent with it.

- **An earlier decision to add no dependencies at all was reversed, and the reversal is kept.**
  The first answer was CSS Modules and zero new packages, resting on a "minimal dependencies"
  convention **this repository does not have** — checked and not found in `.claude/CLAUDE.md`,
  `README.md` or `docs/ROADMAP.md`, while the chat service alone declares twenty runtime
  dependencies. The rule this repository actually has is the one these sections demonstrate:
  record the tradeoff. `research.md` Decision 1 keeps both answers rather than overwriting the
  first, because the reasoning that produced it is worth more to a later reader than a document
  that looks as though it always knew.
- **The typeface is committed asset files, not a package and not a font service.** IBM Plex Sans at
  weights 400/500/600, latin subset, three `woff2` files under `src/styles/fonts/` with the SIL OFL
  1.1 text beside them. Self-hosting is a requirement — no third-party request is made at runtime,
  and the page degrades to a named fallback stack rather than to a browser default. They are
  imported from `src/` rather than dropped in `public/` so Vite content-hashes them and they cache
  immutably. `@ibm/plex` was rejected not because a dependency is forbidden but because it ships
  every weight, style and script of the whole family to deliver three latin files, and a build step
  to extract them is more machinery than committing three files. The cost is 71 KB of binary in the
  repository and a manual step if a fourth weight is ever wanted. (The chosen mockup,
  `specs/015-frontend-design-pass/design/a-front-desk.html`, still loads its font from
  `fonts.googleapis.com`; that is a recorded defect of the mockup, not a decision.)
- **Appearance is verified by a recorded browser procedure, not by tests that cannot fail for the
  right reason.** Every *behavioural* requirement here is test-driven normally — tab set, overflow,
  disabled controls, expansion, the scroll rule, the greeting, the stubs, semantics, focus order —
  and the suite grew from 234 tests to over 400. Appearance requirements get
  `specs/015-frontend-design-pass/quickstart.md` instead, run at four widths with the result
  written down. `expect(el).toHaveStyle("color: #0F2E33")` only restates the CSS in a second place:
  it fails when a token is renamed and passes when the colour is wrong for its purpose. Snapshot
  tests were rejected for the same reason plus churn. The one measurable half of it *is* recorded
  as data — `specs/015-frontend-design-pass/evaluation/contrast.md` computes WCAG ratios for every
  foreground/background pair the palette can form, which found two live pairs under AA that a
  by-eye check would have passed.
- **One reserved colour, and colour is never the only carrier.** `--color-attention` means "a person
  is needed" and nothing else may use it — not a decorative accent, not a required-field asterisk,
  not a delete button. shadcn maps its `destructive` variant onto that colour, so
  `variant="destructive"` is making that claim; the confirmations — deleting a chat in `ChatList`,
  discarding an edit in `PractitionerAdmin` and `FaqAdmin` — all decline it, and carry their weight
  in the sentence naming what is lost instead. (Deleting a practitioner or an FAQ entry asks
  nothing: the icon deletes on one press.) Every state marked by colour is also marked by text, weight, shape or
  position, so a greyscale screen and a reader who cannot distinguish the hue both still see it.
- **Light theme only, by decision rather than omission.** shadcn installs a dark theme by default;
  it was removed rather than left unreferenced, and no `dark:` variant or `prefers-color-scheme`
  block survives under `services/frontend/src/`. The token structure is what a later dark theme
  would redefine, so the decision is reversible without being half-made.
- **The scroll rule is a pure predicate, because jsdom cannot exercise it any other way.** jsdom
  reports `scrollHeight` and `clientHeight` as `0` for every element, so a "follow new content only
  when the reader is at the bottom" rule written inside a component reads `0 - 0 <= threshold` →
  always pinned: the hold-position branch would never execute while the test covering it passed.
  `src/lib/scroll.ts` takes four numbers and no element, is unit-tested across its boundary, and
  two component tests per thread stub the measurements to prove it is actually consulted. The
  related trap: `scrollIntoView` is `undefined` in this jsdom and throws, so both threads scroll by
  assigning `scrollTop`.
- **Radix needs no jsdom polyfill, but it does need the right event.** This was carried into the
  phase as an open risk — that Radix would demand `ResizeObserver`, `matchMedia` or `DOMRect` and
  fail tests that have nothing to do with a dialog. Measured first, before anything was built on
  the stack: nothing throws and `tests/setup.ts` is unchanged. What is true instead is that these
  components open on `pointerdown`/`mousedown`, which `fireEvent.click` does not dispatch — so a
  tab does not switch and a menu does not open, and it fails in the shape of a missing element
  rather than a missing event. `tests/press.ts` fires the whole pointer sequence and drives all
  four primitives *and* a plain `<button>` exactly once; the existing `fireEvent.click` call
  sites were left alone, since rewriting passing tests to use a helper they do not need is churn
  with a migration's risk.

##### What Staff Can See of the Schedule: technology choices

`specs/016-staff-booking-visibility/` (Phase 3a's two leftovers) gives staff a record of
what the assistant did to the schedule on each message — a **booking act** per attempt to book,
reschedule or cancel — and a practitioner's standing appointments for the coming seven days,
inside that practitioner's block on the roster behind a Show/Hide bookings toggle. Seven choices
carried a real tradeoff.

- **An act hangs off the patient message, not the reply.** The reply is the obvious anchor — it is
  where the assistant says what it did — and it is missing in exactly the turns a person most needs
  this record for: one that failed after its write, one staff took over, one a newer message
  cancelled. The patient message the turn was answering always exists, so the record always has
  somewhere to live. The cost is that the console shows an act under the question rather than
  under the answer, and the wire says so: `booking_acts` appears only on `sender = "patient"`
  messages, `null` when nothing was attempted and never `[]`.
- **Write-ahead, settled once, and NULL read as unknown.** An act is inserted and committed, with
  no outcome, *before* the request leaves for the scheduler; its answer then settles it with one
  `UPDATE` whose `WHERE` carries `outcome IS NULL`, so a second settle is a no-op. The alternative
  — write the act once the answer is back — records nothing for precisely the attempts whose
  outcome is in doubt: a handler that raised, a turn cancelled mid-call, a settle that failed. With
  the row written first, each of those leaves an act with no outcome, and the console renders that
  as "Outcome unknown … may or may not have been …", never as nothing having happened. And an act
  that could not be written is never sent: the tool answers `unavailable`, so the record cannot be
  missing a write that reached the scheduler. The cost is a commit before every schedule-changing
  call, and a row that says "unknown" for an attempt that may in fact have succeeded — which is
  the honest thing to say about it. NULL (no outcome was ever written) and `unknown` (the tool's
  own answer was that it could not tell) are kept distinct in storage and on the wire, and rendered
  with the same words.
- **Recorded in the tool registry, not in each handler.** A write tool declares what it is about to
  do (`Tool.plan_act`, reading its arguments with the handler's own helpers) and
  `ToolRegistry` records around the handler through a write-only `BookingActRecorder` port. Three
  copies of begin/settle in three handlers would be three places to get the order wrong, and a
  fourth write tool that forgot them would silently leave its attempts unrecorded. The cost is that
  arguments are parsed twice per write — deterministically, over the same dict.
- **A table, not a JSONB column beside `request_outcomes`.** 011 chose JSONB for `request_outcomes`
  because a turn's outcomes are written once, together. Acts are not: each is inserted and later
  updated by id, and the booking loop dispatches one model response's tool calls concurrently
  (`asyncio.gather`), so two settles on one JSONB array would be a read-modify-write race with a
  lost update waiting in it. Rows have no such race. On the wire it is still its own shape on the
  message, never an entry in `request_outcomes` and never a `FaqVerdict` value: an act is a
  performed change, not a claim the corpus grounded. Cascades follow the message, so deleting a
  chat or a session removes its acts.
- **A snapshot, not a live view.** An act stores the practitioner's *name* as known when the act
  ran — from the turn's roster read, overwritten on settle by any name the scheduler's answer
  carried — and is never touched again after it is settled. Nothing joins it back to scheduler
  data, and the ids stay in storage and off the wire. So a later reschedule, a renamed or deleted
  practitioner, or a cancelled appointment does not rewrite what the record says happened at the
  time. The cost is that the record can disagree with the schedule *as it is now*; that is what the
  week view is for. A name nobody reported is shown as "a practitioner not named in the record"
  rather than an id or a guess.
- **The week is a scheduler REST read, with its window computed in chat from the browser's clock.**
  `ListAppointments` is the agent's gRPC call and is scoped by patient, never by practitioner, so
  answering from it would mean listing every patient's appointments and filtering them, or widening
  the agent's contract to serve a staff screen. The console already speaks REST to the scheduler
  for practitioner CRUD, so the read is `GET /practitioners/{id}/appointments` beside the
  practitioner itself, with session, practitioner, standing status and both ends of the window in
  one `WHERE`. The window is one pure function in chat, `practitioner_week_bounds(local_now)`:
  from the browser's own offset-free `local_now` to midnight at the start of the eighth day, so the
  whole seventh day is in it and midnight crossing needs nothing. The scheduler takes both bounds as
  given and knows nothing of weeks; the browser sends only its clock, so the console exposes the
  product's read rather than a general range query. An open list re-reads on the console poll's
  tick with at most one read in flight, and a failed refresh replaces the list with an error rather
  than leaving the last good one on screen as if it were current.
- **The staff thread re-reads on a counter, not a clock.** A turn can settle an act without writing
  a message — the failed turn a person is paged for is exactly that case — so `last_message_at`
  alone would leave "unknown" on screen after the act had settled. Each console listing row
  therefore carries `booking_acts_version`: the chat's number of acts plus the number of them
  settled. Rows are only inserted and settled once, and deleted only with their chat, so every
  change moves it and nothing else does. It is compared for equality only; `max(settled_at)` was
  rejected because two settles in one clock tick look like one. It rides the 2-second poll the
  console already runs, so it costs one correlated aggregate and no new request — rather than a
  push channel, or bending `last_message_at`, which the list shows and orders by, into meaning
  "something changed".

#### Phase 3b — End-to-end tests in a real browser
The `tests/e2e/` tier, held open since Phase 0, is filled here. Not earlier, for two reasons that
compound. First the contract: 1e replaces `grounded` with a typed verdict and rebuilds citations
from the reranked shortlist, and 1h then moves both onto the request and removes the turn-level
fields altogether — so a suite written before either would assert against a contract already
scheduled to change, twice. Then the surface: a browser suite selects by the DOM and the
`data-testid` hooks the components expose, which is exactly what 3a would reshape — so a suite
written before the design pass would be rewritten by it.

Kept deliberately small: eight journeys, one per feature a demo would be judged on, each driven
from the patient pane and checked from the staff pane as well as from the reply. Every case plants
its own prestate in both databases rather than inheriting another case's, so any one of them runs
alone.

1. **Free slots.** The patient asks for a dentist's available slots today. The prestate books two
   of that practitioner's slots for today. The reply names every slot still free and neither of
   the two booked ones.
2. **The patient's own appointments.** The patient asks which appointments they have. The
   prestate holds one standing appointment for this patient with a practitioner, other patients'
   appointments with the same practitioner, and one cancelled appointment of this patient's own.
   The reply lists exactly the one standing appointment: not the others' and not the cancelled one.
3. **Booking.** The patient asks to book a GP at a specific time. The reply says it is booked, the
   appointment appears under that practitioner in Staff console → Practitioners, and the reply
   carries a blue `(i)` marker in the staff thread whose content describes the booking.
4. **Rescheduling.** The patient asks to move an existing appointment to another time. The reply
   says it is rescheduled, Practitioners shows the appointment at its new time and not at its old
   one, and the reply's blue `(i)` marker describes the reschedule.
5. **Cancellation.** The patient asks to cancel one of their appointments. The reply says it is
   cancelled, Practitioners no longer shows it, and the reply's blue `(i)` marker describes the
   cancellation.
6. **An FAQ answer.** The patient asks a question the FAQ covers. The reply answers it, and its
   blue `(i)` marker holds the FAQ chunk the answer was drawn from.
7. **An FAQ gap.** The patient asks a question the FAQ does not cover. The reply says staff have
   been called, the conversation carries the attention (exclamation) icon in the staff console's
   conversation list, and the reply's `(i)` marker is red.
8. **Asking for a person.** The patient asks to speak to a person. The reply says staff have been
   called, the conversation carries the attention icon, and the reply's `(i)` marker is red.

Colour is what a person checks; it is not what the suite asserts. Blue and red are the marker's
`data-outcome-state` (`served` / `needs-person`), the exclamation icon is the conversation's
`data-emphasized`, what the marker describes is its `booking-act` (`data-operation`,
`data-outcome`) or its `citations`, and Practitioners is read through `bookings-toggle` and
`week-appointment` — the hooks `services/frontend/.claude/CLAUDE.md` lists, which name what a thing
is rather than how it looks. The two cases whose whole claim is in the reply's text, 1 and 2, assert
on the facts the prestate fixed — which times and which appointment appear — never on the model's
phrasing.

Driven by `pytest-playwright`, so the tier stays pytest behind `make test-e2e`. Alone among the
test tiers it may spend live model calls (see `docs/testing-strategy.md`) — which is exactly what
keeps it off the per-push gate the unit tier holds, and what forces its assertions onto structure
rather than onto the model's wording. The 13-scenario `quickstart.md` stays a manual walk-through:
its value is that a person reads it before a demo, which automating it would remove rather than
preserve.

*(Shipped in `tests/e2e/`, without a spec directory. The suite runs against the stack `make
services-up` runs rather than one it starts, and isolates by session instead of by database: each
journey mints its own session, plants its prestate through the console's practitioner API and the
booking tools' own gRPC client, and deletes the session through `/admin` afterwards. "Today" is
made deterministic by the browser's timezone rather than a faked clock — the context gets a
fixed-offset zone in which it is early morning — which works because every time in this system is
the visitor's own wall clock. It runs by hand, not in CI.)*

##### End-to-End Tests in a Real Browser: technology choices

`tests/e2e/` (Phase 3b) drives eight journeys — free slots, the patient's own
appointments, booking, rescheduling, cancelling, an FAQ answer, an FAQ gap, a request for a person
— through a real Chromium, from the patient pane to the staff console. Four choices carried a real
tradeoff; the details are in [`tests/e2e/README.md`](../tests/e2e/README.md).

- **`pytest-playwright`, not a Node test runner.** The frontend has vitest, and Playwright's own
  runner is the more common choice for browser suites. But these tests set up prestate through
  `chat`'s own gRPC client and import `chat`'s constants — the hand-off text, the starter corpus,
  the escalation reasons — so they would otherwise restate them in TypeScript, and the tier would be
  the one test tier not behind pytest. The cost is the sync API's event loop: an async gRPC call from
  a test runs on a thread of its own.
- **Against the running stack, isolated by session.** The integration tier starts its own servers
  on `_test` databases; this tier drives whatever `make services-up` runs, on the dev databases.
  Each journey mints a fresh session and deletes it afterwards, and since every read the app makes
  is scoped to a session, nothing one journey plants is visible to another or to a person using the
  stack. The payoff is that the tier tests the processes a demo would use, not a copy of them; the
  cost is that it needs that stack up and an `ADMIN_SECRET` to clean up after itself.
- **The visitor's clock, chosen per run.** "Today's free slots" depends on the hour. Rather than
  faking time — which would reach timers and the 2-second poll too — the browser is given a
  fixed-offset `Etc/GMT` zone in which it is 07:xx, and the prestate is planted on that clock. It
  works because the system has no timezone anywhere: every time is the visitor's own wall clock,
  sent as `local_now`, so a zone only chooses which wall clock the visitor has.
- **Facts from a model's reply, never its wording.** Two journeys have nothing to assert but the
  reply. They are held to the times of day it names, read in any format, with a prestate arranged
  so that no forbidden time can appear as the end of a permitted one. Everything else asserts on
  hooks, data attributes and production constants.

### Phase 4+ — Optional extensions (if time allows)

#### Phase 4a — Staff in the loop from the Claude app
The console only helps a staff member who is looking at it. This phase lets them stay in the loop
while they are away from it: from the Claude mobile app, with VisitDoc added as a custom connector,
they can ask two questions and get a number back.

1. **How many conversations need staff attention right now.**
2. **How many appointments were booked, cancelled or rescheduled in the last N minutes or
   hours**, answered as a sentence Claude can relay: "Three appointments were booked, one
   cancelled and two rescheduled."

This is the second consumer 1c said MCP should wait for, and it is a different consumer from the
one 1c imagined: a staff member reading console state, not an outside client driving the agent's
tools. The agent's in-process registry stays exactly as it is. Its tools are patient-scoped, with
the patient bound from the chat the turn runs in, and nothing in this phase puts them behind MCP.

- **A remote MCP server mounted in the chat service** (`/mcp`, streamable HTTP), calling the same
  functions the `/console/*` routes call rather than holding logic of its own. The scheduler is not
  involved: both answers come from the chat service's own database, and the scheduler never learns
  that MCP exists.
- **Read-only, by having no write tools at all**, not by a scope that hides them.
- **It returns counts, never conversations.** No patient name and no message text leaves through
  this surface. That keeps what reaches a third party to the minimum the two questions need, and
  it means text a patient wrote can never arrive in the staff member's Claude as something that
  reads like an instruction. The details stay in the console, where the staff member goes next.
- **"Needs attention" means exactly what the console marks.** The count is taken over the same
  derived state the conversation listing shows, so the phone and the console cannot disagree about
  which conversations are waiting.
- **The booking count reads 016's `booking_acts`**, counting distinct appointments with an act
  that settled as `done` inside the window, per operation: an appointment rescheduled twice is one
  rescheduled appointment. Acts whose outcome is unknown or never settled are reported as their own
  number, never folded into the done count or silently dropped. Every schedule change the system
  makes is an act, since staff cannot book, so the table is complete for this question.
- **The window is the one time judgement in the system that needs no `local_now`.** "The last 30
  minutes" is measured back from the server's clock against `settled_at`, which is a real timestamp,
  so the app still stores no timezone. Every other date judgement keeps the visitor's own wall clock.

**Authorization is OAuth 2.1, as the MCP authorization spec requires**, with the chat service as
both the resource server and the authorization server. Claude discovers it from the `401` on
`/mcp` (RFC 9728 protected-resource metadata, then RFC 8414 server metadata), registers itself by
Dynamic Client Registration, and signs in with an authorization code bound by PKCE (S256) and
redirected to `https://claude.ai/api/mcp/auth_callback`. Tokens are opaque and checked against the
database, so a revocation takes effect on the next call. Refresh tokens rotate on every use, and a
reused one revokes the whole grant.

- **The session stays the only identity; a pairing code bridges it.** 1d's decision stands: there
  is no staff login. The staff member clicks *Connect Claude* in the console, which shows a
  single-use code valid for ten minutes, stored only as a hash. The `/authorize` page, a
  server-rendered form rather than part of the SPA, asks for that code. Consuming it is one
  conditional `UPDATE` whose `WHERE` carries the expiry and `used_at IS NULL`, and wrong attempts
  are capped. The code is what proves that the person connecting Claude is the person at the
  console, and it is what binds the issued token to that session.
- **The session comes from the token, never from a tool argument**, the same rule `ToolContext`
  follows for the agent. Every query keeps its session predicate.
- **The console lists connected Claude apps and can revoke each one.** Deleting a session through
  `/admin` removes its grants and tokens with it. Token material joins the log redaction list in
  the same change that introduces it.
- **It needs a public HTTPS endpoint, the first thing in this project that does.** Anthropic's
  cloud calls the connector, not the phone, so a server reachable only inside WSL cannot serve it.
  A tunnel is enough for a demonstration.

*(Built in `specs/017-staff-mcp-connector/`; the walk against a real Claude account and a public
tunnel, its `quickstart.md` §1–§5, has not been done yet, so the connector is tested but not yet
seen working in Claude. Four things differ from the plan above. The console's control is a gear
tab, "Connected apps", whose button reads *Get pairing code*. The needs-attention count does not
call the listing's function but counts the listing's own `emphasized` expression, which keeps the
two equal by construction without reading every row. `booking_acts` gained a check that a `done`
act names its appointment, because the count is of distinct appointments and a NULL id would drop
out of it silently. And the log redaction gained the credential keys by name (`code`,
`pairing_code`, `code_verifier`) rather than the substring `code`, which would have redacted every
`status_code` either service logs.)*

##### Staff in the Loop from the Claude App: technology choices

`specs/017-staff-mcp-connector/` (Phase 4a) lets a staff member pair the Claude app with
their session from the console's gear tab, then ask it, from a phone, two questions: how many
conversations need attention, and how many appointments were booked, cancelled and rescheduled
in the last N minutes. Five choices carried a real tradeoff.

- **A hand-written authorization server, not the SDK's, Authlib's or an identity provider's.**
  Claude connects only through OAuth 2.1 with discovery, dynamic registration and PKCE, so some
  authorization server has to exist. The MCP SDK ships one, and its own documentation advises new
  servers against it; Authlib is a general framework to be configured down to one public client
  type and two grants; an external identity provider needs staff accounts, which the project
  decided against in Phase 1d. None of them models the one step that matters here, a pairing code
  tied to an anonymous session, so that step would be custom code in any of them. The server is
  four small routes (`api/oauth.py`) over `chat/connectors/`, with the SDK kept for the half it is
  meant for: serving `/mcp` and checking tokens. The cost is a security mechanism this project owns,
  which is why each of its eleven invariants (plan.md's table) has a test of its own.
- **Opaque tokens looked up on every call, not JWTs.** A JWT is checked without a database read,
  and stays valid until it expires however quickly it is revoked. Revoking must stop the next call
  (FR-021), so a JWT would need a deny-list, which is the same database read plus a signing key to
  manage. Tokens are 32 random bytes stored as SHA-256 digests; a call costs one primary-key
  lookup, which also stamps "last used" at most once a minute.
- **The pairing code is the identity bridge.** There is no staff login, so the only thing that can
  prove the person connecting Claude is the person at this console is something the console shows
  them. The code is 8 Crockford base32 characters, lives 10 minutes, is keyed by the session so a
  new one replaces the old by primary key, and is spent by one conditional `UPDATE`, so two
  submissions at once pair exactly once. Its 40 bits are small, which is why each sign-in accepts 5
  wrong codes and then nothing, and why the stored digest is unsalted: by the time a stolen digest
  could be cracked the code is long dead.
- **Counts only.** The tools return integers and a sentence built from them, never a name, a
  message or an id. That keeps what reaches a third party to what the two questions need, and it
  means nothing a patient wrote can arrive in the staff member's Claude as text that reads like an
  instruction. The cost is that "which conversation?" is answered by opening the console.
- **One setting for every public address.** Claude's servers call the connector, so it needs an
  HTTPS address they can reach; the browser asking for a code is at `localhost`, which they cannot.
  `PUBLIC_BASE_URL` (an ngrok static domain locally) is the single source of the issuer, the
  connector address and the host the transport accepts, and anything but a bare `https://` origin
  makes the connector unavailable with the reason shown in the tab, rather than publishing metadata
  that names an address nobody can reach. Deriving it from the request's `Host` would give the
  wrong answer from the SPA and let any caller choose the issuer through `X-Forwarded-Host`.

#### Phase 4b — Retrieval under a realistic corpus, then hybrid search
1e built the pipeline and 2b measured it, but on a corpus too small to say anything about
retrieval: the golden corpus is 9 entries of 136-364 characters, each one chunk, so the 25-wide
pool returns the whole corpus on every search. The v2 baseline reads similarity hit@3 1.0, MRR
0.988, rerank hit@1 43/43, no answerable request unserved and no wrong abstention. Hybrid search
added now would move none of those numbers, so this phase makes the problem hard first and only
then changes the retriever, in that order.

*(Outcome, branch `018-hybrid-retrieval`: the corpus was extended and the set given cases aimed at
retrieval weaknesses, and what they exposed was not ranking. Four changes shipped, each on its own
measurement - no similarity floor in front of the reranker, a fallback floor of its own for a
reranker outage, an answerer that gives a no the information states, and chunks cut by heading
section - plus a classifier fix for the routing misses that remained. Unserved answerable requests
went from 12/87 to 1/87 with no answer on a labelled gap (baseline `01M3WD842TTD1Q9FX8TDRTMFAB`). Hybrid search was
first left unbuilt - dense search put the cited chunk in the reranker's shortlist for every
answerable request and the reranker ranked it first every time - and then built on
`018-hybrid-retrieval-bm25`, with no eval regression as its bar. Reciprocal Rank Fusion failed it;
**the shipped form adds BM25's top 3 to the dense top 5** that the reranker sees, and changed no
verdict (baseline `01M4X2XC19QBJWH8RQ9B81SAX6`). The plan below is kept as written, with notes on
what happened to each part; the measurements are in
`specs/018-hybrid-retrieval/evaluation/findings.md`.)*

- **A realistic corpus, by extension rather than replacement.** The 9 entries stay as they are,
  and documents the size a clinic actually has are added beside them — a patient handbook,
  insurance and billing policy, preparation instructions per procedure, records and privacy policy
  — tens of documents, several pages each, including near-duplicates that differ in one detail.
  Keeping the 9 keeps every existing FAQ label meaningful, and the existing cases run against the
  larger corpus measure what distractors alone cost. Long documents are what make 1e's chunking
  matter for the first time: today the 1,000-character splitter never triggers.
  *(It did matter, and was replaced: a fixed window ran across sections, so a chunk could end on a
  bare heading and set one section's sentence beside another's. An entry with headings is now
  chunked per section with its heading path as a prefix, and a headless entry exactly as before.
  `specs/018-hybrid-retrieval/evaluation/findings.md`.)*
  *(First increment on branch `018-hybrid-retrieval`: ten documents of 1.0-2.9k characters,
  bringing the corpus to 19 entries and 36 chunks. Smaller than planned, and grown further only if
  the new baseline shows retrieval still saturated. The tenth, a price list by procedure code, was
  added after the first targeted run showed dense search ranking every exact-term case first.)*
- **Every existing FAQ label re-checked against the additions**, as `PROVENANCE.md` requires when
  the corpus moves. An `answerable: false` label was checked against nine entries and may now be
  answered by a new document; an `answerable: true` label may now be answered by a second one too,
  and a label citing only the old entry would score a correct retrieval as a miss. A new document
  that disagrees with an old entry is either fixed or kept deliberately as a conflict case — never
  left by accident.
- **Golden cases aimed at known retrieval weaknesses**, declared in `golden_harness.golden_set` and
  rendered with `make eval-build-set` like every other change to the set:
  - *exact terms* a dense embedding blurs — plan names, form numbers, procedure codes, drug names;
  - *paraphrase and colloquial wording*, where dense search is strong and lexical search is not;
  - *a fact deep inside a long document*;
  - *near-miss distractors* — two sections on neighbouring subjects ("cancellation policy" against
    "late arrival policy"), where the wrong one is a confident wrong answer.
- **A new baseline and a noise band for it.** The new corpus and cases are a new set, so the v2
  baseline no longer compares and the v1 bands never did. Take the baseline, then five full runs of
  that one build through `make eval-band`, before any retriever change — a band measured earlier
  ranges over a different corpus and stops applying the moment the corpus changes.
  *(Done in a different order than planned: the floor, the answer prompt and the chunker changed
  first, each judged on replays and on movements a band could not have explained, and the band
  was measured on the build they produced - five runs, `evals/baselines/bands/`. Classification
  and retrieval did not move across them at all; only the answerer did, by ±1 request.)*
- **Hybrid retrieval: dense + BM25, fused by Reciprocal Rank Fusion.** A sparse BM25 vector is
  stored on the same Qdrant point as the dense one and written in the same upsert, so a save stays
  additive and 007's revision scheme is unchanged. One Query API call runs two `prefetch` searches
  and fuses them with `Fusion.RRF`. **The session and live-revision filter goes on both
  prefetches**: a lexical branch without it would reach points Postgres no longer vouches for.
  *(Built, but not fused. The sparse vector is on the same point, computed by Qdrant from the
  chunk text, and both searches carry the filter - as one `query_batch_points` call of two
  requests, so each branch's ranks reach the pipeline. Fused by RRF, an offline replay of the
  baseline lost three answered requests: a chunk both branches returned on common words outranked
  dense search's top hit. The shipped shortlist is the dense top 5 plus BM25's top 3, never in
  place of a dense hit; replayed and then run in full it changed no verdict and no top reranked
  chunk, at 680 chunks reranked against 545. findings.md section 11.)*
- **The fused score is a rank, not a relevance score.** RRF scores `1/(k + rank)`, so the top result
  gets the same number whether it is a perfect match or the least bad of nothing. No floor can be
  put on it: fusion decides only the order of what reaches the reranker, and the rerank floor stays
  where the abstention is decided. *(Moot: nothing is fused. The shortlist is cut by each branch's
  own rank, and the rerank floor still decides the abstention.)*
- **No similarity floor; the rerank floor is the only abstention gate.** The floor is a dense
  cosine threshold, and BM25 earns its place on exactly the chunks dense search scores low — an
  exact plan name or form number the embedding blurs — so a floor applied to the fused candidates
  would drop the chunk BM25 rescued. It turned out to be dropping dense search's own right answers
  first: on the extended corpus it stopped "PR-4", "braces" and "HC-9" with the right chunk ranked
  first. *(Shipped ahead of hybrid, on that evidence: `SIMILARITY_FLOOR` defaulted to -1.0, and
  the setting went with the BM25 build, so the cap alone picks the shortlist; measured, it answered all three and let no labelled gap through,
  at four more rerank calls in 146 cases. `specs/018-hybrid-retrieval/evaluation/findings.md`.)*
- **No unchecked fallback.** A reranker outage used to answer from the ≤5 chunks that cleared the
  similarity floor (`answered_unreranked`). Without the floor nothing in the shortlist has passed a
  relevance check, so answering from it would serve the least bad of nothing whenever the reranker
  is down. *(Shipped as a floor of the fallback's own rather than a new verdict:
  `UNRERANKED_SIMILARITY_FLOOR`, 0.25, applies only when no rerank score was obtained. The fallback
  answers from the chunks at or above it - so `answered_unreranked` keeps meaning a floor-checked
  answer - and abstains at the similarity floor when none is, which still never reads an outage as
  a rerank-floor corpus gap. It is a run condition, so a comparison names it.)*
- **An ablation, not a switch.** Which branches run is a setting recorded in `service.configured`,
  so it becomes a run condition and a comparison names it as the thing that changed. Each branch's
  rank is logged alongside the fused one in `faq.retrieval_completed`, so the harness scores each
  stage on its own. The result is a table — dense only, BM25 only, fused, each with and without the
  reranker — split by case family, and read against the band. Hybrid is kept only if the table
  says it pays for itself; "no measurable effect" is a result this document records, not one it hides.
  *(Not run as a table, and not a switch: there is one retrieval path. Each candidate's rank in
  each branch is logged in `faq.retrieval_completed`, and `retrieval`/`lexical_cap` are run
  conditions. The rows that were measured - RRF, dense plus BM25's top, and a dense-only control,
  each by offline replay of the baseline - are in findings.md section 11; "no measurable effect"
  is the result, and this document records it.)*
- **A "dense, no floor" row, so removing the floor is not credited to BM25.** Hybrid changes two
  things at once - it adds a lexical branch and it drops the similarity floor - and the first
  targeted run on the extended set put every retrieval miss on the second: "PR-4" and "braces"
  ranked the right chunk first and the floor dropped it, and an offline probe put two of family
  `w`'s billing-code questions below the floor the same way. The same probe found dense search
  ranking the cited entry first in seven of family `w`'s eight cases and BM25 in four. So the table
  carries dense retrieval with the floor removed and the rerank floor as the only gate, beside
  today's pipeline and the fused one: what separates "dense, no floor" from "fused" is BM25's own
  contribution, and what separates today's pipeline from "dense, no floor" is the floor's.
  *(Measured, and now the default: run `01M3VZWB0SA7P17DQD8S827T21` against the baseline took
  unserved answerable requests from 12/87 to 8/87, with no answer on a labelled gap. That makes
  "dense, no floor" the row hybrid has to beat, and on this corpus dense search already hands the
  reranker the right chunk every time - so "no measurable effect" is the expected outcome, and
  worth recording as one.)*

#### Phase 4d — Kubernetes
Containerize the services and deploy them to Kubernetes, with the rationale and tradeoff recorded
below like every other technology choice.

*(Status: done. The three services run from their own images under Docker Compose, in a local
k3s cluster and on a public one at `https://visitdoc.app`, each deployed in one command; the
public cluster pulls the images from GitHub's registry. The tradeoffs are in the "Running in Containers",
"Running in Kubernetes" and "Serving the Public Demo" sections below.)*

- **Containers first, under Compose.** One image per service, built from the repo root, so a
  problem of containers working together is found apart from a problem of Kubernetes.
  *(Done: two-stage `Dockerfile`s whose final image holds only the installed virtualenv and the
  migration scripts, `uv sync --locked`, a non-root uid that cannot rewrite its own code, and the
  frontend as a static bundle behind nginx. `make stack-up` runs `docker-compose.full.yml`, which
  includes `docker-compose.yml`'s datastores and adds both migrations as one-off containers.
  `.dockerignore` keeps `.env` out of the images.)*
- **A local cluster of the distribution deployed to.** k3s, run through k3d, so the Ingress and
  storage learnt on are production's. *(Done: `deploy/k3d.yaml`, one control-plane node and one
  worker, Traefik at `http://localhost:8080`; `make cluster-up` / `make cluster-down`. Images reach
  the nodes by `k3d image import`, not a registry.)*
- **The datastores, the migrations and the apps as three stages.** *(Done, in `deploy/k8s/`:
  Postgres and Qdrant as StatefulSets, each on its own `local-path` volume, gated on readiness
  only; both migrations as Jobs re-created and waited for on every deploy; chat, the scheduler
  and the frontend as Deployments with readiness and liveness probes (chat and the scheduler a
  startup probe too) that touch no dependency. Chat runs as exactly one pod replaced with `Recreate`, because its running turns
  live in process memory; the frontend runs as two. The Ingress sends everything to the frontend's
  nginx. Secrets are made at deploy time from `.env` - only the API keys and `ADMIN_SECRET` -
  and never committed; the cluster's addresses are a committed ConfigMap.)*
- **One command to deploy, and only what changed rolls.** *(Done: `make k8s-deploy` builds the
  images, imports them, applies the datastores, migrates and applies the apps, stamping each
  Deployment's pod template with the ID of the image the node holds, so a rebuilt `:latest` image
  rolls its pods and an unchanged one rolls none. `make k8s-logs` follows chat and the scheduler
  without the probes' requests; `make k8s-down` deletes the namespace, data included.)*
- **Kustomize, a base and an overlay per cluster.** *(Done: `deploy/k8s/base/` names the images
  untagged; `overlays/local/` adds the `:latest` tag and `Never` pull. The public cluster gets a
  second overlay, not a copy of the manifests.)*
- **Images a remote cluster can pull.** *(Done: `make k8s-push` pushes
  `ghcr.io/andrey-volkovitskiy/visitdoc-<app>:<12-char commit>`, refusing while the tree is dirty
  so a tag always names the code it was built from. The three packages are public, so a cluster
  needs no pull secret. Pushed from this machine, not by CI.)*
- **A public deployment**, at `https://visitdoc.app`, open to anyone with the link. *(Done, in
  this order.)*
  - `deploy/k8s/overlays/public/`: the ghcr images at a commit tag, the domain on the Ingress, TLS
    from Let's Encrypt through k3s's Traefik, rate limits, and chat's `PUBLIC_BASE_URL` and
    `LANGFUSE_ENVIRONMENT`. *(Done: the tag is a placeholder the deploy fills
    in; a `HelmChartConfig` gives k3s's Traefik the ACME resolver, the redirect to HTTPS, a
    volume for the certificate and the visitor's address; a general per-address limit covers
    every request and a tight one the requests that call Claude or Voyage. The staff connector's
    paths go from the base Ingress straight to chat, which nginx would not forward.)*
  - A VPS running k3s, open on 80 and 443 only, its API reached over an SSH tunnel, as a second
    kube context the Makefile can select - with `make k8s-admin`, a port-forward to chat, as the
    way to `/admin`, which no public route reaches. *(Done: a Hetzner server running k3s
    `v1.35.5+k3s1`, its firewall admitting 22, 80 and 443 only, `visitdoc.app` pointing at it.
    Every `k8s-*` target takes `CLUSTER=public`, needing `make k8s-tunnel` open; the cluster's own
    Postgres password and `ADMIN_SECRET` are in a gitignored `.env.public`, written once.)*
  - Spending caps in the Anthropic and Voyage consoles before the address is shared: a public demo
    spends real money on every chat. *(Done.)*
  - Deploy, check the certificate, smoke-test, and walk the staff connector's quickstart
    (`specs/017-staff-mcp-connector/quickstart.md`) against the public HTTPS address instead of
    ngrok. *(Done, first deployed on 2026-11-06: a Let's Encrypt certificate, HTTP redirected to
    HTTPS, `/admin` answered by the page rather than chat; the spend limit refusing the eleventh
    request a minute from one address while another's still passed; the session cookie `Secure`
    and `HttpOnly` in a browser, a conversation surviving a reload, and the Claude app paired
    through `https://visitdoc.app/mcp`.)*

##### Running in Containers: technology choices

The first step of Phase 4d: each service is an image, and the whole stack runs from them under
Docker Compose before anything is deployed to Kubernetes, so a problem of containers working
together is found apart from a problem of Kubernetes. Seven choices carried a tradeoff.

- **One image per service.** `services/chat/Dockerfile`, `services/scheduler/Dockerfile` and
  `services/frontend/Dockerfile`, all built from the repo root - the Python services need
  `packages/` and the workspace's one `uv.lock`. The two Python ones are near copies differing in
  the package, the port and the migrations, which was preferred to one parameterised file: the
  copies are short, and a reader sees one service's whole image in one place.
- **Two stages, the second holding only what runs.** The builder stage has uv and the source; the
  final image receives the installed virtualenv and the migration scripts and nothing else. Our
  own packages are installed from wheels (`--no-editable`) rather than copied in as source, so
  which workspace packages an image carries is read from `pyproject.toml`, not restated in the
  Dockerfile where it could fall behind. The cost is a build step a plain copy would not need.
- **`uv sync --locked`, not `--frozen`.** The build fails when `uv.lock` is stale against any
  `pyproject.toml`, rather than quietly installing what an old lock says. That check needs the
  whole workspace's manifests, so every image copies the other members' `pyproject.toml` files
  too, though it installs none of their code.
- **Not root, and unable to rewrite its own code.** The Python images run as uid 1000, by number so
  Kubernetes' `runAsNonRoot` can verify it, over files owned by root; the frontend is the
  unprivileged nginx build. A process that is compromised can read the app but not change it.
- **The frontend is a static bundle behind nginx**, which forwards the same API prefixes
  `vite.config.ts` proxies in development, with response buffering off so a reply still streams.
  The two lists of prefixes have to be kept in step by hand.
- **Two compose files, one including the other.** `docker-compose.yml` stays the datastores alone,
  for running the services by hand; `docker-compose.full.yml` includes it and adds the services, so
  Postgres and Qdrant are declared once and both modes use the same data. Migrations are one-off
  containers the services wait for, from the same image as the service they migrate - the shape a
  Kubernetes Job will take. The first switch from one file to the other recreated the Postgres
  container once (its data is in a named volume, which was untouched); switching back did not.
- **A local cluster from k3d, running k3s.** `make cluster-up` creates it from `deploy/k3d.yaml`:
  one control-plane node and one worker as Docker containers, with Traefik - k3s's built-in
  Ingress - at `http://localhost:8080`. k3s is the distribution planned for the public deployment,
  so the cluster learnt on is the one deployed to; minikube, kind and Docker Desktop's built-in
  cluster were the alternatives, and each would differ from production in its Ingress and
  storage. The images reach the nodes by `k3d image import`, not through a registry, which keeps
  a local cluster free of one until a public deployment needs it. Requires `k3d` on the `PATH`;
  `kubectl` comes with Docker Desktop.

##### Running in Kubernetes: technology choices

The second step of Phase 4d: the same three images and both datastores in the k3d cluster, from
manifests under `deploy/k8s/`. `make cluster-up` then `make k8s-deploy` builds, imports,
migrates and serves the app at `http://localhost:8080`; `make k8s-logs` follows chat and the
scheduler; `make k8s-down` deletes all of it, data included. Eight choices carried a tradeoff.

- **Kustomize, not Helm: a base and an overlay per cluster.** `deploy/k8s/base/` holds every
  object as Kubernetes itself sees it, with the images named but untagged; an overlay says what
  one cluster changes - for the k3d one (`overlays/local/`), the `:latest` tag and `Never` pull,
  in a Component the migrations and the apps share. The public deployment adds a second overlay
  rather than a copy of the manifests. Helm's templates would have made the objects unreadable
  without rendering, for options only a chart installed by other people needs. Each of base and
  overlay comes in three stages - datastores, migrations, apps - which `make` applies in turn,
  because a Job cannot simply be re-applied and the apps must not start before the migrations
  have run; a single kustomization would have applied them all at once.
- **Postgres and Qdrant as StatefulSets, each on its own volume.** One pod each, with a stable
  name, a headless Service and a volume claim of its own on k3s's `local-path` storage, which
  keeps the data in a directory of the node the pod first ran on. It survives a pod being
  replaced, not the namespace or the cluster being deleted, and it pins each datastore to its
  node. Neither has a liveness probe: a crash ends the process, which is restarted without one,
  and restarting a store that is merely slow - recovering, or loading from disk - only makes it
  slower. They gate traffic on readiness alone. Managed databases were the alternative,
  and are out of scope for a local cluster.
- **Secrets made at deploy time, never committed.** `make k8s-up` creates the Postgres Secret and
  Secret `chat` - only the API keys and `ADMIN_SECRET`, read from `.env` by
  `scripts/env-subset.py`, since `.env`'s addresses point at localhost - and the addresses the
  cluster does differ in are a committed ConfigMap. Each database URL is assembled in the manifest
  from the Secret, so the password is stored once; the cost is that it must not contain `@`, `:`
  or `/`. A Secret is base64, not encryption: anyone who can read the namespace can read the keys.
- **Migrations as Jobs, re-created on every deploy.** `make k8s-migrate` deletes and re-applies
  them, since a finished Job never runs again, and waits for both before any service starts -
  from the service's own image, as the compose migrations run. The wait is a fixed three minutes,
  so a failing migration reports its logs only once it has run out.
- **Probes that touch no dependency.** The scheduler is probed on `/health` and chat on
  `/openapi.json`, which it has no `/health` to replace; the frontend on `/`, a file in its own
  image. A database or chat outage therefore takes nothing out of service and restarts nothing
  that a restart cannot fix. Their readiness says the process answers, not that it can serve.
- **Chat as exactly one pod, replaced with `Recreate`.** Its running turns live in its own memory
  (`generation_registry`), through which a newer message cancels an older turn, so two processes
  would each miss the other's turns. An update therefore stops the old pod before starting the new
  one - a few seconds without chat - and chat cannot be scaled until that registry moves out of
  the process. The frontend, which holds nothing, runs as two.
- **`:latest` images, rolled by their ID.** The images are tagged `:latest` and run with
  `imagePullPolicy: Never`, so the nodes never reach for a registry. An unchanged tag gives
  Kubernetes no reason to replace a pod, so `make k8s-up` stamps each Deployment's pod template
  with the ID of the image the node holds: a rebuilt image rolls its pods, an unchanged one rolls
  none. A unique tag per build is the usual answer and what a registry-backed deployment will use;
  here it would have meant rewriting the manifests on every build.
- **For a cluster that pulls, GitHub's registry and a tag per commit.** `make k8s-push` builds the
  images and pushes them to `ghcr.io`, tagged with the commit they were built from, and refuses
  while anything is uncommitted - an image named for a commit but built from other edits would
  make the tag a lie that nothing catches. A deploy then names exactly what it runs, and a new tag
  is what rolls the pods. GitHub's registry is free for a public repository and sits beside the
  code, each image linked to the repository by its `org.opencontainers.image.source` label;
  Docker Hub would have meant a second account and its pull limits. The images are pushed from
  this machine, not built by CI: one person deploys, and a workflow pushing on every commit would
  publish images nobody deploys.

##### Serving the Public Demo: technology choices

The public deployment: `deploy/k8s/overlays/public/` runs the base on one k3s server at
`https://visitdoc.app`, open to anyone with the link, and every `k8s-*` target takes
`CLUSTER=public` to act on it instead of the local cluster. Once: `make k8s-public-env` writes the
cluster's own secrets, and `make k8s-public-context` adds it to `~/.kube/config`. Then, with
`make k8s-tunnel` open in another terminal, `make k8s-deploy CLUSTER=public` pushes the images
and applies everything; `make k8s-admin CLUSTER=public` reaches `/admin`, and `make k8s-db CLUSTER=public` the
datastores, for a database client. The server runs k3s
`v1.35.5+k3s1`, the version the local cluster runs. Eight choices
carried a tradeoff.

- **The images at the commit being deployed, filled in at apply time.** The overlay names the
  `ghcr.io` images with the tag `unset-deploy-tag`, which the deploy replaces in the rendered
  output with the commit it deploys. Kustomize reads no variables, so the alternative was
  committing the tag: a commit per deploy that changes nothing but which build runs. The cost is that `kubectl apply -k` alone
  deploys nothing usable; it fails to pull an image whose name says why, rather than running
  something unintended.
- **TLS from Let's Encrypt through k3s's own Traefik, not cert-manager.** A `HelmChartConfig`
  (`overlays/public/traefik/`) adds values to the Traefik k3s installs: an ACME resolver proving
  the domain over HTTP-01, a permanent redirect from port 80 to 443, and a volume for the
  certificate, since Let's Encrypt issues at most five for one name a week and a lost file would
  ask again on every restart. cert-manager is the usual answer, and stores certificates as
  Secrets any number of Traefik pods can read; for one domain behind one Traefik pod it would be
  a second component to run for nothing this one lacks.
- **Rate limits in Traefik, per client address, two of them.** A general limit against floods
  covers every request; a tight one - ten a minute - covers only the requests that call Claude or
  Voyage: a patient's turn, a new chat (and on a first arrival a new session, seeded by Voyage),
  and adding or changing a FAQ entry. An Ingress cannot match a method, and `GET /chats` spends
  nothing where `POST /chats` does, so the tight one is a Traefik `IngressRoute` of its own. At
  the edge, a request over the limit is refused before it reaches nginx or chat, and chat stays
  unaware of a deployment's limits; the cost is that the limits are a Traefik feature, and an
  open demo is bounded by them and by the providers' spending caps, not by an access code. Each
  client gets its own bucket only because Traefik's Service keeps the visitor's address
  (`externalTrafficPolicy: Local`); with the default, all of them could share one.
- **The staff connector's addresses routed to chat by the Ingress, not by nginx.** `/mcp`,
  `/oauth` and `/.well-known` are called by Claude's servers, not the page, so they are paths of
  the base Ingress to chat's Service rather than more prefixes for nginx and Vite to keep in step.
  Locally they answer 503, the connector being unavailable without a `PUBLIC_BASE_URL`;
  `/admin` is in neither list, and is reached only through the cluster.
- **The session cookie marked `Secure` by a setting, not by detecting HTTPS.**
  `SESSION_COOKIE_SECURE` is off by default and on in the public overlay. Every other way the app
  runs serves plain HTTP, over which a `Secure` cookie is never sent back - so on by default, each
  of them would start every request a new session. Reading the scheme off the request was the
  alternative, but chat sits behind two proxies, and nginx reports its own hop from Traefik, which
  is plain HTTP; nor is `PUBLIC_BASE_URL` the answer, being HTTPS (an ngrok tunnel) in development,
  where the browser is still at `http://localhost`.
- **The cluster's API behind an SSH tunnel, not open to the internet.** The server's firewall
  admits 22, 80 and 443 only, so the Kubernetes API on 6443 - from which anyone with a credential,
  or an exploit, runs any container - is reached through `make k8s-tunnel`, and the
  `visitdoc-public` context points at the tunnel's local end. A public API needs only a client
  certificate, which is what the context holds anyway; the tunnel adds the SSH key in front of
  it. The cost is a terminal held open while deploying, and every target against the public
  cluster stops first, saying so, when it is not.
- **The public cluster's own secrets in `.env.public`.** Its Postgres password and `ADMIN_SECRET`
  are random values written once (`make k8s-public-env`), gitignored and dockerignored like
  `.env`; the API keys still come from `.env`, read before it. `make k8s-up CLUSTER=public`
  refuses a missing file, the local default password, a password a database URL cannot hold, or
  `.env`'s admin secret. Written once and never regenerated, because Postgres keeps the password
  it was initialised with: a new one would lock the services out of the existing database.
  Keeping the values in the cluster alone was the alternative, and would leave nothing to read
  `ADMIN_SECRET` from but the cluster itself.
- **One server, its datastores on its own disk.** Postgres and Qdrant keep their `local-path`
  volumes on the server's disk, as in the local cluster, and nothing is replicated or backed up.
  A demo can afford that: every session is disposable, and each new one is given the starter
  corpus afresh.

---

## Practices for an AI role

- **Route models deliberately** — a cheap, fast model for classification; a stronger one for
  generation — and record the cost reasoning.
- **Structured outputs** for intents and tool arguments, not string parsing.
- **Ship a live, clickable demo** on something cheap and simple — a URL an interviewer can poke,
  prioritized over deployment sophistication.
