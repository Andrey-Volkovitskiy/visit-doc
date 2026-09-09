---

description: "Task list for Phase 1g — One Message, Several Requests"
---

# Tasks: One Message, Several Requests (Phase 1g)

**Input**: Design documents from `/specs/010-multi-request-turns/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: Per the constitution's Test-Driven Development principle (VIII, non-negotiable), every
test task below precedes its implementation and MUST be observed failing first. Every behavioural
requirement is testable offline against the stubbed classifier (FR-082); only segmentation *quality*
needs live calls, and that is the manual procedure in Phase 8.

**On the two classifier-prompt tasks (T007, T033).** A prompt rule's *effect* cannot be asserted
offline — it is a property of a model's output — so each is preceded by a test that the rule is
present in the system prompt, and its effect is measured once by the Phase 8 procedure. That is the
deliberate exception to "tests define the behaviour", and it is the same treatment spec 009 gave its
classifier prompt. No other task takes it.

**Organization**: grouped by user story. Note honestly: this feature reshapes one pipeline rather
than adding five separable features, so Phase 2 is heavier than usual and US1 carries the bulk of the
change. Each story is still independently *testable* — the checkpoints say how.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable — different file, no dependency on an unfinished task
- **[Story]**: US1–US5 from [spec.md](./spec.md)
- Paths are repository-relative and exact

## Path Conventions

Monorepo, work confined to the `chat` service: source under `services/chat/src/chat/`, unit tests
under `services/chat/tests/`. No frontend, no migration, no new module.

---

## Phase 1: Setup

**Purpose**: establish a baseline so any later failure is attributable to this feature

- [X] T001 Run `make lint`, `make typecheck` and `make test-unit` from the repo root and record the passing test count as the pre-change baseline
- [X] T002 [P] Confirm the plan's assumptions still hold in `services/chat/src/chat/agent/`: `_select_specialists` and `_GraphState` in `graph.py`, `replace_trailing_entry` in `history.py`, `_run_pipeline` and `trailing_question` use in `answer_faq.py`, `FaqResult` and `_build_prompt` in `compose_answer.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: every turn becomes segmented, and a one-segment turn behaves exactly as it does today

**⚠️ CRITICAL**: no user story can begin until this phase is complete — all five read segments

### Tests (write first, confirm failing) ⚠️

- [X] T003 Write failing tests for the segmented result shape in `services/chat/tests/test_classify_intent.py`: 1–3 segments accepted, 0 or 4 rejected, empty/whitespace `text` rejected, `intents` derived in segment order, `CLASSIFICATION_FAILED` in a response rejected, `cap_bound` defaulting to false — plus an assertion that segmentation rules 1–3 and 6 are present in the system prompt
- [X] T004 [P] Write failing tests for the classifier node's fallback in `services/chat/tests/test_graph.py`: an invalid or failed classification yields exactly one synthetic segment carrying the trailing patient message, routes to the FAQ path, and produces no small-talk reply and no not-authorized escalation (FR-008)

### Implementation

- [X] T005 Add `RequestSegment` and reshape `IntentClassificationResult` (`segments` with `min_length=1`/`max_length=3`, `cap_bound: bool = False`, `intents` as a derived property) in `services/chat/src/chat/domain/schemas.py` per [data-model.md §1–2](./data-model.md)
- [X] T006 Update the JSON Outputs request schema in `services/chat/src/chat/agent/classify_intent.py`: `minItems: 1`, `maxItems: 3`, `cap_bound`, the existing `CLASSIFICATION_FAILED` enum exclusion preserved, plus the parsed-result backstop that raises `ClassificationFailedError` on an over-long list or an empty segment text
- [X] T007 Add segmentation rules 1–3 and 6 (requests only; standalone restatement; restate, never add; read against the conversation) to the classifier system prompt in `services/chat/src/chat/agent/classify_intent.py`, per [contracts/segmentation.md](./contracts/segmentation.md)
- [X] T008 Add the `segments` key to `_GraphState`, write it from `classify_intent_node`, and synthesize `[{CLASSIFICATION_FAILED, <trailing message>}]` on the failure branch, in `services/chat/src/chat/agent/graph.py`
- [X] T009 Update `fake_classify_intent_client` to return segments, keeping a label-only convenience shape for existing callers, in `services/chat/tests/conftest.py`
- [X] T010 Run T003–T004 to green, then `make test-unit` to confirm no existing test regressed against the T001 baseline

