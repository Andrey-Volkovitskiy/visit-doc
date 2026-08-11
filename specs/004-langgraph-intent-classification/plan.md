# Implementation Plan: Adopt LangGraph + Intent Classification (Phase 1b)

**Branch**: `004-langgraph-intent-classification` | **Date**: 2026-08-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/004-langgraph-intent-classification/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

Two changes land together, per ROADMAP Phase 1b: (1) the existing FAQ-answering pipeline
(`agent/answer_faq.py`, a plain async generator) gets wrapped in a LangGraph `StateGraph`, proving
the framework swap in isolation before any real branching is added in Phase 1d; (2) every patient
message is classified into intent labels (FAQ / booking / escalation / catch-all, multi-label,
structured output via a cheap/fast model) **before** the FAQ answer is generated, and the result is
logged for later review — but classification never changes what answers the message: every message
still goes through the one FAQ-answering path regardless of its intent(s) (FR-004).

**The graph is sequential: `classify_intent_node -> answer_faq_node -> END`**, sharing the
FAQ-answering pipeline's existing cancel-and-restart lifecycle (`agent/generation_registry.py`,
unchanged) rather than running as an independently-managed task. When a message's turn is
superseded by a follow-up (spec 003 FR-015), its classification attempt is abandoned along with its
FAQ reply — no record is expected or produced for it, mirroring exactly how a superseded turn
already produces no assistant reply. Sequential ordering (classification completes before FAQ
generation starts) is deliberate, not incidental: it's what gives Phase 1d's eventual routing
decision — which specialist node(s) to launch, based on the classified intent(s) — a place to attach
in the graph; a parallel shape would have no such decision point. This does spend part of SC-004's
latency budget on classification, an accepted tradeoff the budget already anticipates. See
research.md #1/#2.

No new database table, no new HTTP endpoint, no frontend change: classification results are
logged/traced only (spec.md Assumptions), reviewed via structured logs (User Story 3), not a UI.

## Technical Context

**Language/Version**: Python 3.12 (`services/chat`, per `.python-version`) — no frontend change
this feature.

