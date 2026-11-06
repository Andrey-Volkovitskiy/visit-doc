# VisitDoc — Roadmap

A conversational assistant for a medical clinic: patients book, reschedule and cancel appointments
through chat, get grounded, cited answers to policy/FAQ questions, and are escalated to human staff
when the assistant can't confidently resolve a request.

This page says only what was built in which phase. The design principles, the architecture and the
reasoning behind every phase are in [`roadmap-details.md`](roadmap-details.md).

| Phase | Theme |
|---|---|
| 0 | Walking skeleton |
| 1 | The real agent |
| 2 | Evaluation & observability |
| 3 | The frontend on its own terms |
| 4+ | Optional extensions |

---

## Phase 0 — Walking skeleton

- One chat endpoint, a minimal streaming chat UI, and FAQ answering via RAG as a plain Claude API
  call — no agent framework yet (`specs/001-grounded-faq-chat/`).
- Structured logging of each turn's decisions (`specs/002-structured-logging/`).

## Phase 1 — The real agent

| Sub-phase | What was built | Spec |
|---|---|---|
| 1a | Multi-turn conversation history per visitor | `003-conversational-chat-history` |
| 1b | LangGraph graph and intent classification with structured output | `004-langgraph-intent-classification` |
| 1c | Scheduling service (FastAPI + own Postgres, gRPC), booking by chat through an in-process tool registry | `005-scheduling-and-booking` |
| 1d | Rescheduling and cancellation; escalation and the staff console | `006-reschedule-and-cancel`, `007-escalation-and-staff-console` |
| 1e | RAG pipeline: chunking, gated retrieval, reranking, typed verdicts and citations | `008-reranked-retrieval-pipeline` |
| 1f | Small-talk intent, and escalation only when there is something to escalate | `009-small-talk-and-escalation` |
| 1g | One message split into several requests, each routed to its specialist | `010-multi-request-turns` |
| 1h | Per-request outcomes: answer the requests it can, escalate the rest | `011-answer-what-you-can` |

## Phase 2 — Evaluation & observability

| Sub-phase | What was built | Where |
|---|---|---|
| 2a | Golden dataset, labelled per request | `evals/golden/` |
| 2b | Metrics over the labelled set (golden harness) | `012-golden-set-metrics` |
| 2c | Comparing two stored runs, and the run-to-run noise band | `013-compare-eval-runs` |
| 2d | Per-turn tracing in Langfuse Cloud | `014-langfuse-tracing` |

## Phase 3 — The frontend on its own terms

| Sub-phase | What was built | Where |
|---|---|---|
| 3a | Frontend design pass; staff visibility of bookings | `015-frontend-design-pass`, `016-staff-booking-visibility` |
| 3b | End-to-end tests in a real browser | `tests/e2e/` |

## Phase 4+ — Optional extensions

| Sub-phase | What was built | Where |
|---|---|---|
| 4a | Staff in the loop from the Claude app (MCP connector) | `017-staff-mcp-connector` |
| 4b | Realistic corpus, retrieval fixes, then hybrid search (BM25 added to the dense shortlist) | `018-hybrid-retrieval` |
| 4d | Containers (Compose), Kubernetes (local k3d, public k3s at `https://visitdoc.app`) | `deploy/` |