**Checkpoint**: every turn carries segments; a single-segment turn is byte-identical to today

---

## Phase 3: User Story 1 - Two questions in one message are two questions (Priority: P1) 🎯 MVP

**Goal**: each FAQ request is retrieved for, gated and answered on its own evidence, and the reply
answers both halves with deduplicated citations

**Independent Test**: with two corpus entries that each answer a different question, ask both in one
sentence and confirm the turn answers both rather than abstaining, with each half's chunks visible
separately in the log

### Tests for User Story 1 (write first, confirm failing) ⚠️

- [X] T011 [P] [US1] Write failing tests in `services/chat/tests/test_answer_faq.py`: `summarize_verdict` over all three rules exhaustively (abstention wins, then `answered_unreranked`, else `answered`; first abstaining segment in message order), and citation dedupe on `(entry_id, chunk_index)` preserving first-appearance order
- [X] T012 [P] [US1] Write failing tests in `services/chat/tests/test_compose_answer.py`: N answer parts merged into one reply; a single part emitted with **no** composing call; citations carried through, never re-reported; the composer's existing claim-preservation constraints unchanged
- [X] T013 [P] [US1] Write failing tests in `services/chat/tests/test_graph.py`: two FAQ segments collect rather than stream and produce one merged reply; both abstaining collapses to one abstention part with no composing call (FR-051a); **one answered and one abstaining also abstains as a whole** — no answered text reaches the reply, one corpus-gap escalation, `faq_verdict` equal to the first abstaining segment's (FR-042, FR-043); one segment's search failure fails the whole turn with no partial delivery (FR-037)
- [X] T014 [US1] Extend `services/chat/tests/test_answer_faq.py` with the fan-out tests: one pipeline run per segment using that segment's text as the query, no shortlist pooling across segments, both gates applied per segment, per-segment reranking degradation recorded as `answered_unreranked`, and at most one search/rerank/generation call per segment. Include a **concurrency** test proving the runs overlap rather than serialize (FR-032, SC-009): a fake search that blocks until every segment has entered it, so a sequential loop deadlocks and the gather passes — deterministic, with no timing assertion

### Implementation for User Story 1

