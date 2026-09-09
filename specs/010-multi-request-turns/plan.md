# Implementation Plan: One Message, Several Requests (Phase 1g)

**Branch**: `010-multi-request-turns` | **Date**: 2026-09-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/010-multi-request-turns/spec.md`

## Summary

Make the **request**, not the message, the unit of work. The classifier's single call starts
returning an ordered list of `{intent, text}` segments instead of a list of labels; each specialist
is handed only its own segments and never sees another's; and the retrieval pipeline runs once per
FAQ segment — concurrently, never pooled, both gates and both caps applying per run.

The approach is deliberately confined to four seams that already exist. **The classifier's output
shape changes** (`segments`, with `intents` surviving as a derived property, so every router rule
reads the same value it reads today). **The fan-out lives inside the FAQ node**, not in the graph:
`asyncio.gather` over one pipeline-and-generation task per segment, so the graph keeps its nodes, its
edges and its disjoint-state-key invariant. **Both specialists substitute their own segments into the
trailing conversation entry** through `history.replace_trailing_entry`, the function the FAQ node
already uses for its prompt — which is what makes FR-020's isolation structural rather than
instructed. **One routing-time flag splits into two decisions** — `merge_required` becomes
`specialists_collect`, and merging is decided from the parts that exist — because "do specialists
stream?" and "is there more than one reply part?" stop being the same question the moment a
two-segment FAQ half can collapse into one abstention.

No new node, no new graph edge, no new escalation cause, no new mark, no new wire field, no
migration, no frontend change, and no second model call: segmentation rides on the classification the
turn already performs. What grows is the *number of times* the existing pipeline runs — bounded at
three by the cap — and that only for messages that genuinely carry several requests.

## Technical Context

**Language/Version**: Python 3.12 (`.python-version`, workspace-wide). No frontend work in this
phase.

**Primary Dependencies**: FastAPI, Pydantic v2, LangGraph, `anthropic` (JSON Outputs, already used
for classification), Qdrant + Voyage (retrieval and reranking, unchanged), structlog via
`shared-logging`. Nothing new, and no version change.

**Storage**: PostgreSQL (`visitdoc_chat`) — **no migration, no new column, no new read API**
(FR-044a). The message keeps the single `faq_verdict` and the single citation list it stores today.
Qdrant is queried more often per turn and is otherwise untouched: no re-indexing, no schema change,
no new collection.

**Testing**: pytest (unit tier, `services/chat/tests/`). Every behavioural requirement is exercised
against a **stubbed classifier** through the existing `fake_classify_intent_client` helper in
`conftest.py` (FR-082) — the fixture now returns segments, and fixing that return value is what makes
routing, isolation, per-segment retrieval, the verdict collapse and the merge decision testable
offline and deterministically. The autouse paid-API guard keeps an unmocked model call failing
loudly. `summarize_verdict` and the citation dedupe are pure functions and are tested exhaustively
with no client at all. The committed labelled sets under `evaluation/` are data plus a manual
procedure, in no tier and in no gate — the treatment `specs/**/*.py` already has.

**Target Platform**: Linux server (WSL2 dev), browser SPA (untouched this phase).

**Project Type**: Web application — existing monorepo, work confined to `services/chat`.

**Performance Goals**: a *k*-request turn issues exactly *k* searches, at most *k* reranking calls
and at most *k* generation calls, plus at most one composing call (SC-008, SC-008a), with *k* ≤ 3.
The per-segment runs are concurrent, so a two-question turn's retrieval and generation each cost one
round trip of wall-clock rather than two (SC-009). A single-request turn issues exactly what it
issues today and makes no composing call (FR-070, SC-007).

**Constraints**: One classification call per turn (FR-071). No pooling of shortlists across segments,
before or after either gate (FR-031). No segment's answer generated from another segment's chunks,
enforced structurally (FR-033a). A dependency failure in any segment fails the whole turn (FR-037).
Nothing per-segment may be stored (FR-044a). Silence handling is untouched: a message arriving while
the assistant is silent is still never classified, so it is never segmented.

**Scale/Scope**: session-scoped demo traffic; at most three concurrent retrieval pipelines per turn.
Roughly six files changed in `services/chat/src`, six test modules touched or added, plus committed
evaluation data. No file added outside `evaluation/` — the phase adds behaviour to existing modules
rather than new modules, because it introduces no new specialist and no new node.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design. Both passes recorded.*

| Principle | Pre-Phase 0 | Post-Phase 1 | Notes |
|---|---|---|---|
| I. Phase-Gated Scope Discipline | PASS | PASS | This is Phase 1g exactly as `docs/ROADMAP.md` defines it. Phase 1h's two deliverables — the per-request verdict and partial serving — are named in the spec's Out of Scope and enforced by FR-042 and FR-044a, which is what keeps this phase from quietly becoming the next one. |
| II. AI Core Is the Centerpiece | PASS | PASS | The whole change is agent routing and retrieval. No infrastructure, no platform layer, no frontend. |
| III. Deliberate, Minimal Service Boundaries | PASS | PASS | No new service and no new cross-service call. The scheduling boundary sees the same one booking call per turn it sees today (FR-022), never one per segment. |
| IV. Structured Outputs & Decoupled Tool Interfaces | PASS | PASS | Segmentation extends the same JSON-schema-constrained output on the same cheap model, and the cap is enforced *in the schema* (`maxItems: 3`) rather than by parsing. The tool registry is untouched. |
| V. Grounded Retrieval with Mandatory Abstention | PASS | PASS | Strengthened, not weakened. Each question is retrieved for on its own evidence and cites what its own gates approved; a shortlist crowded out by a stronger sibling question is the ungrounded-citation risk this removes. Abstention is preserved whole and, per FR-042, still takes the turn's FAQ half with it. |
| VI. Documentation as a First-Class Deliverable | PASS | PASS | Four contracts below document the subsystem; `contracts/log-events.md` extends spec 008's published field contract in the same change that changes the fields (FR-063), and `docs/ROADMAP.md` 1g already describes this design. |
| VII. Clean Architecture, SOLID & Design Patterns | PASS | PASS | Nothing new is invented: the fan-out reuses one node and one state key, the isolation reuses `replace_trailing_entry`, the log attribution reuses `bound_contextvars`, and the cap reuses spec 009's schema-plus-backstop construction. The one *removal* of a shortcut — splitting `merge_required` in two — is the "one value, one meaning" rule applied the moment the two answers diverge. |
| VIII. Test-Driven Development (NON-NEGOTIABLE) | PASS | PASS | Each contract below is written as failing tests first, and `tasks.md` will enforce the ordering. The stubbed-classifier seam plus two pure functions (`summarize_verdict`, citation dedupe) mean every requirement has an offline, deterministic test. |

**On principle V and the FAQ half's all-or-nothing abstention.** FR-042 means a turn can withhold an
answer it *had*. That is not an abstention failure but a scope boundary: delivering the answerable
half alongside a named gap is Phase 1h's deliverable, and it needs 1h's composer constraint ("an
abstention may not be softened by an answered segment beside it") to be safe. Doing it here without
that constraint is precisely how a merged reply comes to soften an abstention, which is the
confabulation risk this principle exists to prevent.

**No Complexity Tracking entries.** No gate required a justification, so that section is omitted.

## Project Structure

### Documentation (this feature)

```text
specs/010-multi-request-turns/
├── plan.md              # This file
├── research.md          # Phase 0 output — 14 decisions and what each rejected
├── data-model.md        # Phase 1 output — the segment type, the derived views, the collapse rules
├── quickstart.md        # Phase 1 output — the manual walk-through
├── contracts/           # Phase 1 output
│   ├── segmentation.md        # what the classifier returns, its rules, its cap, its invalid cases
│   ├── routing.md             # segments → specialists, the overrides, what each specialist reads
│   ├── retrieval-per-segment.md  # per-run isolation, citation dedupe, verdict collapse, failure
│   └── log-events.md          # the segment field on 1e's six events, and the classification event
├── evaluation/          # tasks-phase output (data + procedure, no runner — FR-080, FR-081)
│   ├── messages.md            # the labelled sets, as prose
│   ├── procedure.md           # how the measurement is run, and every run's result
│   └── inputs/                # the same messages as JSON, plus the driver (excluded from lint/mypy)
├── checklists/
│   └── requirements.md
└── tasks.md             # /speckit-tasks output — NOT created here
```

### Source Code (repository root)

```text
services/chat/
├── src/chat/
│   ├── agent/
│   │   ├── classify_intent.py  # CHANGED: segmented response schema (maxItems 3) + prompt rules
│   │   ├── graph.py            # CHANGED: `segments` state key, per-specialist slices, the
│   │   │                       #          streaming/merging split, synthetic failure segment
│   │   ├── answer_faq.py       # CHANGED: per-segment fan-out, summarize_verdict(), citation
│   │   │                       #          dedupe, per-segment contextvar binding
│   │   ├── handle_booking.py   # CHANGED: reads its own segments; prompt states it holds no
│   │   │                       #          clinic knowledge (FR-023)
│   │   ├── compose_answer.py   # CHANGED: N answer parts instead of one FAQ half; merge decided
│   │   │                       #          from the parts present; single-part emission
│   │   ├── history.py          # UNCHANGED — `replace_trailing_entry` is reused as-is
│   │   ├── small_talk.py       # UNCHANGED — a small-talk turn is a one-segment turn
│   │   └── escalation.py       # UNCHANGED — no new cause, no new precedence entry
│   ├── domain/
│   │   ├── schemas.py          # CHANGED: RequestSegment (new); IntentClassificationResult holds
│   │   │                       #          segments, `intents` becomes a derived property
│   │   └── models.py           # UNCHANGED — no stored field changes (FR-044a)
│   ├── rag/
│   │   └── pipeline.py         # UNCHANGED — the pipeline runs more often, not differently
│   └── api/
│       └── turn.py             # UNCHANGED — the silence gate keeps unclassified messages unsegmented
└── tests/                      # test_classify_intent, test_graph, test_answer_faq,
                                # test_compose_answer, test_handle_booking, test_escalation
                                # (+ conftest: fake_classify_intent_client returns segments)

