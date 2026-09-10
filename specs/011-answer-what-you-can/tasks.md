---

description: "Task list for Phase 1h — Answer What You Can"
---

# Tasks: Answer What You Can (Phase 1h)

**Input**: Design documents from `/specs/011-answer-what-you-can/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: Per the constitution's Test-Driven Development principle (VIII, non-negotiable), every
test task below precedes its implementation and MUST be observed failing first. Everything except
the composer's three constraints is testable offline against the existing stubbed-classifier and
stubbed-retrieval seams (FR-070).

**On the composer's constraints (T034, T035).** A prompt clause's *effect* is a property of a
model's output and cannot be asserted offline. Each is therefore preceded by a test that the clause
is **present** in the system prompt, and its effect is measured once by the Phase 8 procedure
against committed data (FR-071). That is the same treatment specs 009 and 010 gave their classifier
prompts, and no other task takes it.

**Organization**: grouped by user story. Note honestly, as 1g's task list did: this phase reshapes
one value that every reply path touches, so **Phase 2 is heavy** — it moves the record onto the
request everywhere *without changing behaviour*, and US1 then flips the behaviour on top of it. That
split is deliberate: it means the migration, the wire change and the frontend re-point can be
reviewed and reverted separately from partial serving.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable — different file, no dependency on an unfinished task
- **[Story]**: US1–US5 from [spec.md](./spec.md)
- Paths are repository-relative and exact

## Path Conventions

Monorepo. Backend source under `services/chat/src/chat/`, unit tests under `services/chat/tests/`,
the migration under `services/chat/alembic/versions/`. Frontend source under
`services/frontend/src/`, its tests colocated. Unlike 1g, this phase touches both services and ships
a migration.

---

## Phase 1: Setup

**Purpose**: establish a baseline, and confirm the precondition the whole design rests on

- [ ] T001 Run `make lint`, `make typecheck` and `make test-unit` from the repo root and record the passing test count as the pre-change baseline
- [ ] T002 [P] Verify FR-072: no session remains in any store — `docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_chat -c "select count(*) from sessions"`, the same for `visitdoc_scheduler`'s `patients`, and `curl -s localhost:6333/collections/faq_chunks` showing 0 points. If any is non-zero, run `DELETE /admin/sessions` with the admin secret before going further — the migration drops columns with no backfill (FR-044, FR-044a)
- [ ] T003 [P] Confirm the plan's assumptions still hold: `FaqResult`/`FaqSegmentAnswer`/`summarize_verdict`/`_build_prompt` in `services/chat/src/chat/agent/compose_answer.py`, `_expected_parts`/`_actual_parts`/`compose_answer_node` in `services/chat/src/chat/agent/graph.py`, the abstention record in `services/chat/src/chat/agent/answer_faq.py`, and the reply insert in `services/chat/src/chat/repositories/chat_repository.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the verdict, the answer and the citations move onto the request — in storage, on the
wire, in the console — **with the turn's behaviour unchanged**. The FAQ half still abstains as a
whole at the end of this phase; only the record's shape has changed.

**⚠️ CRITICAL**: every user story reads this shape. Nothing below can begin until it is complete.

### Tests (write first, confirm failing) ⚠️

