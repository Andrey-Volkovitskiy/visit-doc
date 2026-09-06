# Implementation Plan: Reranked Retrieval Pipeline (Phase 1e)

**Branch**: `008-reranked-retrieval-pipeline` | **Date**: 2026-09-06 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/008-reranked-retrieval-pipeline/spec.md`

## Summary

Replace the FAQ path's single "is the best chunk above 0.5?" gate with a two-stage pipeline:
retrieve a 25-candidate observation pool, keep what clears a **0.3** similarity floor up to **5**,
score those with a **cross-encoder reranker**, keep what clears the rerank floor up to **3**, and
generate from exactly those. Either gate coming up empty abstains before any generation call. A
reranker failure or 5-second timeout is absorbed — the turn answers from the similarity survivors and
records that it did. The `grounded` boolean is replaced by a five-value verdict naming the gate that
stopped the turn, and every stage logs its candidates, scores, and keep/drop decisions.

The technical approach is deliberately narrow: **one new module** (`chat/rag/reranking.py`) behind
the same shape `embeddings.py` already has, **one new domain type** (`ScoredChunk`) that carries a
chunk through both stages, and **one rewritten node** (`answer_faq.py`). `chunking.py`,
`indexing.py`, `qdrant_repository.py`'s write path, and the FAQ CRUD are untouched (FR-034). Nothing
new is stored, no table is added, and no state outlives a single turn (FR-010b).

## Technical Context

**Language/Version**: Python 3.12 (`.python-version`, workspace-wide), TypeScript/React 19 for the
two frontend touches

**Primary Dependencies**: FastAPI, Pydantic v2, LangGraph, `voyageai>=0.5.0` (already present — its
`AsyncClient` exposes `rerank()` alongside the `embed()` this repo already uses), `qdrant-client`,
`anthropic`, structlog via `shared-logging`

**Storage**: PostgreSQL (`visitdoc_chat`) — one Alembic migration replacing `messages.grounded` with
`messages.faq_verdict`. Qdrant is read-only in this feature; no collection, payload, or index change.

**Testing**: pytest (unit tier, `services/chat/tests/`), vitest (`services/frontend/tests/`). The
autouse paid-API guard in `conftest.py` must gain `voyageai.client_async.AsyncClient.rerank` — a
reranking call is a paid call, and the guard exists to make an unmocked one fail loudly rather than
bill silently.

**Target Platform**: Linux server (WSL2 dev), browser SPA

**Project Type**: Web application — existing monorepo (`services/chat` + `services/frontend`)

**Performance Goals**: One added external round trip per FAQ turn that clears the similarity gate.
Time-to-first-token grows by the reranking call, bounded at 5s (FR-010a). The wider vector search
(5 → 25) is a `limit` change on a call already being made, index-backed by the payload indexes
`qdrant_repository` already creates.

**Constraints**: No new datastore, table, or read API (FR-026). No state shared across turns
(FR-010b). Per-turn log volume must stay near today's despite a 5× wider pool (SC-004a) — which is
what the 200-character truncation of discarded candidates buys (FR-027a).

**Scale/Scope**: Session-scoped corpora, ≤200 entries per session (`FAQ_MAX_ENTRIES_PER_SESSION`).
Single-user demo traffic; concurrency is per-turn, not per-tenant.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design. Both passes recorded.*

| Principle | Pre-Phase 0 | Post-Phase 1 | Notes |
|---|---|---|---|
| I. Phase-Gated Scope Discipline | PASS | PASS | This is Phase 1e as `docs/ROADMAP.md` defines it. Phase 2's belongings stay out: no Langfuse, no metric suite, no automated eval runner. The committed calibration set is data with no runner (spec Out of Scope), which the spec states explicitly so it cannot be mistaken for a golden dataset. 1f (sub-query extraction) is excluded by FR-035. |
| II. AI Core Is the Centerpiece | PASS | PASS | The feature *is* AI-core work — the retrieval pipeline itself. Frontend spend is two small changes (a marker, and not rendering citations). |
| III. Deliberate, Minimal Service Boundaries | PASS | PASS | No new service. The reranker is an external dependency of one module, and its failure handling is designed up front (FR-010, FR-010a, FR-010b) rather than added later — which is the principle's actual demand. |
| IV. Structured Outputs & Decoupled Tool Interfaces | PASS | PASS | Unchanged: the classifier still uses structured output; the tool registry is untouched. `rerank_chunks()` is a plain async function taking a domain type, matching `embed_texts()`, so `answer_faq` never sees a provider wire shape (Dependency Inversion, `.claude/CLAUDE.md`). |
| V. Grounded Retrieval with Mandatory Abstention | PASS | PASS | Strengthened. Two gates now abstain instead of one, the per-chunk floor closes the hole where a single strong chunk dragged four weak ones into the prompt, and citations are built structurally from survivors. See the note below on what "groundedness check" means after this phase. |
| VI. Documentation as a First-Class Deliverable | PASS | PASS | README's technology table gains the reranker with its tradeoff; `docs/ROADMAP.md`'s 1e bullets are reconciled with what shipped; the calibration set ships with its recorded result. Same change, not follow-up work. |
| VII. Clean Architecture, SOLID & Design Patterns | PASS | PASS | One new module with one public function; one new domain type replacing an ad-hoc positional `chunk_scores` list. `groundedness.py` is deleted rather than left as a vestigial gate — a second gate nobody calls is exactly the drift this principle exists to prevent. |
| VIII. Test-Driven Development (NON-NEGOTIABLE) | PASS | PASS | Every contract in `contracts/` is written as failing tests first. The ordering is enforced by `tasks.md`, not by intent — see the Testing Strategy note below. |

**On principle V and the word "groundedness".** The constitution requires "an explicit groundedness
check before [an answer] is returned". After this phase that check is the two gates, run *before*
generation, and it is stricter than what it replaces. What this phase does **not** add is a
post-generation verification that the produced text is entailed by its context — and that is not an
omission: `docs/ROADMAP.md` Phase 2 assigns answer groundedness explicitly to the offline eval
harness ("run offline across the labeled set rather than per turn"). The verdict rename is therefore
a correction of a misleading name, not a removal of a control: `grounded=true` never meant the answer
had been verified, it meant retrieval had cleared a threshold, which is what the verdict now says
plainly.

**No Complexity Tracking entries.** No gate required a justification, so that section is omitted
rather than left empty.

## Project Structure

### Documentation (this feature)

```text
specs/008-reranked-retrieval-pipeline/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── reranking-port.md      # the internal seam: rerank_chunks()
│   ├── pipeline-gates.md      # floors, caps, verdict decision table
│   ├── log-events.md          # the Phase 2 input contract
│   └── http-contract.md       # ChatDoneEvent / MessageOut changes
├── calibration/         # Phase 1 output (data, no runner — spec SC-008a)
│   └── questions.md           # the 20-question set; results recorded at implementation
└── tasks.md             # /speckit-tasks output — NOT created here
```

### Source Code (repository root)

```text
services/chat/
├── src/chat/
│   ├── rag/
│   │   ├── reranking.py        # NEW: rerank_chunks() — the cross-encoder call + deadline
│   │   ├── pipeline.py         # NEW: the two gates + verdict decision, pure functions
│   │   ├── retriever.py        # CHANGED: fetch the observation pool, return it whole
│   │   ├── groundedness.py     # DELETED: superseded by pipeline.py's similarity gate
│   │   ├── chunking.py         # UNCHANGED (FR-034)
│   │   ├── indexing.py         # UNCHANGED
│   │   └── default_corpus.py   # UNCHANGED
│   ├── domain/
│   │   ├── models.py           # CHANGED: messages.grounded -> messages.faq_verdict
│   │   └── schemas.py          # CHANGED: FaqVerdict enum; ChatDoneEvent/MessageOut
│   ├── agent/
│   │   ├── answer_faq.py       # CHANGED: drives the pipeline, records the verdict
│   │   └── compose_answer.py   # CHANGED: FaqResult carries ScoredChunks + verdict
│   ├── repositories/
│   │   ├── qdrant_repository.py  # UNCHANGED: search() already takes `limit`; only the
│   │   │                         #            caller's value changes (see retriever.py)
│   │   └── chat_repository.py    # CHANGED: persists the verdict
│   ├── api/turn.py             # CHANGED: writes faq_verdict instead of grounded
│   └── core/config.py          # CHANGED: 7 new settings
├── alembic/versions/           # NEW: one migration (grounded -> faq_verdict)
└── tests/                      # NEW: test_pipeline.py, test_reranking.py, test_retriever.py,
                                #      test_answer_faq.py
                                # CHANGED: conftest paid-API guard; test_config, test_migrations,
                                #      test_dependencies, test_compose_answer, test_turn_api,
                                #      test_chats_api, test_console_api, test_staff_messages,
                                #      test_graph, test_silence_gate, test_escalation,
                                #      test_attention_marks, test_anthropic_failure, test_logging
                                # DELETED: test_groundedness.py, with its module

