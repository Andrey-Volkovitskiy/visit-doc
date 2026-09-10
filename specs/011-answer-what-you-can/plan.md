# Implementation Plan: Answer What You Can (Phase 1h)

**Branch**: `011-answer-what-you-can` | **Date**: 2026-09-10 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/011-answer-what-you-can/spec.md`

## Summary

Move the verdict, the citations and the answer **onto the request**, and let a turn deliver the
requests it could answer beside a named gap for the ones it could not.

One value replaces two: the message's `faq_verdict` and `citations` columns give way to a single
ordered list of request outcomes — `{position, question, answer, verdict, citations}` — and every
reader re-points at it in the same change (the console, the streamed terminal event, the log, the
frontend types). Nothing derives a turn-level verdict from the list: after this phase there is no
such value to be right or wrong about.

The behavioural change is confined to three places that already exist. **`FaqResult.from_segments`
stops abstaining as a whole**: it keeps every answered request's answer and citations, and the
requests that abstained become one gap part rather than the whole half's outcome. **The composer
gains a gap block and three constraints** — an abstention may not be softened, an answer may not be
extended to cover one, and the gap is named in the composer's own words rather than by quoting the
classifier's restatement at the patient. **The escalation is unchanged** and gains nothing stored:
what staff read as "the unserved requests" is the outcomes whose verdict is an abstention, derived
at render time.

Two things do *not* change and are load-bearing. The routing-time streaming decision keeps working
untouched, because `_expected_parts` remains an upper bound on `_actual_parts` under the new part
count (answered + one gap ≤ the number of FAQ requests). And a turn whose every request abstained
still produces the constant abstention message with no composing call — the one reply the design
deliberately keeps model-free.

What is new relative to 1g: a migration (the stored shape changes), and frontend work (the wire
shape it reads is the thing being replaced). 1g had neither, deliberately; this phase cannot avoid
either.

## Technical Context

**Language/Version**: Python 3.12 (`.python-version`, workspace-wide) and TypeScript/React on the
frontend, which this phase does touch.

**Primary Dependencies**: FastAPI, Pydantic v2, SQLModel/SQLAlchemy + Alembic, LangGraph,
`anthropic` (the composing call), structlog via `shared-logging`; React + Vite. Nothing new, and no
version change.

**Storage**: PostgreSQL (`visitdoc_chat`) — **one migration**: `messages.faq_verdict` and
`messages.citations` are dropped, `messages.request_outcomes` (JSONB, nullable) is added. No
backfill and no dual-read (FR-044a): every session was deleted on 2026-09-10 (FR-044), verified
empty in the chat store, the scheduler's store and Qdrant. Qdrant and the scheduler's schema are
untouched.

**Testing**: pytest (unit tier, `services/chat/tests/`) plus vitest (`services/frontend`). Every
behavioural requirement is exercised offline against the existing stubbed-classifier and
stubbed-retrieval seams (FR-070) — a fixed segmentation plus fixed per-request verdicts is what
makes partial serving deterministic without a model call. The composer's three constraints are the
exception: they are model-obeyed, so FR-071 gives them committed data plus a written manual
procedure under `evaluation/`, in no tier and in no gate, the treatment `specs/**/*.py` already has.

**Target Platform**: Linux server (WSL2 dev), browser SPA.

**Project Type**: Web application — existing monorepo; work in `services/chat` and
`services/frontend`.

**Performance Goals**: unchanged per request — *m* generation calls for *m* answered requests, at
most one composing call (FR-060, SC-008). Turns that previously abstained without a composing call
and now merge pay one composing call; that is the phase's whole cost, and it falls only on turns
that have something to serve.

**No latency target is set for those turns, deliberately.** The call they now pay is the same
composing call a mixed-intent turn has paid since 1c, on the same model with the same budget — so
there is no new *kind* of cost to bound, only turns newly eligible for an existing one. Phases 1e
and 1g set no latency budget either, and inventing this phase's first one would mean picking a
number no measurement supports and then gating on it. What *is* bounded is the call count (FR-060),
which is the thing a wrong design would inflate; wall-clock is Phase 2's to measure once Langfuse is
recording per-step latency across every path rather than this one.

**Constraints**: no turn-level verdict anywhere (FR-002); citations deduplicated within a request,
not across the turn (FR-003); a composing failure fails the turn whole (FR-026); a dependency
failure still fails the turn whole (1g FR-037, unchanged); the all-abstained turn keeps its constant
reply and makes no composing call (FR-013).

**Scale/Scope**: session-scoped demo traffic; at most three request outcomes per message. Seven
files changed in `services/chat/src` — the two the record's shape lives in, the two that store it,
and the three agent modules that produce it — plus one migration, four frontend modules, and the
test modules that read the two removed fields.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design. Both passes recorded.*

| Principle | Pre-Phase 0 | Post-Phase 1 | Notes |
|---|---|---|---|
| I. Phase-Gated Scope Discipline | PASS | PASS | This is Phase 1h exactly as `docs/ROADMAP.md` defines it, and the ROADMAP was corrected in the same change where its Phase 2 metric no longer described what 1h ships. Nothing from Phase 2 is pulled forward: no harness, no runner, no gate. |
| II. AI Core Is the Centerpiece | PASS | PASS | Retrieval outcomes, abstention and the composed reply. The frontend work is a re-point at a changed shape, not a feature. |
| III. Deliberate, Minimal Service Boundaries | PASS | PASS | No new service, no new cross-service call. The scheduler is untouched; the session deletion that precedes the change went through the maintenance surface that already spans both stores. |
| IV. Structured Outputs & Decoupled Tool Interfaces | PASS | PASS | The classifier and the tool registry are untouched (FR-062). The composing call is generation, not routing, and stays free-text by design. |
| V. Grounded Retrieval with Mandatory Abstention | PASS | **PASS, and this is the gate to watch** | Abstention survives per request and is still mandatory; what changes is that it no longer suppresses an answer that cleared both gates. The risk moves to the composer, which is why FR-021–FR-023 are requirements, FR-071 gives them committed data, and SC-004a makes the constraint checkable *in production* from the stored parts rather than only under test. |
| VI. Documentation as a First-Class Deliverable | PASS | PASS | Four contracts below, plus `contracts/log-events.md` updating spec 010's published field contract in the same change that changes the fields (FR-053). |
| VII. Clean Architecture, SOLID & Design Patterns | PASS | PASS | The change is a removal: one summary reduction (`summarize_verdict`) and one whole-half collapse disappear, and what is left is the per-request data the pipeline already produced. The one addition — the gap prompt block — sits in the module that already owns every other block. |
| VIII. Test-Driven Development (NON-NEGOTIABLE) | PASS | PASS | Each contract is written as failing tests first; `tasks.md` will enforce the order. The stubbed seams make every requirement offline and deterministic except the three composer constraints, which get committed data and a procedure. |

**On principle V, and why this phase is the one that could weaken it.** Every earlier phase made
abstention *stronger* by giving it more places to stop. This one delivers an answer next to an
abstention for the first time, and the step that puts them in one reply has a model in it. The
mitigation is not the prompt alone: the stored record keeps the part the composer was given *and*
the reply it produced (FR-040a, FR-040b), so a softened abstention leaves evidence that SC-004a can
be checked against on real traffic. A constraint whose only witness is a test suite is a constraint
that stops holding the day the model changes.

**On the one field this phase removes without being asked to.** `turn.completed` carries both
`outcome` and `answer_source`, and once `outcome` stops being a verdict (FR-050a) the two say the
same thing in the same event. Research decision D9 removes `answer_source` from that event rather
than shipping a duplicate — the wire keeps it, where it addresses a different audience. It is an
inference from the clarification rather than the clarification itself, and is called out in the
completion report for that reason.

**No Complexity Tracking entries.** No gate required a justification, so that section is omitted.

## Project Structure

### Documentation (this feature)

```text
specs/011-answer-what-you-can/
├── plan.md              # This file
├── research.md          # Phase 0 output — 12 decisions and what each rejected
├── data-model.md        # Phase 1 output — RequestOutcome, the storage shape, the migration
├── quickstart.md        # Phase 1 output — the manual walk-through
├── contracts/           # Phase 1 output
│   ├── record.md              # stored + wire shape, the two removed fields, the migration
│   ├── composition.md         # part counting, the gap block, the three composer constraints
│   ├── escalation-and-console.md  # derived unserved requests, what staff see, what the patient does not
│   └── log-events.md          # the completion event after the summary verdict is gone
├── evaluation/          # tasks-phase output (data + procedure, no runner — FR-071)
├── checklists/
│   └── requirements.md
└── tasks.md             # /speckit-tasks output — NOT created here
```

### Source Code (repository root)

```text
services/chat/
├── src/chat/
│   ├── agent/
│   │   ├── answer_faq.py       # CHANGED: escalates when *any* request abstained, not when the
│   │   │                       #          half did; the constant message only for the all-gap turn
│   │   ├── compose_answer.py   # CHANGED: from_segments keeps answered parts; part_count = answered
│   │   │                       #          + one gap; summarize_verdict DELETED; gap prompt block;
│   │   │                       #          request_outcomes projection; completion fields
│   │   ├── graph.py            # CHANGED: collapse path and done events carry outcomes, not a
│   │   │                       #          verdict; _actual_parts follows the new part_count
│   │   ├── escalation.py       # UNCHANGED — no new cause, no new mark, no payload stored
│   │   ├── classify_intent.py  # UNCHANGED — segmentation is 1g's and is not touched (FR-062)
│   │   └── handle_booking.py   # UNCHANGED
│   ├── domain/
│   │   ├── schemas.py          # CHANGED: RequestOutcome (new); ChatDoneEvent and MessageOut lose
│   │   │                       #          faq_verdict/citations and gain request_outcomes
│   │   └── models.py           # CHANGED: Message.faq_verdict + citations -> request_outcomes
│   ├── repositories/
│   │   └── chat_repository.py  # CHANGED: the reply insert and the reads carry one field, not two
│   └── api/
│       └── turn.py             # CHANGED: stores the outcomes off the done event
├── alembic/versions/           # NEW: one revision — drop two columns, add one (no backfill)
└── tests/                      # test_answer_faq, test_compose_answer, test_graph, test_turn_api,
                                # test_chat_repository, test_console_api

