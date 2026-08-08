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

**Staff** (a later phase) work from a console that surfaces incoming escalations, lets them take
over a conversation, and shows operational analytics.

---

## Architecture — AI-core phase

| Component | Choice | Why |
|---|---|---|
| Core backend | **FastAPI** (Python) | Hosts the agent, RAG, chat, and auth. Python because the agent/RAG/eval ecosystem lives there. A single deployable for everything except Scheduling. |
| Scheduling service | **FastAPI + own PostgreSQL** | The one separated service: owns doctor calendars, availability, and booking. A clean, self-contained seam. |
| Inter-service call | **gRPC** | Availability and booking are synchronous request/response the agent needs immediately — a justified use of gRPC and one clean place to demonstrate it. |
| Relational store | **PostgreSQL** (×2 — core + Scheduling) | Structured data with real integrity needs; database-per-service across this one boundary. |
| Vector store | **Qdrant** | Embeddings for retrieval-augmented FAQ answering. |
| Frontend | **React + Vite SPA**, minimal | A streaming chat UI, kept lean. |
| Agent framework | **LangGraph** | Real branching and parallel intent handling, not just a linear chain. |
| Tracing / eval | **Langfuse** (self-hosted) | Open-source and self-hostable: trace UI, per-step latency, and token cost, with no vendor lock-in. |

**Double-booking is prevented in the Scheduling service at the database level**, using PostgreSQL's
interval/range types and an exclusion constraint in Scheduling's own database rather than relying on
application code to catch the race.

---

## Phased build plan

### Phase 0 — Walking skeleton
Prove the entire loop end to end before adding any branching. One chat endpoint, a minimal
streaming chat UI, and an agent step that does exactly one thing: answer an FAQ via RAG (message in
→ retrieval → grounded answer streaming back), implemented as a plain function call to the Claude
API — no agent framework yet, since a single linear step has no branching to justify one.

### Phase 1 — The real agent
The spine of the project, split into sub-phases small enough to build and verify one at a time. Each
builds on the ones before it; MCP stays the seam that keeps agent logic decoupled from how a
capability is actually implemented, so a later sub-phase can swap an implementation without touching
the agent.

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

#### Phase 1c — Scheduling service
Stand up Scheduling as a separate FastAPI service with its own PostgreSQL and a small gRPC API
(`CheckAvailability`, `BookAppointment`), including the double-booking guard — a PostgreSQL
exclusion constraint on interval/range types — and failure handling: timeouts, retries, and defined
behavior for callers when Scheduling is unreachable. Built and tested standalone against its own
gRPC contract, before the agent calls it.

#### Phase 1d — Booking, escalation, and real branching
Wire the agent up to what 1a–1c built:
- **MCP tool servers** — `search_faq`, `check_availability`, `book_appointment`,
  `escalate_to_staff` — keeping the agent's logic decoupled from how each capability is implemented.
- **Parallel specialist nodes with a merge step** for mixed-intent messages ("what should I bring,
  and can I move my appointment to Friday?") — the graph doing real branching.
- **Booking** flows through Scheduling: `check_availability`/`book_appointment` call it over gRPC;
  the agent code is identical whether Scheduling is in-process or remote — the tool just points at
  the service.
- The **escalation** path, which is where conversation shape actually becomes multi-party: staff
  take over and post directly into the same thread the patient sees (not a separate,
  assistant-mediated channel), so a conversation becomes a flat, ordered log of messages from any
  sender (patient, assistant, or staff). Escalation is a state on the conversation as a whole, not
  per-message: once escalated, the assistant stops generating replies in it until a staff member
  resolves it or hands it back.

#### Phase 1e — RAG done properly
Upgrade Phase 0's naive embed-and-top-k retrieval: defensible chunking, a reranking step, grounded
answers **with citations back to the source document** — derived structurally from what was actually
retrieved, never self-reported by the LLM — and an explicit **abstention path** that escalates via
1d's `escalate_to_staff` tool instead of hallucinating when retrieval is weak. A **groundedness
check** runs before any FAQ answer is returned to the user.

### Phase 2 — Evaluation & observability
The centerpiece — the ability to *measure* whether the system works, not just demo that it does:

- **A golden dataset** — 50–100 realistic patient messages labeled with expected intent(s),
  expected tool calls, and (for FAQ) the correct source document.
- **Metrics**: intent-classification accuracy, tool-selection correctness, retrieval hit@k / MRR,
  **answer groundedness** (LLM-as-judge comparing the answer against the retrieved context), and
  **end-to-end task success** (did the booking land in the correct database state?).
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
- Build the **staff console** for escalations and practitioner management.
- Containerize and deploy to Kubernetes.

---

## Practices for an AI role

- **Route models deliberately** — a cheap, fast model for classification; a stronger one for
  generation — and record the cost reasoning.
- **Structured outputs** for intents and tool arguments, not string parsing.
- **Groundedness gate** on every retrieved answer.
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
