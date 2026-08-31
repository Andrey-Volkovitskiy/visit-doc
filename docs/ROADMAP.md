# VisitDoc — Roadmap

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
4. **Each significant technology choice and its tradeoff is documented in the README**, so the
   design reads as intentional.

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
single staff member, without logging in as anyone. Operational analytics follow in Phase 3+.

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
| Tracing / eval | **Langfuse** (self-hosted) | Open-source and self-hostable: trace UI, per-step latency, and token cost, with no vendor lock-in. |

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

#### Phase 1b — Adopt LangGraph + intent classification
Replace Phase 0's plain function call with a LangGraph graph, proving the framework swap on its own
before adding new capabilities on top of it. Add **intent classification** into one or more of FAQ /
booking / escalation, using a cheap, fast model and **structured output** rather than free-text
parsing. The graph still has only one real path (FAQ) at this point — branching comes in 1d.

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

**Part 2 also carries the staff-facing interface, pulled forward from what was Phase 2b**, together
with the two admin surfaces over data the assistant already depends on. The reason is manual
verification: a handoff to a human that no human can see, a practitioner list editable only with
`curl`, and an FAQ entry whose indexing state is invisible are all things that can be unit-tested
but not *exercised*. It is also what makes 1e's threshold work possible by hand — tuning an
abstention gate means editing the corpus and re-asking questions, repeatedly, in the real UI.
Mostly frontend over APIs that already exist (1c's REST admin surface, the existing FAQ CRUD); the
only new backend is what the escalation path itself needs.

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
  infrastructure. Out-of-band delivery (email, SMS) is deliberately deferred to Phase 3+, where a
  broker and a Notification service actually exist.
- **Practitioner management** — a UI over 1c's REST admin surface: add, edit, and delete
  practitioners, with the seeded-name defaults and the cascading deletes the service already
  enforces. No new backend.
- **FAQ entry management** — a UI over the existing FAQ CRUD, with one thing it must not get wrong:
  a *saved* entry and a *searchable* entry are different states. The backend's Postgres↔Qdrant
  ordering (deindex before deleting the row, delete-then-upsert on update) is what keeps the two
  consistent, and the console surfaces indexing state so a staff member can tell whether what they
  just wrote is something the assistant can actually retrieve. It is also the one admin action that
  changes what the assistant will say, which makes it the natural place to show a retrieval or eval
  effect later.
- **The session stays the only boundary — there is no staff login.** A session gets exactly one
  staff member, created with it as its patients and practitioners already are, and the app user
  simply acts as that person. Scoping is unchanged from 1c: a session sees and manages only its own
  chats, patients, practitioners, and staff member, and an id from another session resolves to
  nothing. Authentication would buy nothing this scope does not already give, and would cost the
  side-by-side demonstration that is the point of the screen. The staff member is a core-backend
  record, alongside sessions, chats, and messages — nothing about it touches Scheduling's
  invariants.

Operational analytics over this console stay in Phase 3+.

#### Phase 1e — RAG done properly
Upgrade Phase 0's naive embed-and-top-k retrieval into a pipeline with a defensible stage for each
job: chunking, retrieval, reranking, and two gates that decide whether an answer is allowed out at
all.

- **Defensible chunking**, replacing Phase 0's naive split.
- **Reranking, so retrieval can cast wider while the prompt carries less.** Vector search is a
  bi-encoder: a chunk is embedded at index time knowing nothing about the question, so cosine
  distance between two independently placed points is a blunt relevance signal — measured on the
  current corpus, two chunks answering *different* questions already score 0.57 against each other.
  A reranker is a cross-encoder, scoring query and chunk together: far more accurate, and far too
  expensive to run over a corpus, so it re-orders a shortlist the cheap retriever produced. The
  pipeline becomes retrieve wide for recall → rerank → keep the best few for precision, with both
  the prompt context and the citations built from the survivors — so a citation comes to mean "this
  is what the answer stands on" rather than "this was nearby".
- **The reranked score is a different scale and gets its own name.** Cosine similarity and
  cross-encoder relevance are not interchangeable numbers, and writing the second into the field
  holding the first would make one value mean two things depending on which path produced it. Two
  scores, two thresholds: the fallback that runs when reranking is unavailable must read the cosine
  threshold, never the relevance one.
- **Gate A — retrieval sufficiency, before generation.** *Is there material here worth answering
  from?* A per-chunk floor on the relevance score plus a minimum number of surviving chunks,
  replacing Phase 0's single top-1 test. Failing it abstains without spending a generation call.
  Cross-encoder scores are bimodal — genuinely relevant chunks pile up high, irrelevant ones near
  zero — so this threshold sits in an empty valley instead of slicing through a continuum, which is
  what makes the gate meaningful rather than nominal.
- **Gate B — answer support, after generation.** *Does the answer that came out actually follow from
  the context that went in?* A cheap, fast model as judge, with structured output, comparing the
  generated answer against the chunks that were in the prompt. It catches the failure Gate A
  structurally cannot see: retrieval was good, and the model answered partly from what it already
  knew. Gate B ships **log-only first** — the FAQ path streams, so a verdict arriving after the
  tokens cannot unsay them, and the honest order is to measure how often it would fire before paying
  the latency of buffering answers in order to act on it.
- **The turn's verdict becomes a typed outcome, not a boolean.** `grounded: true/false/null` already
  carries "no FAQ ran", "retrieval was too weak", and "we generated something"; Gate B would give
  one name five meanings. A verdict enum — not applicable, abstained (nothing retrieved / retrieved
  too weak), answered and verified, answered but unverified — keeps "we checked and it held"
  distinct from "we could not check", for the UI, the log, and Phase 2's metrics alike.
- **Citations back to the source document**, derived structurally from the chunks actually placed in
  context, never self-reported by the LLM.
- **An explicit abstention path** that escalates via 1d's `escalate_to_staff` tool instead of
  confabulating, so an abstention ends with a human rather than at a dead end.
- **Every threshold above is measured, not guessed.** Each is an operating point on a curve, and the
  curve needs Phase 2's labeled set — questions the corpus genuinely answers, and questions it
  genuinely does not — before it exists. Two error rates are tracked apart, hallucinations and false
  abstentions; for a clinic the gates are tuned toward abstaining.

#### Phase 1f — Sub-query extraction
1c routes a mixed-intent message to two specialists at once, but hands each of them the *whole*
message — so the FAQ node retrieves against "what should I bring, and can I book Friday?" rather
than against the question inside it. The scheduling half of that sentence is dense with vocabulary
the FAQ corpus also contains, so it pulls booking-flavored chunks into the top-k, dilutes the
scores the gates read, and produces citations for chunks that answer nothing the patient asked.

- **The classifier returns sub-queries, not just labels.** Its structured output becomes one entry
  per detected intent — the label, plus the part of the message carrying it, rewritten to stand on
  its own. Same cheap model, same single call, one schema change: no second model round trip.
- **Each specialist receives its own sub-query**, so retrieval embeds the question alone and its
  score reflects the question alone.
- **An extracted sub-query has to be self-contained.** "and can I book Friday?" means nothing
  without its referent, and retrieval sees the sub-query with no conversation around it — so
  extraction is also decontextualization, resolving pronouns and elisions against the turn's
  history.
- **The booking specialist still gets the whole conversation.** Only retrieval needs an isolated
  question; dialogue policy needs history, and stripping it would break exactly the multi-turn
  confirmation flow 1c built.
- **Single-intent turns are unaffected** — the sub-query is the message, and the FAQ path behaves
  as it does today.

This has no dependency on 1e and can be built before it. If 1e's thresholds are calibrated first,
they need re-checking afterwards: this phase changes the text those scores are measured against.

### Phase 2 — Evaluation & observability
The centerpiece — the ability to *measure* whether the system works, not just demo that it does:

- **A golden dataset** — 50–100 realistic patient messages labeled with expected intent(s),
  expected tool calls, and (for FAQ) the correct source document.
- **Metrics**: intent-classification accuracy, tool-selection correctness, retrieval hit@k / MRR,
  **answer groundedness** (1e's Gate B judge, run offline across the labeled set rather than per
  turn), and **end-to-end task success** (did the booking land in the correct database state?).
- **CI-gated evals** — run the suite in GitHub Actions on every commit and fail the build on a
  metric regression.
- **Tracing with Langfuse** — per-step latency, token cost, and the full decision trace for each
  turn.

### Phase 3+ — Platform layers (optional, if time allows)
Added as deliberate evolution, each with a one-line rationale in the README:

- Extract further services from the core (Scheduling already stands alone; Patient, Knowledge, and
  Escalation are the natural next cuts).
- Introduce **one** message broker plus the **transactional outbox** pattern and **idempotent
  consumers** (at-least-once delivery plus idempotency gives effectively-once processing).
- Add ClickHouse and an event stream for the analytics dashboard.
- Extend 1d's staff console with operational analytics, and with escalation notifications that
  reach staff out of band (email/SMS) once a broker and a Notification service exist.
- Containerize and deploy to Kubernetes.

---

## Practices for an AI role

- **Route models deliberately** — a cheap, fast model for classification; a stronger one for
  generation — and record the cost reasoning.
- **Structured outputs** for intents and tool arguments, not string parsing.
- **Two groundedness gates** — retrieval sufficiency before generating, answer support after —
  with abstention as a first-class outcome rather than a failure.
- **Ship a live, clickable demo** on something cheap and simple — a URL an interviewer can poke,
  prioritized over deployment sophistication.

---

## Target architecture (Phase 3+ reference)

The fuller microservices shape, kept as the destination if the project is extended. Database-per-service,
synchronous gRPC where a request needs an immediate answer, asynchronous messaging for the event
stream, all behind an Nginx gateway.

| Service | Responsibility | Data store |
|---|---|---|
| API Gateway | Routing, TLS, rate limiting, WebSocket passthrough | — (Nginx) |
| Auth | Login, JWT issuance, token/session cache | PostgreSQL |
| Patient | Patient profiles and contact records | PostgreSQL |
| Scheduling | Doctor calendars, availability, booking | PostgreSQL |
| Notification | Confirmations, reminders, alerts | (document store) |
| Chat / Agent Orchestration | Conversation loop, intent routing, tool calling | (document store) |
| Knowledge (RAG) | Clinic document ingestion, semantic search | Qdrant + doc store |
| Escalation | Routes unresolved/urgent cases to staff | (document store) |
| Analytics | Operational reporting | ClickHouse |
| Staff Console | Internal UI backend for staff workflows | reads across the above |

Some of these stores can collapse into PostgreSQL (chat transcripts, notification log, and
escalation records work well as JSONB); the choice per service is documented in the README.