- [X] T015 [US1] Add `FaqSegmentAnswer` and reshape `FaqResult` (`segment_answers`, `answer_text: str | None`, deduped `citations`/`scored_chunks`, summary `verdict`) in `services/chat/src/chat/agent/compose_answer.py` per [data-model.md §3–4](./data-model.md)
- [X] T016 [US1] Implement `summarize_verdict()` and the citation dedupe helper as pure functions in `services/chat/src/chat/agent/answer_faq.py` per [data-model.md §5.1](./data-model.md)
- [X] T017 [US1] Extract the single-segment path in `answer_faq()` into a per-segment coroutine (pipeline → gates → generation over that segment's shortlist alone) taking the segment text where `trailing_question(bounded)` is read today, in `services/chat/src/chat/agent/answer_faq.py`
- [X] T018 [US1] Fan out one task per FAQ segment with `asyncio.gather` (default `return_exceptions=False`, so the first failure cancels its siblings and propagates — FR-037) in `services/chat/src/chat/agent/answer_faq.py`
- [X] T019 [US1] Assemble the turn-level `FaqResult` — reply parts per [data-model.md §4](./data-model.md), deduped citations, summary verdict, `segment_answers` in segment order — in `services/chat/src/chat/agent/answer_faq.py`
- [X] T020 [US1] Teach `compose_answer()` and `_build_prompt()` to take N labelled answer parts instead of one FAQ half, in `services/chat/src/chat/agent/compose_answer.py`
- [X] T021 [US1] Split the routing-time flag in `services/chat/src/chat/agent/graph.py`: rename `merge_required` to **`specialists_collect`** — the streaming decision it still makes — count it from `len(faq_segments) + booking + notice`, and pass the FAQ node its segments
- [X] T022 [US1] Decide merging in `compose_answer_node` from the parts that actually exist, emitting a single part directly (abstention message via `ChatDoneEvent.message`, as today) with no composing call, in `services/chat/src/chat/agent/graph.py`
- [X] T023 [US1] Run T011–T014 to green, then `make test-unit`

**Checkpoint**: a compound question is answered instead of abstaining; US1 is demonstrable on its own via [quickstart.md](./quickstart.md) scenarios 1–2

---

## Phase 4: User Story 2 - A question and a booking stop damaging each other (Priority: P1)

**Goal**: the FAQ specialist never sees a scheduling clause and the booking specialist never sees a
corpus question — structurally, not by instruction

**Independent Test**: send messages pairing a corpus question with a scheduling request and confirm
the FAQ half raises no escalation for the scheduling clause and the booking half states no clinic
fact

### Tests for User Story 2 (write first, confirm failing) ⚠️

- [X] T024 [P] [US2] Write failing tests in `services/chat/tests/test_handle_booking.py`: the prompt the booking loop sends contains its own segments and **not** the FAQ clause; two booking segments enter the tool loop **once**, not twice (FR-022); the bounded conversation history still reaches the loop around the substituted entry (FR-021); the system prompt states it holds no clinic knowledge (FR-023)
- [X] T025 [P] [US2] Write failing tests in `services/chat/tests/test_graph.py`: on a mixed FAQ+booking turn the FAQ node retrieves for the FAQ segment only, no escalation is raised by the scheduling clause, and the two halves arrive as one merged reply

### Implementation for User Story 2

- [X] T026 [US2] Accept the booking segments in `handle_booking()` and substitute them into the trailing conversation entry via `history.replace_trailing_entry`, in `services/chat/src/chat/agent/handle_booking.py`
- [X] T027 [US2] Add the "you hold no clinic knowledge and answer nothing outside your own segments" instruction to the booking system prompt in `services/chat/src/chat/agent/handle_booking.py`
- [X] T028 [US2] Pass each specialist its own segment slice from `handle_booking_node` and `answer_faq_node` in `services/chat/src/chat/agent/graph.py`
- [X] T029 [US2] Run T024–T025 to green, then `make test-unit`

**Checkpoint**: US1 and US2 both hold; [quickstart.md](./quickstart.md) scenarios 3–4 pass

---

## Phase 5: User Story 3 - One request still costs exactly one path (Priority: P2)

**Goal**: the common case pays nothing — one segment, one specialist, no merge, no extra call

**Independent Test**: send ordinary single-request messages, including long comma-heavy ones, and
confirm one segment, today's streaming path, and no composing call

### Tests for User Story 3 (write first, confirm failing) ⚠️

- [X] T030 [P] [US3] Write failing tests in `services/chat/tests/test_graph.py`: a single-segment turn streams its specialist's tokens, emits the same terminal event as today, records `answer_source: faq`, and makes **no** composing call (FR-051, FR-070); and a **three-segment** turn still makes exactly **one** classification call (FR-002, FR-071)
- [X] T031 [P] [US3] Write failing tests in `services/chat/tests/test_answer_faq.py`: a one-segment turn issues exactly one search, at most one rerank and at most one generation call, and builds the same prompt shape it builds today (SC-007)

- [X] T032 [P] [US3] Write a failing test in `services/chat/tests/test_classify_intent.py` asserting segmentation rules 4–5 (split conservatively; combine above the cap, dropping nothing) and the `cap_bound` instruction are present in the system prompt

### Implementation for User Story 3

- [X] T033 [US3] Add segmentation rules 4–5 (split conservatively; combine above the cap, dropping nothing) and the `cap_bound` instruction to the classifier system prompt in `services/chat/src/chat/agent/classify_intent.py`
- [X] T034 [US3] Run T030–T032 to green, then `make test-unit`, and diff the turn events of a single-request turn against the T001 baseline to confirm nothing new appears

**Checkpoint**: the common case is provably unchanged; [quickstart.md](./quickstart.md) scenarios 5–7 pass

---

## Phase 6: User Story 4 - The earlier phases' routing rules survive, read per segment (Priority: P2)

**Goal**: every Phase 1c and 1f rule behaves exactly as it does today, now evaluated over segments

**Independent Test**: pair an ordinary request with each overriding intent in turn and confirm each
turn behaves as that intent alone behaves today

### Tests for User Story 4 (write first, confirm failing) ⚠️

- [X] T035 [P] [US4] Write failing tests in `services/chat/tests/test_graph.py`: `call_staff`, `urgent_condition`, `distress` and `booking_for_another` on **any** segment take the whole turn — no retrieval, no booking, the fixed reply, the right cause, the assistant silent (FR-011, FR-012)
- [X] T036 [P] [US4] Write failing tests in `services/chat/tests/test_escalation.py`: a small-talk segment beside any request routes nowhere; an `unknown` segment beside something servable becomes a notice while alone it hands off; a turn raises at most one escalation however many segments failed (FR-013, FR-014, FR-045)

### Implementation for User Story 4

- [X] T037 [US4] Keep `_select_specialists` and `_handoff_reasons` reading the derived intents unchanged, and keep the small-talk drop as the router-side half of the belt-and-braces rule, in `services/chat/src/chat/agent/graph.py`
- [X] T038 [US4] Run T035–T036 to green plus `services/chat/tests/test_attention_marks.py` and `test_small_talk.py` unchanged, then `make test-unit`

**Checkpoint**: no routing regression; [quickstart.md](./quickstart.md) scenarios 8–9 pass

---

## Phase 7: User Story 5 - The record says which question produced which retrieval (Priority: P3)

**Goal**: two concurrent pipelines' events are attributable to the request that produced them

**Independent Test**: run one two-question turn and reconstruct both questions, both pools, both gate
decisions and both outcomes from the log alone

### Tests for User Story 5 (write first, confirm failing) ⚠️

- [X] T039 [P] [US5] Write failing tests in `services/chat/tests/test_answer_faq.py`: all six Phase 1e events plus `turn.retrieval_skipped_empty_corpus` carry `segment`, and concurrent segments do not leak each other's binding (FR-060)
- [X] T040 [P] [US5] Write failing tests in `services/chat/tests/test_graph.py`: `intent.classified` carries `segments` (position, intent, text) and `cap_bound`; `turn.completed` carries `segment_count` and `segment_verdicts`; the FAQ node span carries `segment_count` (FR-061, FR-062)

### Implementation for User Story 5

- [X] T041 [US5] Bind `segment=<position>` with `structlog.contextvars.bound_contextvars` for the whole of each segment's task in `services/chat/src/chat/agent/answer_faq.py` (research D10)
- [X] T042 [US5] Log `segments` and `cap_bound` on `intent.classified` in `services/chat/src/chat/agent/graph.py`
- [X] T043 [US5] Carry `segment_count` and `segment_verdicts` into the `TurnCompletion` fields in `services/chat/src/chat/agent/compose_answer.py` and set them from both branches of `compose_answer_node` in `services/chat/src/chat/agent/graph.py`
- [X] T044 [US5] Add `segment_count` to the FAQ node span in `services/chat/src/chat/agent/graph.py`
- [X] T045 [US5] Run T039–T040 to green, then `make test-unit`

**Checkpoint**: all five stories hold; the log is readable for a three-request turn

---

## Phase 8: Verification Data & Manual Measurement (FR-080, FR-081)

**Purpose**: committed labelled sets plus a written procedure — data, not a runner, and in no gate

- [X] T046 [P] Write the labelled sets as prose in `specs/010-multi-request-turns/evaluation/messages.md`: compound questions whose halves are individually answerable (≥10, SC-001), question+booking pairs (≥20, SC-002), multi-request messages with an expected segmentation (≥25, SC-003), single-request messages including long and repetitive ones (≥20, SC-004), and multi-request messages carrying an overriding intent (≥15, SC-012)
- [X] T047 [P] Commit the same messages with their expected segmentation as JSON under `specs/010-multi-request-turns/evaluation/inputs/`
- [X] T048 Write the driver in `specs/010-multi-request-turns/evaluation/inputs/drive.py` (excluded from ruff and mypy by the existing `specs/**/*.py` rule — do not add application code here)
- [X] T049 Write `specs/010-multi-request-turns/evaluation/procedure.md`: how to run the measurement, what each set measures, and a results section to record every run
- [X] T050 Run the measurement live against a running stack, record the result in `procedure.md`, and — if SC-003 (≥90%), SC-004 (≥95%), SC-005 (0 invented constraints) or SC-006 (0 over-cap) misses — adjust the classifier prompt in `services/chat/src/chat/agent/classify_intent.py`, re-run, and record both runs

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T051 [P] Annotate Phase 1g's bullets in `docs/ROADMAP.md` with what shipped and where, and state the 1h boundary (one turn verdict, all-or-nothing FAQ abstention), following the "*(Shipped in `specs/...`)*" pattern 1e already uses
- [X] T052 [P] Add the request-segmentation design decision to "Key design decisions to preserve" in `.claude/CLAUDE.md`, alongside the 007/008/009 entries
- [X] T053 [P] Add a pointer from `specs/008-reranked-retrieval-pipeline/contracts/log-events.md` to `specs/010-multi-request-turns/contracts/log-events.md`, so the published field contract names its extension (FR-063)
- [X] T054 Run `make lint`, `make typecheck` and `make test-unit`; confirm the pass count exceeds the T001 baseline and that nothing under `services/frontend/` changed
- [X] T055 Walk [quickstart.md](./quickstart.md) end to end against `make services-up`, including scenario 10's deliberate Phase 1h limitation, then `make services-down`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — **blocks every user story**, since all five read segments
- **US1 (Phase 3)**: depends on Phase 2. Carries the fan-out, the collapse rules and the composer change
- **US2 (Phase 4)**: depends on Phase 2; the merged-reply assertions in T025 depend on US1's composer work (T020–T022)
- **US3 (Phase 5)**: depends on Phase 2 and on US1's part-counting (T021–T022), which is what makes the single-part path provable
- **US4 (Phase 6)**: depends on Phase 2 only — the override rules read the derived intent set
- **US5 (Phase 7)**: depends on Phase 2; T039 depends on US1's fan-out existing (T018)
- **Verification (Phase 8)**: depends on US1–US4 being complete — the measurement exercises real routing
- **Polish (Phase 9)**: depends on everything

### Honest note on story independence

US1 is not a slice that can ship without the rest of Phase 2, and US2/US3/US5 build on US1's
composer and fan-out. What each story *is* independently is **testable**: each phase's tests fail
before its implementation and pass after it, without needing the next story's code.

### Within Each Story

- Tests written and observed failing before implementation (constitution VIII, no exceptions)
- Types before the functions that return them (T015 before T017–T019)
- Pure functions before the code that calls them (T016 before T019)
- Node changes after the specialist changes they call (T021–T022 after T017–T020)

### Parallel Opportunities

- T003 and T004 (different test files) — Phase 2's two test tasks
- T011, T012, T013 — US1's three test files, all independent
- T024 and T025 — US2's two test files
- T030, T031 and T032 (three different test files), T035 and T036, T039 and T040 — the same pattern per story
- T046, T047 (different files) and T051, T052, T053 (three different documents)

---

## Parallel Example: User Story 1

```bash
# The three test files are independent — write them together, confirm all three fail:
Task: "Pure-function tests (summarize_verdict, citation dedupe) in services/chat/tests/test_answer_faq.py"
Task: "N-part merge and single-part emission in services/chat/tests/test_compose_answer.py"
Task: "Two-segment routing, collapse, and whole-turn failure in services/chat/tests/test_graph.py"

# Implementation is mostly sequential — three of the four tasks touch answer_faq.py:
# T015 (compose_answer.py types) → T016 → T017 → T018 → T019 → T020 → T021 → T022
```

---

## Implementation Strategy

### MVP (Phases 1–3)

1. Setup, then Foundational — every turn segmented, one-segment turns unchanged
2. US1 — the headline defect fixed: a compound question is answered instead of abstaining
3. **STOP and VALIDATE** with quickstart scenarios 1–2 and a full `make test-unit`

That is a demonstrable phase increment on its own: the mixed-intent damage (US2) is still present,
but no message behaves worse than it does today.

### Incremental Delivery

1. Phases 1–2 → foundation, no behaviour change
2. + US1 → compound questions answered (MVP)
3. + US2 → mixed FAQ/booking messages stop damaging each other
4. + US3 → the common case proven unchanged
5. + US4 → the safety and routing rules proven intact
6. + US5 → the record becomes readable per request
7. + Phases 8–9 → the measurement recorded and the documentation reconciled

### Suggested commit boundaries

One commit per checkpoint, prefixed `feat: 010 - …` / `fix: 010 - …` per the repo's convention. The
test task and its implementation belong in the same commit only if the tests were observed failing
first — the failing run is the evidence the constitution's TDD principle asks for.

---

## Notes

- No migration, no new column, no wire-field change, and nothing under `services/frontend/` — if a
  task seems to need one of those, it has drifted from [spec.md](./spec.md) FR-044a
- Do not put application code under `specs/` — Phase 8's driver is excluded from lint and mypy
  precisely because it is not application code
- Phase 1h is out of scope throughout: no per-request verdict storage, no partial serving, no
  escalation carrying the specific unanswered question

---

## Phase 10: Convergence

**Purpose**: close the gaps a post-implementation assessment found between the artifacts and the code

- [X] T056 Record each request's own answer text in the FAQ node span in `services/chat/src/chat/agent/graph.py` — `answer_text` is None on every multi-request turn while the comment beside it claims the half's words are always recorded, so on a two-question turn neither answer appears in any record; add the per-request texts (with question and verdict), write the test first in `services/chat/tests/test_graph.py`, and make the comment say what the code does, per FR-044, FR-062 (partial)
- [X] T057 Add a "One Message, Several Requests: technology choices" section to `README.md`, recording this phase's three tradeoffs — the fan-out inside the FAQ node rather than a LangGraph `Send` fan-out (which would need a channel reducer on a shared state key), one generation call per request rather than one over every request's shortlist, and a cap the constrained-output schema cannot express — per Constitution VI (missing)
- [X] T058 Note the one deliberate opt-out from the reranking boundary fake in `_reranking_keeps_what_it_is_given`'s docstring in `services/chat/tests/conftest.py`, so a fake the whole suite leans on documents its own exception where it is read, per `docs/testing-strategy.md` (partial)

---

## Phase 11: Convergence

**Purpose**: close the gap a second post-implementation assessment found

- [X] T059 Add the two segmentation rules the shipped prompt carries but `specs/010-multi-request-turns/contracts/segmentation.md` does not list — the follow-up-clause rule ("do you take Medicare? what if you do not?" is two requests, not one) and the standing "never leave out something the visitor asked for" limit — both added after the live measurement and worth 8 points on the `multi` set, per FR-004, FR-004a (partial)