**Primary Dependencies**: One new dependency: `langgraph`, added to `services/chat/pyproject.toml`
(`uv add --package chat langgraph`), the latest stable release at implementation time. Everything
else reuses what `chat` already depends on: `anthropic` (the `AsyncAnthropic` client already shared
via `app.state.anthropic_client`, `main.py`) for both the existing Sonnet-5 generation call and the
new Haiku-4.5 classification call — no new client, no new `Settings` field; `structlog` (existing
`core/logging.py`/`core/correlation.py` — the new `intent.classified` event flows through the same
processor chain and reuses the already-bound `turn_id`, no new correlation mechanism). No new
concurrency primitive either: classification's `asyncio.Task` lifecycle is entirely governed by the
existing `generation_registry.py` (research.md #2) — no second task-tracking module.

**Storage**: No change. No new table, no new column, no Alembic migration — classified intents are
logged/traced only (spec.md Assumptions; research.md #7), not persisted relationally.

**Testing**: pytest (`services/chat/tests/`) — new `test_classify_intent.py` (structured-output
parsing via `output_config.format`/JSON Outputs, the catch-all vs. classification-failed
distinction), `test_graph.py` (the LangGraph wrapper preserves `answer_faq`'s existing
streaming/citation/abstention behavior byte-for-byte, and that both nodes run under one cancellable
task); `test_history.py` extended (new cases for `bound_to_last_n_turns`, the last-5-turns
truncation helper, research.md #5/#9); `test_chat_api.py` extended (an `intent.classified` log line
is emitted for a
message whose turn completes, and — the concrete regression test for research.md #2 — is **absent**
for a message whose turn is cancelled by a rapid follow-up, with the surviving message's own
`intents` reflecting both messages' content; plus research.md #8's own regression case —
`turn.message_received` is emitted for **every** patient message, including one whose turn is
cancelled, and always before that turn's `intent.classified`/`turn.cancelled` line).

**Target Platform**: Unchanged — Linux server (backend, local `uvicorn`).

**Project Type**: Web application — existing `services/chat` only; no `services/frontend` changes
this feature (classification has no UI surface, per spec.md User Story 3).

**Performance Goals**: SC-004 — classification must not add more than 1-2s, on average, to the time
a patient waits before their FAQ answer starts streaming. `classify_intent_node` runs sequentially
before `answer_faq_node` (research.md #1 — the ordering Phase 1d's eventual routing decision needs),
so its latency is genuinely additive to time-to-first-token this time, not free by construction. The
budget is met by model choice, not graph shape: Haiku 4.5 (research.md #4) on a short, closed-set,
structured-output call is expected to complete well within the 1-2s allowance SC-004 itself already
sets aside for this.

**Constraints**: Classification MUST use a cheap/fast model, distinct from the Sonnet-5 model
`answer_faq.py` already uses for generation (Constitution Principle IV, spec.md Assumptions) —
Haiku 4.5 (`claude-haiku-4-5-20251001`), the current cheap/fast model in the Claude 5 family
(research.md #4). Classification context is bounded to the 5 most recent conversation turns plus
the current in-progress burst (FR-006, spec.md Clarifications) — never the full chat history.
Classification failures MUST NOT fail the request or block the FAQ answer (FR-007) — caught and
recorded as a dedicated `classification_failed` label, never silently mislabeled as one of the real
intent categories (spec.md Clarifications, follow-up refinement). The `intent.classified` log event
MUST carry only the conversation turn id and the label(s) — never the patient's raw message text
(FR-005, spec.md Clarifications — a privacy consideration distinct from, and deliberately not
fixing, `turn.message_received`'s own existing raw-text content, research.md #6). This feature does
move *when* `turn.message_received` fires — earlier, ahead of classification (research.md #8) — but
leaves *what* it logs untouched; only the content question is out of scope, not the timing one.

**Scale/Scope**: Same portfolio-demo scale as specs 001/003 — a handful of concurrent visitors,
chats of a few dozen turns at most; not tuned for production load.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Phase-Gated Scope Discipline | PASS | Exactly ROADMAP Phase 1b's scope: LangGraph swap proven on the existing FAQ path alone, intent classification added but not yet acted on (FR-004 — no booking/escalation capability, no per-intent response decomposition, both explicitly deferred to Phase 1d). No new service, no new external infrastructure, no MCP tool servers (those are Phase 1d, ROADMAP), no new datastore. |
| II. AI Core Is the Centerpiece | PASS | Directly agent-framework and classification work — the two things this phase exists to prove. |
| III. Deliberate, Minimal Service Boundaries | PASS (N/A) | No new service boundary; everything lands inside the existing `chat` service. |
| IV. Structured Outputs & Decoupled Tool Interfaces | PASS | This feature *is* Principle IV's routing/classification case: structured output (native JSON Outputs, closed label set, research.md #3) on the cheapest model capable of the task (Haiku 4.5), reserving the stronger model (Sonnet 5) for generation — matches the constitution's own wording almost exactly. The "every capability MUST be exposed behind a tool-call interface" clause doesn't apply here: classification is Principle IV's *routing* case, not an agent-invocable *capability* like `search_faq`, and doesn't use tool-calling at all (research.md #3) — MCP tool servers stay Phase 1d scope (ROADMAP), unchanged by this feature. |
| V. Grounded Retrieval with Mandatory Abstention | PASS (N/A) | Untouched — `is_grounded`, citation derivation, and the abstention path in `answer_faq.py` are not modified, only wrapped in a graph node that preserves their behavior unchanged (research.md #1). |
| VI. Documentation as a First-Class Deliverable | PASS | research.md records the LangGraph-adoption shape (the sequential `classify_intent_node -> answer_faq_node` ordering, why it's deliberate rather than incidental, and the corrections that led to it, research.md #1/#2), the model-routing choice, and the context-window reuse; a new README section is a task for implementation, matching specs 001-003's precedent. |
| VII. Clean Architecture, SOLID & Design Patterns | PASS | New `agent/*.py` modules (`graph.py`, `classify_intent.py`) each own one concern and follow the existing stateless-function style (`search_faq`, `answer_faq`). Classification reuses `generation_registry.py`'s existing cancellation mechanism rather than inventing a parallel one (research.md #2) — one lifecycle mechanism governing the whole turn is a better DRY/SRP fit than two independently-reasoned-about ones. The last-5-turns windowing (`bound_to_last_n_turns`) lands in `history.py` itself, next to the module's other burst/turn functions (`split_into_bursts`, `derive_reply_to_message_ids`, `to_claude_messages`) rather than a classifier-only module — keeping this codebase's one place that interprets `Message` rows as turns/bursts consolidated, reusable by Phase 1d's specialist nodes later without an extra import edge (research.md #5). That same split also keeps `_GraphState`/`run_turn` from leaking either node's own context-formatting choice up into `api/chat.py` (research.md #9) — `api/chat.py` only ever produces `bursts`/`reply_to_message_ids`, never a node-shaped `merged_history`. |
| VIII. Test-Driven Development (NON-NEGOTIABLE) | PASS (procedural gate) | data-model.md and contracts/log-events.md define the testable surface (the structured-output schema and the log event shape) for `/speckit-tasks` to sequence tests-before-implementation against. |

No violations — Complexity Tracking table is empty.

**Post-Phase 1 re-check**: Re-evaluated against `data-model.md` and `contracts/log-events.md`
below — one new dependency (`langgraph`), one new log event type (plus one existing event's timing
relocated, research.md #8), two new `agent/` modules (`graph.py`, `classify_intent.py`) plus a small
addition to the existing `history.py`, no new runtime-state/task-tracking mechanism (classification
reuses `generation_registry.py`, research.md #2), no new table, no new endpoint, no new service.
Nothing changes any principle's status above; all PASS results stand.

## Project Structure

### Documentation (this feature)

```text
specs/004-langgraph-intent-classification/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/
│   └── log-events.md    # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
services/chat/
├── pyproject.toml                     # MODIFIED: + langgraph dependency
├── src/chat/
│   ├── agent/
│   │   ├── answer_faq.py              # MODIFIED (small): retrieve/gate/generate/stream logic itself
│   │   │                              # is untouched, the graph just wraps it (research.md #1) — its
│   │   │                              # one change is losing its own turn.message_received log call,
│   │   │                              # which moves to api/chat.py (research.md #8)
│   │   ├── graph.py                   # NEW: builds/compiles a LangGraph StateGraph, sequential:
│   │   │                              # START -> classify_intent_node -> answer_faq_node -> END.
│   │   │                              # classify_intent_node calls classify_intent(), logs
│   │   │                              # intent.classified, and always continues unconditionally
│   │   │                              # (FR-007's non-fatal handling makes this safe); answer_faq_node
│   │   │                              # wraps answer_faq(), forwarding its events via a custom stream
│   │   │                              # writer (research.md #1); exposes run_turn(...) — named for
│   │   │                              # what it does structurally (run this turn through the graph,
│   │   │                              # get back a stream of events), not for the one outcome it
│   │   │                              # always resolves to in 1b, since Phase 1d will make
│   │   │                              # "answer_faq_node" only one of several things a turn can run
│   │   │                              # through (research.md #1) — its event shape still matches
│   │   │                              # answer_faq()'s own, but NOT the same signature answer_faq()
│   │   │                              # had: run_turn(..., bursts, reply_to_message_ids) takes the
│   │   │                              # turn's raw conversation bursts, not a pre-formatted
│   │   │                              # merged_history — each node bounds/formats bursts itself via
│   │   │                              # history.py's own functions (research.md #9)
│   │   ├── classify_intent.py         # NEW: classify_intent(anthropic_client, context_messages) ->
│   │   │                              # list[IntentLabel] — native JSON Outputs structured output
│   │   │                              # (output_config.format, not tool-use) on Haiku 4.5
│   │   │                              # (research.md #3/#4); raises on failure/invalid output, does
│   │   │                              # NOT assign classification_failed itself (research.md #3 —
│   │   │                              # that's classify_intent_node's job)
│   │   ├── history.py                 # MODIFIED: build_history_messages()/last_n_turns() replaced by
│   │   │                              # four smaller, directly composable functions (research.md #5/
│   │   │                              # #9): split_into_bursts(history) -> list[list[Message]]
│   │   │                              # (promoted from a private helper); derive_reply_to_message_ids
│   │   │                              # (bursts) -> list[str] (the trailing burst's ids);
│   │   │                              # bound_to_last_n_turns(bursts, n=5) -> list[list[Message]]
│   │   │                              # (the last-5-turns truncation, replaces last_n_turns);
│   │   │                              # to_claude_messages(bursts) -> list[MessageParam] (one entry
│   │   │                              # per burst, replaces build_history_messages' formatting half)
│   │   │                              # — kept in this module, not a classifier-only one, so a future
│   │   │                              # caller (e.g. Phase 1d's specialist nodes) can reuse any of
│   │   │                              # them the same way
│   │   └── generation_registry.py     # UNCHANGED: still the one registry governing the per-chat
│   │                                  # cancellable task — now covers both graph nodes at once,
│   │                                  # since both run inside that one task (research.md #2)
│   ├── api/
│   │   └── chat.py                    # MODIFIED: run_pipeline's call to answer_faq() becomes a call
│   │                                  # to graph.run_turn() (research.md #1); classification is
│   │                                  # invoked from inside that same call, not spawned separately
│   │                                  # (research.md #2); _event_stream gains one new log call —
│   │                                  # turn.message_received, moved here from answer_faq(), logged
│   │                                  # right after history.split_into_bursts()/
│   │                                  # derive_reply_to_message_ids() compute bursts/
│   │                                  # reply_to_message_ids over history_rows (with the just-
│   │                                  # persisted patient message folded in first), before the graph
│   │                                  # task is created (research.md #8/#9)
│   ├── domain/
│   │   └── schemas.py                 # MODIFIED: + IntentLabel (StrEnum, 5 members — 4 the
│   │                                  # classifier can output plus classification_failed, which
│   │                                  # only orchestration code ever assigns, research.md #3) +
│   │                                  # IntentClassificationResult (the JSON Outputs response shape)
│   └── core/                          # UNCHANGED: logging.py/correlation.py/config.py — no new
│                                      # Settings field, no new redaction target (no new secret)
└── tests/
    ├── test_classify_intent.py        # NEW
    ├── test_graph.py                  # NEW
    ├── test_history.py                # MODIFIED: retargeted at the new split_into_bursts()/
    │                                  # derive_reply_to_message_ids()/bound_to_last_n_turns()/
    │                                  # to_claude_messages() functions, incl. bound_to_last_n_turns()
    │                                  # truncation-boundary cases
    └── test_chat_api.py               # MODIFIED: + intent.classified log-event assertions for a
                                       # completed turn, and its **absence** for a turn cancelled by
                                       # a rapid follow-up (research.md #2's regression test)

tests/integration/                      # unchanged placeholder — no cross-service surface here
tests/e2e/                              # unchanged placeholder
```

**Structure Decision**: Single project, backend-only — no new workspace member, no frontend change.
New logic lands entirely under `services/chat/src/chat/agent/`, following the existing
stateless-function-per-module style (`search_faq`, `answer_faq`) rather than introducing a class
hierarchy or a new layer. `graph.py` and `classify_intent.py` are their own small modules, not one
large file, mirroring how `generation_registry.py` already exists as its own focused module with its
own test file. The last-5-turns windowing logic (`bound_to_last_n_turns`) is deliberately *not* a
third new module — it joins `history.py`'s other burst/turn functions (`split_into_bursts`,
`derive_reply_to_message_ids`, `to_claude_messages`, research.md #5/#9) instead, since all four are
about the same thing (interpreting `Message` rows as turns/bursts) and a future caller beyond the
classifier (e.g. Phase 1d's specialist nodes) should be able to reach any of them from the one
module that already owns that concern, not from a module named for this feature's specific use of
it. No new task-lifecycle
module is introduced either: classification reuses `generation_registry.py` as-is (research.md #2).
`IntentLabel`/`IntentClassificationResult` live in `domain/schemas.py`, not `domain/models.py`,
because — unlike `MessageSender` — they're never a
SQLAlchemy column value; nothing about them is persisted (research.md #7).

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations — table intentionally empty.