services/frontend/
└── src/
    ├── lib/chatStream.ts       # CHANGED: RequestOutcome type; ChatDoneEvent/MessageOut re-shaped
    ├── components/MessageView.tsx   # CHANGED: renders one block per request outcome
    ├── components/StaffThread.tsx   # CHANGED: passes outcomes instead of verdict + citations
    └── components/ChatWindow.tsx    # CHANGED: the optimistic message carries no outcomes
```

**Structure Decision**: no new module, package, service or layer. One new type
(`RequestOutcome`) joins `Citation` in the module that already owns the wire shapes, and one pure
function (`summarize_verdict`) is deleted. The phase is a *narrowing* of what each value means, and
narrowing belongs in the module that owns the value.

## Phase 1 Artifacts

| Artifact | What it fixes |
|---|---|
| [`research.md`](./research.md) | The twelve decisions this design rests on, each with what it rejected — including why the record is a JSONB list rather than a table, and why `answer_source` leaves the completion event. |
| [`data-model.md`](./data-model.md) | `RequestOutcome`'s fields and invariants, the storage shape, what the migration does in both directions, and the four values that disappear. |
| [`contracts/record.md`](./contracts/record.md) | The stored message, the terminal event and the console read after the change; what each carries and what nothing carries any more. |
| [`contracts/composition.md`](./contracts/composition.md) | Part counting, the collapse rule, the gap prompt block, and the three constraints with the failure each one prevents. |
| [`contracts/escalation-and-console.md`](./contracts/escalation-and-console.md) | How the unserved requests are derived, what staff see, what the patient pane still does not draw, and the escalation-with-no-reply case. |
| [`contracts/log-events.md`](./contracts/log-events.md) | `turn.completed` after the summary verdict is gone: `outcome` as a shape, per-request outcomes, the `abstention_message` rule, and the queries that must re-point. |
| [`quickstart.md`](./quickstart.md) | The manual walk-through: the headline defect, the composer constraints, the staff view, and the three non-regression cases. |
