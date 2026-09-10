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
  infrastructure. Out-of-band delivery (email, SMS) is deliberately deferred to Phase 3+, where a
  broker and a Notification service actually exist.
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

Operational analytics over this console stay in Phase 3+.

#### Phase 1e — RAG done properly
Upgrade Phase 0's naive embed-and-top-k retrieval into a pipeline with a defensible stage for each
job: chunking, retrieval, reranking.

- **A gated retrieval stage**, replacing Phase 0's naive top-k. It fetches a wide observation pool
  (25 by default) and keeps every chunk at or above a **minimum cosine similarity of 0.3**, up to the
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
  logs are the input Phase 2 computes its metrics and threshold adjustments from; their field
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
  `unknown` stops meaning "try the FAQ path and see": a request the assistant has no path for is
  escalated deliberately — told in fixed text that a person now has it, without silencing the
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
- **Split conservatively — under-splitting is today's behavior, over-splitting is a new failure.**
  One request stays one segment; a message splits only where the parts are independently
  answerable, and the count is capped (3 to start, revisited against Phase 2's golden set) so a
  rambling message cannot fan out without bound. *(The cap cannot be put in the schema — the API
  rejects array bounds in a constrained-output schema — so it is stated in the prompt and enforced
  on arrival: an over-long result is rejected and the turn falls back to the whole message, never
  trimmed. A four-request message is the one case the shipped classifier does not combine; see
  `specs/010-multi-request-turns/evaluation/procedure.md`, which is also the data that cap is to be
  revisited against.)*
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
  takes the whole turn, small talk is still dropped whenever any segment is a real request, and an
  `unknown` segment is still escalated deliberately.
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

### Phase 2 — Evaluation & observability
The centerpiece — the ability to *measure* whether the system works, not just demo that it does:

- **A golden dataset** — 50–100 realistic patient messages labeled with expected intent(s),
  expected tool calls, and (for FAQ) the correct source document. **The label attaches to a request,
  not to a message**, following 1g and 1h: a message carries an expected segmentation — how many
  requests, in what order, and each one's intent — and the retrieval and source-document labels hang
  off the individual request. Labeled per message, the set could not express the traffic those two
  phases exist to serve, and would have to score a turn that answered one of two questions as either
  wholly right or wholly wrong.
- **Metrics**, computed per request wherever a request is what the system decides about:
  intent-classification accuracy and **segmentation accuracy** (against 1g's labels — the expected
  number of requests, their order, and each one's intent), tool-selection correctness, retrieval
  hit@k / MRR per request, **answer groundedness** (run offline across the labeled set rather than
  per turn), and **end-to-end task success** (did the booking land in the correct database state?).
  Two more read directly on 1g and 1h: the share of answerable requests a turn left unserved, and the
  share of abstentions on questions the corpus demonstrably answers, both of which should be zero.
  All of them are computed from the per-request record 1h moved onto the message — after 1h there is
  no turn-level verdict left to compute them from, which is the point: a metric averaged over a
  summary field cannot say which of a turn's requests was the one that failed.
- **CI-gated evals** — run the suite in GitHub Actions on every commit and fail the build on a
  metric regression.
- **End-to-end tests in a real browser** — the `tests/e2e/` tier, held open since Phase 0, is filled
  here. Not earlier: 1e replaces `grounded` with a typed verdict and rebuilds citations from the
  reranked shortlist, and 1h then moves both onto the request and removes the turn-level fields
  altogether — so a suite written before either would assert against a contract already scheduled to
  change, twice. Kept deliberately small — three to five journeys aimed at the one class of
  defect no other tier can reach, **frontend state across time**, where a pane, the 2-second poll
  and a reload disagree: an escalation raised in the patient pane and answered from the staff pane,
  a pause counting down in two tabs, a booking that lands in Scheduling's own database. Driven by
  `pytest-playwright`, so the tier stays pytest behind `make test-e2e`. Alone among the tiers it may
  spend live model calls (see `docs/testing-strategy.md`) — which is exactly what keeps it off the
  per-push gate the unit tier holds, and what forces its assertions onto structure rather than onto
  the model's wording. The 13-scenario `quickstart.md` stays a manual walk-through: its value is
  that a person reads it before a demo, which automating it would remove rather than preserve.
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
