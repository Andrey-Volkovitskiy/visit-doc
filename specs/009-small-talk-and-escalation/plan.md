# Implementation Plan: Small Talk and What Escalation Is For (Phase 1f)

**Branch**: `009-small-talk-and-escalation` | **Date**: 2026-09-08 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/009-small-talk-and-escalation/spec.md`

## Summary

Give the classifier the vocabulary to say what a message *is*, and give each answer a route that
matches it. Four labels are added and one is narrowed: `small_talk` (asks for nothing that can be
acted on), `urgent_condition`, `distress`, `booking_for_another`, with `call_staff` narrowed to an
explicit request for a human and `unknown` re-pointed from "try the FAQ path" to "a request the
assistant is not authorized to serve". Three of the new labels stop the conversation; two produce a
reply and no call to staff at all.

The technical approach is deliberately additive: **one new node module**
(`chat/agent/small_talk.py`), **one generalized node** (`hand_off`, today hard-wired to one sentence
and one cause, becomes one node keyed by cause), **one prompt addition to the booking loop** (it
establishes who an appointment is for before writing, which is what lets an ambiguous beneficiary be
asked about rather than stopping the conversation — FR-045b), and **four values added to each of
three closed enums** (`IntentLabel`, `EscalationReason`, `AttentionMark`). No new table, no migration — both
affected columns are already `String(32)` and the longest new value is 26 characters. No new
external dependency and no second model call: every decision this phase makes is read off the
classification the turn already performs, and the only generation call it adds is the small-talk
reply, which runs on the cheap model and replaces a retrieval round trip plus an abstention.

## Technical Context

**Language/Version**: Python 3.12 (`.python-version`, workspace-wide), TypeScript/React 19 for the
one frontend touch

**Primary Dependencies**: FastAPI, Pydantic v2, LangGraph, `anthropic` (JSON Outputs for
classification, already in use), structlog via `shared-logging`. Nothing new.

**Storage**: PostgreSQL (`visitdoc_chat`), **read/write unchanged** — no migration. `chats.
escalation_reason` and `messages.attention_mark` are `String(32)` and deliberately not database
enums, precisely so a new value needs no schema change (`models.py:270`). Qdrant is untouched: the
paths this phase adds never retrieve.

**Testing**: pytest (unit tier, `services/chat/tests/`), vitest (`services/frontend/tests/`). Every
behavioral requirement is exercised against a stubbed classifier through the existing
`fake_classify_intent_client` helper in `conftest.py` (FR-037); the autouse paid-API guard keeps an unmocked model
call failing loudly. The committed evaluation harness under `evaluation/inputs/` is **not** part of
any tier: it is data plus a driver for the manual procedure, so `specs/**/*.py` is excluded from
ruff and mypy — the same treatment generated code already gets, and for the same reason (it is not
application code, and reshaping it to satisfy rules written for the app would buy nothing).

**Target Platform**: Linux server (WSL2 dev), browser SPA

**Project Type**: Web application — existing monorepo (`services/chat` + `services/frontend`)

**Performance Goals**: A small-talk turn costs one classification plus one short cheap-model
generation, and **no** embedding, vector search, rerank, or scheduling call — strictly less than the
FAQ path it replaces for those messages (SC-002, SC-009). A stopping turn costs the classification
alone: its reply is a constant (SC-015). No turn that is none of these gains any work.

**Constraints**: No new datastore, table, column, or read API. No second classification call and no
new round trip (FR-002). A message arriving while the assistant is silent must remain unclassified
(FR-025a) — today's behavior, preserved by *not* touching `turn.py`'s silence gate.

**Scale/Scope**: Session-scoped demo traffic. Roughly 8 files changed in `services/chat`, 2 in
`services/frontend`, plus committed evaluation data.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design. Both passes recorded.*

| Principle | Pre-Phase 0 | Post-Phase 1 | Notes |
|---|---|---|---|
| I. Phase-Gated Scope Discipline | PASS | PASS | This is Phase 1f as `docs/ROADMAP.md` now defines it. Sub-query extraction is explicitly out (spec Out of Scope), as is Phase 2's eval runner: the labelled sets ship as data with a written procedure and no runner, following 1e's precedent. |
| II. AI Core Is the Centerpiece | PASS | PASS | The work is agent routing and escalation semantics. The one frontend change is four label strings. |
| III. Deliberate, Minimal Service Boundaries | PASS | PASS | No new service and no new cross-service call. A third-party booking is refused *before* any scheduling call, so the boundary sees strictly less traffic. |
| IV. Structured Outputs & Decoupled Tool Interfaces | PASS | PASS | The added labels extend the same JSON-schema-constrained output on the same cheap model. No new tool; the stopping paths deliberately call none. |
| V. Grounded Retrieval with Mandatory Abstention | PASS | PASS | Untouched, and the abstention path is explicitly preserved (FR-024). What changes is only which messages *reach* retrieval: a pleasantry no longer produces an abstention, which makes the remaining abstentions mean what they say. |
| VI. Documentation as a First-Class Deliverable | PASS | PASS | `docs/ROADMAP.md` 1f is already reconciled with this design; the contracts below are the subsystem documentation, and the evaluation data ships with its recorded result. |
| VII. Clean Architecture, SOLID & Design Patterns | PASS | PASS | The stopping causes are handled by *generalizing* the existing hand-off node rather than adding four near-identical nodes — the open/closed change. Cause→(text, silencing, mark) lives in one table, so the four cannot drift apart. |
| VIII. Test-Driven Development (NON-NEGOTIABLE) | PASS | PASS | Every contract file below is written as failing tests first; `tasks.md` enforces the ordering. The stubbed-classifier seam (FR-037) is what makes that possible without live calls. |

**On principle V and a stopping turn.** Three of the new causes end a turn with a constant sentence
and no retrieval. That is not an ungrounded answer: the reply makes no claim about the clinic, which
is the only thing retrieval could ground. The fixed texts are constants for exactly that reason —
the design's guarantee is that nothing in a stopping reply *can* be wrong (contracts/replies.md).

**No Complexity Tracking entries.** No gate required a justification, so that section is omitted.

## Project Structure

### Documentation (this feature)

```text
specs/009-small-talk-and-escalation/
├── plan.md              # This file
├── research.md          # Phase 0 output — the design decisions and what they rejected
├── data-model.md        # Phase 1 output — the three closed sets and the state they write
├── quickstart.md        # Phase 1 output — the manual walk-through
├── contracts/           # Phase 1 output
│   ├── classification.md      # the label set, its rules, and the routing table
│   ├── escalation-causes.md   # cause → silencing, mark, clearing, precedence
│   ├── replies.md             # the fixed texts, the small-talk prompt bounds, the merge rule
│   ├── log-events.md          # what Phase 2 computes the metric from
│   └── http-contract.md       # MessageOut / ChatDoneEvent / console label changes
├── evaluation/          # Phase 1 output (data + procedure, no runner — FR-035, FR-036)
│   ├── messages.md            # the labelled sets, as prose
│   ├── procedure.md           # how the measurement is run, and every run's result
│   └── inputs/                # the same messages as input, plus the harness that drove them
│       ├── set{A,A2,B,B2,C,D1,D2,E}.json   # 111 messages with their expected labels
│       ├── distress.json, retest.json      # the boundary set, and run 1's failures
│       ├── drive.py, sc004.py              # the drivers (excluded from lint/mypy)
│       └── README.md                       # what each file measures, and how to run it
├── checklists/
│   └── requirements.md
└── tasks.md             # /speckit-tasks output — NOT created here
```

### Source Code (repository root)

```text
services/chat/
├── src/chat/
│   ├── agent/
│   │   ├── small_talk.py       # NEW: answer_small_talk() — one cheap generation, no tools
│   │   ├── classify_intent.py  # CHANGED: prompt + label set (4 added, call_staff narrowed)
│   │   ├── graph.py            # CHANGED: routing table, hand_off keyed by cause, notice flag
│   │   ├── escalation.py       # CHANGED: precedence, silencing set, cause→text table
│   │   ├── compose_answer.py   # CHANGED: renders the not-authorized notice alongside an answer
│   │   ├── answer_faq.py       # UNCHANGED
│   │   └── handle_booking.py   # CHANGED: establishes the beneficiary first (FR-045b)
│   ├── domain/
│   │   ├── schemas.py          # CHANGED: IntentLabel +4, AnswerSource +1, MessageOut literal +4
│   │   └── models.py           # CHANGED: EscalationReason +4, AttentionMark +4, CLEARABLE_MARKS
│   ├── api/
│   │   └── turn.py             # UNCHANGED — the silence gate is what FR-025a preserves
│   └── repositories/
│       └── chat_repository.py  # UNCHANGED — the writers are already cause-agnostic
└── tests/                      # test_classify_intent, test_graph, test_escalation,
                                # test_small_talk (NEW), test_attention_marks, test_turn_api

services/frontend/
├── src/lib/
│   ├── chatStream.ts           # CHANGED: AttentionMark union +4
│   └── consoleApi.ts           # CHANGED: ATTENTION_MARK_LABEL +4
└── tests/                      # the label map's own test
```

**Structure Decision**: No new package, service, or layer. Everything lands in `services/chat`'s
existing `agent/` and `domain/` modules, plus two string-level changes in the frontend's `lib/`.
The one new file is the small-talk node, which earns its own module for the same reason
`answer_faq.py` and `handle_booking.py` have theirs: it is a specialist with its own prompt and its
own result type.