- [ ] T004 Write failing tests for `RequestOutcome` and its projection from `FaqSegmentAnswer` in `services/chat/tests/test_compose_answer.py`: `answer` is None exactly when the verdict is an abstention, `citations` empty exactly then, positions unique and ascending, `question` carrying the classifier's **restatement** — the text the pipeline retrieved for, not the patient's own wording (FR-005) — and `scored_chunks` never reaching the wire type, per [data-model.md §1](./data-model.md)
- [ ] T005 [P] Write failing tests for the column swap in `services/chat/tests/test_models.py` and `services/chat/tests/test_migrations.py`: `messages.request_outcomes` exists after `upgrade head`, `faq_verdict` and `citations` do not, and `test_migrations.py` stops importing `FaqVerdict`
- [ ] T006 [P] Write failing tests for the stored shape in `services/chat/tests/test_chat_repository.py`: an FAQ turn stores an ordered `request_outcomes` list; a booking-only turn, a hand-off and a small-talk turn store **null**, never `[]` (FR-004, contract R2); the JSONB round-trips back into `RequestOutcome`s including `answer: null` (R6)
- [ ] T007 [P] Write failing tests in `services/chat/tests/test_turn_api.py`: the terminal event carries `request_outcomes` and carries **no** `faq_verdict` and no turn-level `citations`, for all three emitting paths — streamed FAQ, collapse, merged (R5)
- [ ] T008 [P] Write failing tests in `services/frontend/tests/MessageView.test.tsx`: the staff pane renders one block per outcome with that outcome's citations, the degraded marker appears on a `answered_unreranked` **block** and not on the message, and the patient pane renders no citation, no verdict and no question label for the same payload (E7, FR-042, FR-043)
- [ ] T008a [P] Write failing tests for the two components Phase 2 also re-points, in `services/frontend/tests/StaffThread.test.tsx` and `services/frontend/tests/ChatWindow.test.tsx`: the staff thread passes a message's outcomes through to each rendered block, and an in-progress patient message carries no outcomes until its terminal event arrives. Written before T018–T020 rather than repaired after them — the constitution's TDD order (VIII) governs a changed contract, not only a new one

### Implementation