services/frontend/              # UNTOUCHED — no wire type changes, nothing new to render
```

**Structure Decision**: no new module, package, service or layer. The phase changes the *shape of one
value* (the classification result) and the *arity of one pipeline*, and both belong in the modules
that already own them. Nothing here earns its own file the way `small_talk.py` did in Phase 1f: that
was a new specialist with its own prompt and result type, while this is the same specialists reading
a narrower input.

## Phase 1 Artifacts

| Artifact | What it fixes |
|---|---|
| [`data-model.md`](./data-model.md) | `RequestSegment`, the derived `intents` view, the per-segment outcome list, and the two collapse rules — with the invariants each one must hold. |
| [`contracts/segmentation.md`](./contracts/segmentation.md) | The classifier's request schema and response shape, the six segmentation rules the prompt must carry, and the three invalid results that fall back. |
| [`contracts/routing.md`](./contracts/routing.md) | Segments → specialists, the overrides that take a whole turn, what each specialist is handed, and when the composer merges. |
| [`contracts/retrieval-per-segment.md`](./contracts/retrieval-per-segment.md) | One run per segment, no pooling, citation dedupe, the verdict collapse, and whole-turn failure. |
| [`contracts/log-events.md`](./contracts/log-events.md) | The `segment` field on Phase 1e's six events, the segmentation on the classification event, and the completion event's per-segment outcomes. |
| [`quickstart.md`](./quickstart.md) | The manual walk-through that exercises the four defects and the non-regression case in a real browser. |