services/frontend/
├── src/components/
│   ├── MessageView.tsx         # CHANGED: citations behind a prop; verdict marker
│   ├── ChatWindow.tsx          # CHANGED: patient pane renders no citations
│   └── StaffThread.tsx         # CHANGED: staff pane renders citations + the marker
└── tests/                      # CHANGED: MessageView/ChatWindow/StaffThread specs
```

**Structure Decision**: Existing monorepo layout, no new package or service. The one structural
judgment is splitting the new backend work across **two** modules rather than one: `reranking.py`
owns the external call and its deadline (I/O, fallible, mocked in tests), `pipeline.py` owns the
floors, caps, ordering and verdict (pure, total, exhaustively testable without a client). The gates
are where every requirement in this spec actually lives, and keeping them free of I/O is what lets
the decision table in `contracts/pipeline-gates.md` be tested as a table.

## Testing Strategy

Per constitution VIII, tests precede implementation. Concretely, the order `tasks.md` will enforce:

1. `contracts/pipeline-gates.md`'s decision table → `test_pipeline.py`, failing, before `pipeline.py`
   exists. These are pure functions, so the table is testable in full — every floor/cap boundary,
   every verdict, both empty-input cases.
2. `contracts/reranking-port.md` → `test_reranking.py`, failing, before `reranking.py` exists.
   Covers the deadline, the failure taxonomy of FR-010, and that no retry and no cross-turn state
   exist.
3. `contracts/log-events.md` → assertions in `test_pipeline.py`/`test_answer_faq.py` on captured
   structlog output, since these events are a deliverable of the phase (US4), not a side effect.
4. `contracts/http-contract.md` → `test_turn_api.py` / `test_console_api.py` extensions, then the
   frontend specs.

Two existing-suite obligations that are easy to miss and are therefore called out as tasks rather
than left to discovery: **`conftest.py`'s paid-API guard must block
`voyageai.client_async.AsyncClient.rerank`** before any reranking test is written (otherwise a
mocking mistake bills a live call and the suite still passes), and **`test_groundedness.py` is
deleted with `groundedness.py`**, not left asserting a threshold nothing reads.

The e2e tier stays empty — `docs/ROADMAP.md` Phase 2 states that filling it before 1e would assert
against a contract this phase changes, which is precisely the contract being changed here.