- [ ] T009 Add `RequestOutcome` beside `Citation` in `services/chat/src/chat/domain/schemas.py`, and re-shape `ChatDoneEvent` and `MessageOut` — remove `faq_verdict` and `citations`, add `request_outcomes` — per [contracts/record.md §1, §3, §4](./contracts/record.md)
- [ ] T010 Swap the columns on `Message` in `services/chat/src/chat/domain/models.py`: drop `faq_verdict` and `citations`, add `request_outcomes` (JSONB, nullable), with the docstring stating that NULL means no FAQ half ran and `[]` is never written
- [ ] T011 Generate the migration in `services/chat/alembic/versions/` — drop two columns, add one, downgrade reversing the schema and restoring no data — per [data-model.md §5](./data-model.md); run `make migrate`, then confirm `services/chat/tests/test_migrations.py` passes against a freshly upgraded database
- [ ] T012 Carry one field instead of two through the reply insert and every read in `services/chat/src/chat/repositories/chat_repository.py`
- [ ] T013 Store the outcomes off the done event in `services/chat/src/chat/api/turn.py`, replacing the `faq_verdict`/`citations` arguments
- [ ] T014 Re-shape `FaqResult` in `services/chat/src/chat/agent/compose_answer.py` per [data-model.md §3](./data-model.md): remove `verdict` and `citations`, **delete `summarize_verdict`**, add the `request_outcomes` projection from `segment_answers`, and keep `part_count` at 1-when-any-abstained for now
- [ ] T015 Move the citation dedupe inside the per-request assembly (`deduplicate_chunks` unchanged, applied per request) in `services/chat/src/chat/agent/compose_answer.py` (FR-003, research #11)
- [ ] T016 Record the corpus-gap escalation on **any** abstained request rather than on the half's summary verdict, in `services/chat/src/chat/agent/answer_faq.py` — behaviour-identical while the half still collapses (research #8)
- [ ] T017 Carry `request_outcomes` on every `ChatDoneEvent` the graph emits — the streamed FAQ path in `answer_faq.py`, the collapse path and the merged path in `services/chat/src/chat/agent/graph.py` and `compose_answer.py`
- [ ] T018 [P] Re-point the frontend wire types in `services/frontend/src/lib/chatStream.ts`: add `RequestOutcome`, remove `faq_verdict`/`citations` from `ChatDoneEvent` and the message shape
- [ ] T019 [P] Render one block per outcome in `services/frontend/src/components/MessageView.tsx` — question, citations, per-block degraded marker — and remove the message-level `data-faq-verdict` attribute and marker
- [ ] T020 [P] Pass outcomes instead of verdict + citations in `services/frontend/src/components/StaffThread.tsx`, and give the optimistic message `request_outcomes: null` in `services/frontend/src/components/ChatWindow.tsx`
- [ ] T021 Run T004–T008a to green, then `make test-unit` and `make test-frontend`; confirm the only failures left are tests asserting the old two-field shape, and re-point those in the same commit (FR-050b)

**Checkpoint**: the record is per request everywhere; a turn still abstains as a whole, and every
reply *text* is byte-identical to 1g's — the payload carrying it is not, which is the whole of what
this phase changed

---

## Phase 3: User Story 1 - The turn answers what it can and names what it cannot (Priority: P1) 🎯 MVP

**Goal**: an answerable request is delivered with its citations beside a named gap for the ones that
could not be answered

**Independent Test**: with a corpus that answers A and not B, ask both in one message and confirm
the reply contains A's answer, A's citations, and a statement of B's gap — where today it contains
only the abstention

### Tests for User Story 1 (write first, confirm failing) ⚠️

- [ ] T022 [P] [US1] Write failing tests in `services/chat/tests/test_compose_answer.py`: `from_segments` keeps every answered request's answer and citations when a sibling abstained; `part_count` is `answered + (1 if any abstained)`; `answer_text` is non-None only when the half contributes exactly one part — per [data-model.md §3](./data-model.md)
- [ ] T023 [P] [US1] Write failing tests in `services/chat/tests/test_compose_answer.py` for the prompt: one gap block however many requests fell into it, listing each question in position order, and the "name it in your own words, do not quote" instruction present (contract C4, C5, [composition.md §3](./contracts/composition.md))
- [ ] T024 [P] [US1] Write failing tests in `services/chat/tests/test_graph.py`: 2 requests with 1 answered → 2 parts, a composing call, and the answered text in the reply (C1); 2 requests both abstained → 1 part, **no** composing call, `_ABSTENTION_MESSAGE` byte for byte (C2); 1 request answered → streams, no composing call (C3)
- [ ] T025 [P] [US1] Write a failing property test in `services/chat/tests/test_graph.py` that `_actual_parts` never exceeds `_expected_parts` for every (k FAQ requests, m abstained) with k ≤ 3 (C6, research #4)
- [ ] T025a [P] [US1] Write failing **call-counting** tests in `services/chat/tests/test_answer_faq.py` and `services/chat/tests/test_graph.py`: a turn carrying *k* FAQ requests of which *m* were answered issues exactly *m* generation calls and at most one composing call, for every *k* from 1 to 3, and an abstaining request costs no generation call (FR-060, SC-008) — counted, never timed

### Implementation for User Story 1

- [ ] T026 [US1] Stop the whole-half collapse in `FaqResult.from_segments` in `services/chat/src/chat/agent/compose_answer.py`: keep every answered request's answer and citations, leave abstained requests carrying no answer, and drop the abstention-message substitution except where the half's only part is the gap
- [ ] T027 [US1] Redefine `part_count` as `answered + (1 if any abstained else 0)` in `services/chat/src/chat/agent/compose_answer.py`, and update `_actual_parts`'s docstring in `services/chat/src/chat/agent/graph.py` to match ([composition.md §1](./contracts/composition.md))
- [ ] T028 [US1] Replace the single "no confident answer" block in `_build_prompt` with the gap block listing every unanswered question, in `services/chat/src/chat/agent/compose_answer.py` ([composition.md §3](./contracts/composition.md))
- [ ] T029 [US1] Keep the all-abstained turn on the existing collapse path — constant message, no composing call — in `services/chat/src/chat/agent/graph.py` (FR-013, research #5)
- [ ] T030 [US1] Run T022–T025a to green, then `make test-unit`

**Checkpoint**: a turn serves what it can; [quickstart.md](./quickstart.md) scenarios 1, 6 and 7 pass

---

## Phase 4: User Story 2 - The answered half never covers for the gap (Priority: P1)

**Goal**: the reply may not soften an abstention, extend an answer to cover one, or quote the
classifier's restatement at the patient

**Independent Test**: run fixed answered/abstained pairs through the composing step with stubbed
halves and check each reply against the three prohibitions

### Tests for User Story 2 (write first, confirm failing) ⚠️

- [ ] T031 [P] [US2] Write a failing test in `services/chat/tests/test_compose_answer.py` asserting all three constraints are present in the composing system prompt, **and** that every pre-existing constraint survives — the booking outcome wording rules, the not-authorized notice's three requirements, and the claim-preservation rule (C8, FR-025)
- [ ] T032 [P] [US2] Write failing tests in `services/chat/tests/test_compose_answer.py`: the composer receives the abstained questions **labelled as unanswered**, never as answer blocks with empty bodies, and receives no gap block at all when nothing abstained
- [ ] T033 [P] [US2] Write a failing test in `services/chat/tests/test_turn_api.py`: a composing failure on a turn whose two requests were both answered stores no reply, delivers nothing partial, and raises one failure escalation (C7, FR-026)

### Implementation for User Story 2

- [ ] T034 [US2] Add the three constraints to the composing system prompt in `services/chat/src/chat/agent/compose_answer.py` — never soften a gap, never extend an answer to cover one, name each gap in your own words ([composition.md §4](./contracts/composition.md))
- [ ] T035 [US2] Restate the "own words, do not quote" rule inside the gap block itself in `_build_prompt`, since the input slice and the instruction fail independently (research #6)
- [ ] T036 [US2] Confirm the composing-failure path is unchanged in `services/chat/src/chat/agent/compose_answer.py` and `graph.py` — `TurnPipelineError` propagates, no partial delivery, no fallback to unmerged parts (FR-026)
- [ ] T037 [US2] Run T031–T033 to green, then `make test-unit`

**Checkpoint**: the constraints are in the prompt and their inputs are right;
[quickstart.md](./quickstart.md) scenarios 2–4 exercise them by hand, and their *effect* over a set
is measured in Phase 8

---

## Phase 5: User Story 3 - Staff receive the question that failed (Priority: P2)

**Goal**: a staff member sees each unserved request verbatim, from the console, without reading a
log

**Independent Test**: drive a turn with one answerable and two unanswerable questions, then read the
staff console and confirm both unanswered requests appear verbatim in message order

### Tests for User Story 3 (write first, confirm failing) ⚠️

- [ ] T038 [P] [US3] Write failing tests in `services/chat/tests/test_escalation.py`: one answered + one abstained raises exactly **one** `corpus_could_not_answer` escalation and does not silence the conversation (E1); three abstained raise one (E2); a fully answered turn raises none, including when one request was `answered_unreranked` (E3, FR-032)
- [ ] T039 [P] [US3] Write a failing test for the derived unserved list in `services/chat/tests/test_escalation.py`: the abstained outcomes' questions, in position order, empty for a fully answered turn (E4, [escalation-and-console.md §2](./contracts/escalation-and-console.md))
- [ ] T040 [P] [US3] Write failing tests in `services/frontend/tests/MessageView.test.tsx`: an abstained outcome renders its question verbatim plus a line saying it was not answered and went to staff (E5); and in `services/chat/tests/test_console_api.py`: a turn that stored no reply shows its mark and lists no unanswered request (E6, FR-034a)

### Implementation for User Story 3

- [ ] T041 [US3] Render the abstained outcome's block in `services/frontend/src/components/MessageView.tsx` — the question verbatim and the "not answered, forwarded to staff" line — leaving the answered blocks as Phase 2 built them
- [ ] T042 [US3] Confirm no unserved-request text is written anywhere: nothing added to `EscalationRequests` in `services/chat/src/chat/agent/escalation.py`, no new column, no second copy (FR-031a)
- [ ] T043 [US3] Run T038–T040 to green, then `make test-unit` and `make test-frontend`

**Checkpoint**: staff can act on the question rather than the message;
[quickstart.md](./quickstart.md) scenario 5 passes

---

## Phase 6: User Story 4 - The record moves onto the request (Priority: P2)

**Goal**: the log answers the same way the stored record does — per request, with no turn-level
verdict anywhere

**Independent Test**: complete one multi-request turn and reconstruct each request's question,
answer, verdict and citations from the stored message alone; then answer "which gate stopped which
request" from the log alone

### Tests for User Story 4 (write first, confirm failing) ⚠️

- [ ] T044 [P] [US4] Write failing tests in `services/chat/tests/test_compose_answer.py` for `turn.completed`: no `faq_verdict`, no turn-level `citations`, no `answer_source` (L1); `outcome` is the turn's **shape** and never a verdict string, including for a single-request FAQ turn that abstained (L2, FR-050a, research #9)
- [ ] T045 [P] [US4] Write failing tests in `services/chat/tests/test_compose_answer.py`: a partially-served turn logs `request_outcomes` with two different verdicts and **no** `abstention_message` (L3); an all-abstained turn logs `abstention_message` equal to the constant the patient saw (L4, FR-052); a booking-only turn logs no `request_outcomes` key (L5)
- [ ] T046 [P] [US4] Write a failing test in `services/chat/tests/test_compose_answer.py`: each logged outcome's citations carry both scores, with `rerank_score` absent — not zero — on a degraded request (L6)
- [ ] T047 [P] [US4] Write failing end-to-end record tests in `services/chat/tests/test_turn_api.py`: a two-request turn with one answered stores two outcomes in position order, the abstained one with `answer: null` and no citations (R1); a single-request turn stores one outcome whose `answer` is the text the patient was shown (R3); one chunk answering two requests appears under both (R4)

### Implementation for User Story 4

- [ ] T048 [US4] Replace `_segment_fields`' `segment_verdicts` with `request_outcomes` (`{position, verdict, citations}` with both scores) and drop the turn-level `faq_verdict` and `citations` from both completion paths in `services/chat/src/chat/agent/compose_answer.py`
- [ ] T049 [US4] Make `_single_specialist_outcome` return the turn's shape rather than a verdict value, and remove `answer_source` from the completion fields, in `services/chat/src/chat/agent/compose_answer.py` ([log-events.md §2](./contracts/log-events.md))
- [ ] T050 [US4] Narrow `abstention_message` to the all-abstained turn in `services/chat/src/chat/agent/compose_answer.py` (FR-052)
- [ ] T051 [US4] Run T044–T047 to green, then `make test-unit`

**Checkpoint**: the record and the log agree; nothing anywhere carries a turn-level verdict

---

## Phase 7: User Story 5 - Everything that already worked still works (Priority: P3)

**Goal**: single-request, booking, small-talk, hand-off and all-abstained turns are indistinguishable
from 1g's

**Independent Test**: replay 1g's single-request evaluation inputs and diff every reply, escalation
and mark against what they produced before

### Tests for User Story 5 (write first, confirm failing) ⚠️

- [ ] T052 [P] [US5] Write failing tests in `services/chat/tests/test_graph.py`: a single-request FAQ turn streams, records one outcome, and makes no composing call (FR-061); a booking-only turn, a hand-off and a small-talk turn each behave exactly as today and store `request_outcomes = null`
- [ ] T053 [P] [US5] Write failing tests in `services/chat/tests/test_graph.py`: a retrieval or embedding failure on any request still fails the whole turn, is not recorded as an abstention, and delivers nothing partial (1g FR-037, unchanged); and confirm 1e's six per-request retrieval and gate events still carry the fields they carry today (FR-051) — this phase changes what is done with an outcome, not how it is logged
- [ ] T054 [P] [US5] Write failing tests in `services/chat/tests/test_attention_marks.py` and `test_small_talk.py` confirming the overriding intents — a request for a human, an urgent condition, distress, a booking for another person — produce the same reply, cause, mark and silence as today

### Implementation for User Story 5

- [ ] T055 [US5] Run T052–T054 to green; fix only what they catch, in `services/chat/src/chat/agent/graph.py` — this phase is expected to require no production change beyond what Phases 2–6 made
- [ ] T056 [US5] Run `make test-unit` and confirm the pass count exceeds the T001 baseline, then diff a single-request turn's log line against the baseline to confirm nothing new appears on it

**Checkpoint**: all five stories hold; [quickstart.md](./quickstart.md) scenarios 8 and 9 pass

---

## Phase 8: Verification Data & Manual Measurement (FR-071)

**Purpose**: committed data plus a written procedure for the one contract a stub cannot verify —
data, not a runner, and in no gate

- [ ] T057 [P] Write the labelled pairs as prose in `specs/011-answer-what-you-can/evaluation/messages.md`: at least 15 two-question messages where exactly one half is answerable (SC-001, SC-002), at least 10 answer+gap pairs whose subjects are adjacent (SC-003), and the single-request set replayed for SC-011
- [ ] T058 [P] Commit the same messages as JSON under `specs/011-answer-what-you-can/evaluation/inputs/`, with the expected answerable half labelled
- [ ] T059 Write the driver in `specs/011-answer-what-you-can/evaluation/inputs/drive.py` (excluded from ruff and mypy by the existing `specs/**/*.py` rule — do not put application code here)
- [ ] T060 Write `specs/011-answer-what-you-can/evaluation/procedure.md`: how the measurement is run, what each set measures, how a softened gap / an extended answer / a quoted restatement is judged, and a results section to record every run
- [ ] T061 Run the measurement live against a running stack, record the result against C9's four checks, and — if SC-002 (100% state the gap), SC-002a (0 quoted restatements) or SC-003 (0 cross-subject claims) misses — adjust the composing prompt in `services/chat/src/chat/agent/compose_answer.py`, re-run, and record both runs
- [ ] T061a Write the **record-based check** as a section of its own in `specs/011-answer-what-you-can/evaluation/procedure.md`: how to read a stored turn's `request_outcomes` and its reply back out of the database, and how to judge from those two alone whether every claim in a request's stored answer survived into the reply and whether the reply says anything about an abstained request's subject (SC-004a). This is what makes the composer's constraint observable on traffic nobody was watching — the whole of plan.md's principle-V argument, since a constraint witnessed only by a test suite stops holding the day the model changes
- [ ] T061b Run the record-based check over the turns T061 produced, record the result in `specs/011-answer-what-you-can/evaluation/procedure.md`, and state plainly whether SC-004a held — a run that finds a softened gap is a finding about the prompt, not about the check

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T062 [P] Annotate Phase 1h's bullets in `docs/ROADMAP.md` with what shipped and where, following the "*(Shipped in `specs/...`)*" pattern 1e and 1g already use
- [ ] T063 [P] Replace the 1g entry's "one verdict per turn" language in "Key design decisions to preserve" in `.claude/CLAUDE.md` with this phase's per-request record, and note that `faq_verdict`/`citations` no longer exist on a message
- [ ] T064 [P] Add a pointer from `specs/010-multi-request-turns/contracts/log-events.md` to `specs/011-answer-what-you-can/contracts/log-events.md`, so the published field contract names its successor (FR-053)
- [ ] T065 [P] Record in `specs/011-answer-what-you-can/data-model.md` the actual migration revision id once generated, so the document names the revision rather than describing one
- [ ] T066 Run `make lint`, `make typecheck`, `make test-unit` and `make test-frontend`; grep `services/chat/src` and `services/frontend/src` for `faq_verdict` and message-level `citations` and confirm zero hits (R7)
- [ ] T067 Walk [quickstart.md](./quickstart.md) end to end against `make services-up`, then `make services-down`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies. T002 is a **gate**, not a formality: the migration drops columns with no backfill
- **Foundational (Phase 2)**: depends on Setup — **blocks every user story**, since all five read the record's shape
- **US1 (Phase 3)**: depends on Phase 2. Carries partial serving, the gap block and the part count
- **US2 (Phase 4)**: depends on US1 — there is no gap-beside-an-answer to constrain until T026 exists
- **US3 (Phase 5)**: depends on Phase 2 for the console blocks and on US1 for a turn that has both kinds of outcome; T038's escalation assertions depend on T016
- **US4 (Phase 6)**: depends on Phase 2; T045's partial-turn assertions depend on US1
- **US5 (Phase 7)**: depends on Phases 2–6 — it is the regression sweep over all of them
- **Verification (Phase 8)**: depends on US1 and US2 being complete; it measures the real composing call
- **Polish (Phase 9)**: depends on everything

### Honest note on story independence

US1 cannot ship without Phase 2, and US2 constrains behaviour US1 introduces. What each story *is*
independently is **testable**: each phase's tests fail before its implementation and pass after it,
without needing the next phase's code. Phase 2 is the one increment that is independently
*shippable* on its own — it changes the record's shape while every reply stays byte-identical, which
is exactly what makes it reviewable apart from the behaviour change.

### Within each user story

- Tests MUST be written and observed FAILING before implementation (TDD, non-negotiable)
- Schema and storage before the code that reads them
- Backend before the frontend that renders it
- Story complete before moving to the next priority

### Parallel Opportunities

- T002 and T003 (Setup)
- T004–T008a (Phase 2 tests) — five different files, two of them frontend
- T018–T020 (frontend) run beside T009–T017 (backend) once T009 fixes the wire shape
- T022–T025a, T031–T033, T038–T040, T044–T047, T052–T054 — each story's tests
- T057–T058 (evaluation data) and T062–T065 (documentation)

---

## Parallel Example: Phase 2 tests

```bash
Task: "T004 RequestOutcome invariants in services/chat/tests/test_compose_answer.py"
Task: "T005 column swap in services/chat/tests/test_models.py + test_migrations.py"
Task: "T006 stored shape in services/chat/tests/test_chat_repository.py"
Task: "T007 terminal event shape in services/chat/tests/test_turn_api.py"
Task: "T008 per-outcome rendering in services/frontend/tests/MessageView.test.tsx"
Task: "T008a re-pointed assertions in services/frontend/tests/StaffThread.test.tsx + ChatWindow.test.tsx"
```

---

## Implementation Strategy

### MVP scope

**Phase 1 + Phase 2 + Phase 3 (US1)**, and then stop and look: at that point a turn serves what it
can, the record is per request, and every other path is unchanged — but the composer is not yet
constrained. **Do not demonstrate the MVP to anyone as finished**, because the one failure it can
produce is a softened abstention, which is the failure this project cares most about. Phase 4 (US2)
is what makes the MVP safe, and the two are best delivered together.

### Incremental delivery

1. Phases 1–2 — the record moves, behaviour unchanged. Reviewable and revertible on its own.
2. Phase 3 — partial serving. The headline behaviour.
3. Phase 4 — the composer's constraints. Ship with Phase 3.
4. Phases 5–6 — staff visibility and the log.
5. Phase 7 — the regression sweep.
6. Phases 8–9 — measurement and documentation.

### Suggested commits

One per phase, at each checkpoint. Phase 2's commit is the one that carries the migration; keep it
alone so a revert is a single `alembic downgrade` plus one commit.
