---

description: "Task list for small talk and what escalation is for (Phase 1f)"
---

# Tasks: Small Talk and What Escalation Is For (Phase 1f)

**Input**: Design documents from `/specs/009-small-talk-and-escalation/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: Per constitution principle VIII (NON-NEGOTIABLE), every test task precedes its
implementation task and MUST be observed failing first. This holds for *changed* behavior as well as
new: where an existing suite encodes the old contract — `call_staff` meaning "a billing problem",
`unknown` falling through to the FAQ path — updating it to the new contract is itself the failing
test, and it comes first. No task bundles a test with the implementation it covers.

**Organization**: Grouped by user story, ordered by **priority**, which is not the order the spec
lists them in: US1, US2 and US5 are all P1 and come first, then US3 and US4 (P2), then US6 (P3).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable — different files, no dependency on an incomplete task
- Paths are repo-relative from `/home/andrey/visit-doc`

## Running tests while you work

`services/chat/tests` is ~6 minutes and hits real Postgres and Qdrant. Per
`docs/testing-strategy.md`: iterate with scoped runs
(`uv run pytest services/chat/tests/test_graph.py -q`), run the full tier **once** at the end, and
never start a second database-backed tier alongside it. The frontend tier is fast:
`cd services/frontend && npm test`.

**No live model call belongs in any test here.** Every behavioral task drives a stubbed
classification result through `fake_classify_intent_client` in `services/chat/tests/conftest.py`
(FR-037); the classifier's own judgement is measured once, by hand, in Phase 9.

---

## Phase 1: Setup

**Purpose**: the evaluation data, which nothing depends on and which Phase 9 cannot run without.

- [X] T001 [P] Fill sets A–E in `specs/009-small-talk-and-escalation/evaluation/messages.md` to the sizes their criteria require: ≥30 no-request (SC-001), ≥20 pleasantry+request (SC-003), ≥15 not-authorized (SC-011), ≥20 stopping causes spread across all three (SC-015), plus the near-miss set (SC-016a)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the shared vocabulary — three closed sets, one cause table, one generalized node.
Every story below reads from these; none can be written first.

**⚠️ CRITICAL**: no user story work begins until this phase is complete.

### Tests (write first, observe failing)

- [X] T002 In `services/chat/tests/test_models.py`, assert `EscalationReason` and `AttentionMark` each contain the four new values from [data-model.md §2–3](./data-model.md), and that every member of both is at most 32 characters — the guard that says why this feature needs no migration
- [X] T003 In `services/chat/tests/test_models.py`, assert `CLEARABLE_MARKS` is exactly `{patient_asked_for_person, unanswered, urgent_condition, distress, booking_for_another_person, not_authorized}` and that `corpus_could_not_answer` and `assistant_failed` are absent
- [X] T004 In `services/chat/tests/test_escalation.py`, assert `_PRECEDENCE` is exactly the order in [contracts/escalation-causes.md](./contracts/escalation-causes.md) and that `_SILENCING` is the four silencing causes — not one, and not all of them
- [X] T005 In `services/chat/tests/test_escalation.py`, assert every `EscalationReason` has an entry in the cause table (mark, fixed text where one applies, silencing answer), so a cause added later without its row fails here rather than at runtime
- [X] T006 In `services/chat/tests/test_escalation.py`, add resolution cases over the new precedence: a turn recording urgent + distress marks urgent; urgent + patient-asked marks urgent; not-authorized + corpus-gap marks not-authorized; and every recorded cause still appears in `recorded`
- [X] T007 [P] In `services/frontend/tests/consoleApi.test.ts`, assert `ATTENTION_MARK_LABEL` has a non-empty label for all eight `AttentionMark` values
- [X] T008 [P] In `services/chat/tests/test_console_api.py`, assert `MessageOut` accepts each of the four new `attention_mark` values and still rejects an unknown string

### Implementation

- [X] T009 Add `URGENT_CONDITION`, `DISTRESS`, `BOOKING_FOR_ANOTHER_PERSON`, `NOT_AUTHORIZED` to `EscalationReason` and the same four to `AttentionMark` in `services/chat/src/chat/domain/models.py`, with the docstrings updated to say which silence and why — the current text asserting "only `PATIENT_ASKED_FOR_PERSON` silences" is now false and must not survive
- [X] T010 Extend `CLEARABLE_MARKS` in `services/chat/src/chat/domain/models.py` with the four new marks, and update its comment to state the rule that decides membership: a staff reply is the whole of what the mark asked for
- [X] T011 Rewrite `_PRECEDENCE`, `_SILENCING` and `_MARK_BY_REASON` in `services/chat/src/chat/agent/escalation.py` per [contracts/escalation-causes.md](./contracts/escalation-causes.md), keeping silencing a separate membership rather than an attribute of the enum
- [X] T012 Add the cause table to `services/chat/src/chat/agent/escalation.py`: one mapping from `EscalationReason` to its fixed reply text, beside `HANDOFF_MESSAGE`, which becomes one row of it. Texts per [contracts/replies.md](./contracts/replies.md) — the three obligations of each, no promise of a time, no clinical content
- [X] T013 Add the four values to the `attention_mark` literal in `MessageOut` in `services/chat/src/chat/domain/schemas.py`
- [X] T014 [P] Add the four values to the `AttentionMark` union in `services/frontend/src/lib/chatStream.ts` and the four labels to `ATTENTION_MARK_LABEL` in `services/frontend/src/lib/consoleApi.ts` ("Urgent condition", "Patient in distress", "Booking for someone else", "Not something the assistant may do")
- [X] T015 Generalize the hand-off node in `services/chat/src/chat/agent/graph.py`: replace the `handed_off: bool` state key with `handoff_reason: EscalationReason | None`, and have the node write that cause's text from T012's table instead of the single constant. `AnswerSource.HAND_OFF` widens to "a fixed notice that a person now has this" — update its docstring in `services/chat/src/chat/domain/schemas.py`
- [X] T016 Run the scoped suites and observe T002–T008 passing: `uv run pytest services/chat/tests/test_models.py services/chat/tests/test_escalation.py services/chat/tests/test_console_api.py -q` and `cd services/frontend && npm test`

**Checkpoint**: the vocabulary exists and is enforced. No routing has changed yet.

---

## Phase 3: User Story 1 — A pleasantry is answered, not escalated (P1) 🎯 MVP

**Goal**: a message that asks for nothing gets a courteous reply, no retrieval, and no call to staff.

**Independent Test**: send "Hi", "I see", "Thanks", "OMG", "Let me think a bit" into a fresh
conversation — a natural reply to each, no citations, no abstention message, empty staff queue.

### Tests (write first, observe failing)

- [X] T017 [P] [US1] In `services/chat/tests/test_classify_intent.py`, assert `IntentLabel` contains `SMALL_TALK` and that the request schema's enum offers it to the model while still excluding `CLASSIFICATION_FAILED`
- [X] T018 [US1] Create `services/chat/tests/test_small_talk.py` asserting `answer_small_talk()` makes exactly one Anthropic call, on `CLASSIFICATION_MODEL`, with history bounded by `CONTEXT_TURNS`, and builds no tool registry
- [X] T019 [US1] In `services/chat/tests/test_small_talk.py`, assert it streams its tokens and yields a trailing result carrying the reply text, and that it never records anything into the turn's `EscalationRequests`
- [X] T020 [US1] In `services/chat/tests/test_small_talk.py`, assert the system prompt states the FR-012 prohibitions (no policy, price, date, time, practitioner, appointment, callback promise, clinical content), the FR-013 permission (a greeting may offer help in general terms), and the FR-022e case (an unintelligible message is answered by inviting a rephrase) — the prompt is the contract, so it is asserted, not paraphrased
- [X] T021 [US1] In `services/chat/tests/test_graph.py`, assert a turn classified `[small_talk]` runs `["classify_intent", "small_talk", "compose_answer"]`, performs no retrieval and no scheduling call, and records no escalation
- [X] T022 [US1] In `services/chat/tests/test_turn_api.py`, assert such a turn's terminal event carries `answer_source="small_talk"`, `faq_verdict=null`, `citations=[]`, and that the patient message ends with no attention mark and the chat with no `attention_since`
- [X] T023 [US1] In `services/chat/tests/test_turn_api.py`, assert the small-talk reply is stored as an ordinary assistant message: it comes back from the chat's own history and from the console's thread for the same conversation, with no marker distinguishing it from any other assistant message (FR-015)
- [X] T024 [US1] In `services/chat/tests/test_graph.py`, assert a `[small_talk]` turn in a session whose corpus is empty still replies — an empty corpus is irrelevant to a message that asked nothing of it

- [X] T025 [US1] In `services/chat/tests/test_small_talk.py`, assert a failing model call behaves exactly as a generation failure on an existing path does — same exception out of the node, same turn ending, no new failure semantics and no silent empty reply (FR-017)

### Implementation

- [X] T026 [US1] Add `SMALL_TALK` to `IntentLabel` in `services/chat/src/chat/domain/schemas.py` and `SMALL_TALK` to `AnswerSource` in the same file
- [X] T027 [US1] Extend the classifier's system prompt in `services/chat/src/chat/agent/classify_intent.py` with the `small_talk` rule from [contracts/classification.md](./contracts/classification.md): asks for nothing that can be acted on, including an unintelligible fragment
- [X] T028 [US1] Create `services/chat/src/chat/agent/small_talk.py` with `answer_small_talk()` — one cheap-model call, bounded history, streaming, a `SmallTalkResult`, and the bounded system prompt; no tools, no retrieval, no escalation
- [X] T029 [US1] Wire the node in `services/chat/src/chat/agent/graph.py`: a `small_talk` node, an edge to `compose_answer`, `small_talk_result` in `_GraphState`, and the routing row that selects it when `small_talk` is the only label. It never merges — `_single_specialist_reply` gains its case
- [X] T030 [US1] Run `uv run pytest services/chat/tests/test_small_talk.py services/chat/tests/test_graph.py services/chat/tests/test_turn_api.py services/chat/tests/test_classify_intent.py -q` and observe T017–T024 passing

**Checkpoint**: US1 is independently demonstrable — the phase's headline defect is gone.

---

## Phase 4: User Story 2 — A pleasantry wrapped around a request is handled as the request (P1)

**Goal**: the greeting changes nothing; the request decides the turn.

**Independent Test**: send messages pairing a social opener with a real request — every one handled
by the specialist the request implies, with no bare acknowledgement.

### Tests (write first, observe failing)

- [X] T031 [US2] In `services/chat/tests/test_graph.py`, assert `small_talk` is dropped whenever another label is present: `[small_talk, faq_question]` runs only the FAQ node, `[small_talk, booking]` only the booking node, `[small_talk, faq_question, booking]` both plus the merge
- [X] T032 [US2] In `services/chat/tests/test_graph.py`, assert a dropped `small_talk` leaves no trace in the reply — no acknowledgement is prepended, and `answer_source` is the servable path's, never `merged` on account of the drop
- [X] T033 [P] [US2] In `services/chat/tests/test_classify_intent.py`, assert the prompt carries the tie-break rule: where a social and a request reading are both plausible, it is not small talk

### Implementation

- [X] T034 [US2] Implement the drop in `_select_specialists` in `services/chat/src/chat/agent/graph.py` — `small_talk` selects a node only when it is alone among the actionable labels
- [X] T035 [US2] Add the FR-007 tie-break sentence to the classifier prompt in `services/chat/src/chat/agent/classify_intent.py`
- [X] T036 [US2] Run `uv run pytest services/chat/tests/test_graph.py services/chat/tests/test_classify_intent.py -q` and observe T031–T033 passing

**Checkpoint**: US1 cannot regress the assistant — the failure mode it introduced is closed.

---

## Phase 5: User Story 5 — Three situations stop the conversation and fetch a person (P1)

**Goal**: an urgent condition, evident distress, and a third-party booking each call staff under
their own cause, silence the assistant, and answer with one fixed sentence.

**Independent Test**: send one of each into three conversations — a fixed reply, the assistant silent
afterwards, the cause named beside the message in the console, and no appointment created anywhere.

### Tests (write first, observe failing)

- [X] T037 [P] [US5] In `services/chat/tests/test_classify_intent.py`, assert `IntentLabel` contains `URGENT_CONDITION`, `DISTRESS` and `BOOKING_FOR_ANOTHER`, and that the prompt states each rule and its exclusion from [contracts/classification.md](./contracts/classification.md)
- [X] T038 [US5] In `services/chat/tests/test_graph.py`, assert each of the three suppresses every other label — `[urgent_condition, booking]` and `[distress, faq_question]` run no specialist, make no retrieval and no scheduling call, and reach `compose_answer` through the hand-off node
- [X] T039 [US5] In `services/chat/tests/test_graph.py`, assert each records its own `EscalationReason` and that the turn's reply is that cause's exact text — byte-for-byte, per [contracts/replies.md](./contracts/replies.md)
- [X] T040 [US5] In `services/chat/tests/test_graph.py`, assert precedence across mixed labels: `[urgent_condition, distress]` records urgent as the mark; `[urgent_condition, booking_for_another]` records urgent; `[distress, call_staff]` records distress
- [X] T041 [US5] In `services/chat/tests/test_turn_api.py`, assert each of the three silences the conversation: the chat ends the turn escalated with that reason, the patient message carries the matching mark, and the *next* patient message receives no reply and the `unanswered` mark
- [X] T042 [US5] In `services/chat/tests/test_turn_api.py`, assert the reply is delivered *before* the silence begins — the turn that stops the conversation still streams its sentence
- [X] T043 [P] [US5] In `services/chat/tests/test_scheduling_tools.py` (or `test_graph.py`, wherever the scheduling stub is asserted), assert a `booking_for_another` turn issues **zero** scheduling calls — the refusal precedes the service boundary
- [X] T044 [US5] In `services/chat/tests/test_handle_booking.py`, assert a booking turn whose beneficiary is not stated establishes who the appointment is for **before** any booking call — the clarifying question is asked and no appointment is written (FR-045b). This is what makes the explicit-only rule safe: ambiguity is resolved by a question the patient can answer, never by stopping the conversation
- [X] T045 [P] [US5] In `services/chat/tests/test_staff_messages.py`, assert a staff reply clears each of the three new marks, and leaves `corpus_could_not_answer` in place on the same conversation
- [X] T046 [P] [US5] In `services/frontend/tests/MessageView.test.tsx`, assert a message carrying each new mark renders its own label, distinct from "needs attention" and from each other
- [X] T047 [US5] In `services/chat/tests/test_graph.py`, assert the urgent-condition text names emergency services and contains no severity assessment, diagnosis, or medical advice (SC-019)

### Implementation

- [X] T048 [US5] Add `URGENT_CONDITION`, `DISTRESS`, `BOOKING_FOR_ANOTHER` to `IntentLabel` in `services/chat/src/chat/domain/schemas.py`
- [X] T049 [US5] Add the three rules and their exclusions to the classifier prompt in `services/chat/src/chat/agent/classify_intent.py`, including the explicit-only rule for `booking_for_another` (FR-045a)
- [X] T050 [US5] Write the three fixed texts into T012's cause table in `services/chat/src/chat/agent/escalation.py`, each satisfying its row in [contracts/replies.md](./contracts/replies.md)
- [X] T051 [US5] Add the four stopping rows to the routing table in `services/chat/src/chat/agent/graph.py` — each records its cause in `classify_intent_node` and routes to the hand-off node alone, as `call_staff` already does
- [X] T052 [US5] Extend the booking system prompt in `services/chat/src/chat/agent/handle_booking.py` so the loop establishes who an appointment is for before writing, whenever the message did not say (FR-045b). No tool changes — the existing confirmation step is where this belongs
- [X] T053 [US5] Run `uv run pytest services/chat/tests/test_graph.py services/chat/tests/test_turn_api.py services/chat/tests/test_staff_messages.py services/chat/tests/test_handle_booking.py -q` and `cd services/frontend && npm test`, observing T037–T047 passing

**Checkpoint**: the safety half of the phase is in place and demonstrable end to end.

---

## Phase 6: User Story 3 — The same word is read against the conversation (P2)

**Goal**: "OK" after arrival instructions is small talk; "OK" after a slot offer is a booking.

**Independent Test**: drive the two conversations to the same one-word reply and watch them diverge —
one acknowledgement, one appointment.

**Note**: the judgement itself is the classifier's and cannot be asserted offline. What is tested
here is that the classifier is *given* what it needs and that both outcomes are reachable; the
judgement is measured in Phase 9 against sets A9 and D of the evaluation data.

### Tests (write first, observe failing)

- [X] T054 [US3] In `services/chat/tests/test_classify_intent.py`, assert the call carries the preceding assistant turns — the bounded window, not the trailing patient message alone — so a one-word reply is classified in context
- [X] T055 [US3] In `services/chat/tests/test_classify_intent.py`, assert the prompt instructs that the same words may be social in one context and a request in another, with the confirmation case named
- [X] T056 [US3] In `services/chat/tests/test_graph.py`, assert both outcomes route correctly from an identical trailing message: `[small_talk]` reaches the small-talk node, `[booking]` reaches the booking node and completes the booking

### Implementation

- [X] T057 [US3] Add the context-sensitivity guidance to the classifier prompt in `services/chat/src/chat/agent/classify_intent.py`, naming the confirmation case explicitly
- [X] T058 [US3] Run `uv run pytest services/chat/tests/test_classify_intent.py services/chat/tests/test_graph.py -q` and observe T054–T056 passing

---

## Phase 7: User Story 4 — The staff queue means something again (P2)

**Goal**: escalation narrows to its four situations; an unauthorized request is refused deliberately,
with a person called and the assistant still free to talk.

**Independent Test**: walk one conversation through pleasantries, answerable questions, unanswerable
questions, an unauthorized request and a request for a human — only the last three appear in the
queue, each under its own cause.

### Tests (write first, observe failing)

- [X] T059 [US4] In `services/chat/tests/test_graph.py`, assert `[unknown]` alone makes **no** retrieval call, records `not_authorized`, and replies with that cause's exact text — replacing the existing assertion that `unknown` falls through to the FAQ path, which is the old contract
- [X] T060 [P] [US4] In `services/chat/tests/test_turn_api.py`, assert a `not_authorized` turn leaves the conversation **not** silenced, marks the message, sets `attention_since`, and that the patient's next question is answered normally (SC-012)
- [X] T061 [US4] In `services/chat/tests/test_graph.py`, assert `[unknown, faq_question]` runs the FAQ node, sets `notice_required`, and reaches the merge — and that no fixed text is emitted verbatim on that path
- [X] T062 [P] [US4] In `services/chat/tests/test_compose_answer.py`, assert the composer is told a notice is owed and that its prompt requires FR-022c's three obligations, forbidding a claim that the request was served or a promise of when staff will respond
- [X] T063 [P] [US4] In `services/chat/tests/test_classify_intent.py`, assert the prompt narrows `call_staff` to an explicit request for a human and points staff-handled topics at `unknown` — replacing the assertion that encodes today's "e.g. a billing problem" wording
- [X] T064 [US4] In `services/chat/tests/test_graph.py`, assert `classification_failed` still falls back to the FAQ path and reaches none of the new routes (FR-009)
- [X] T065 [P] [US4] In `services/chat/tests/test_attention_marks.py`, assert a staff reply clears a `not_authorized` mark while a `corpus_could_not_answer` mark on the same conversation survives (SC-014)
- [X] T066 [P] [US4] In `services/chat/tests/test_answer_faq.py`, assert the abstention path is unchanged — a real question the corpus cannot answer still records `corpus_could_not_answer` and still leaves the assistant free to talk (FR-024)

### Implementation

- [X] T067 [US4] Re-point `unknown` in the classifier prompt in `services/chat/src/chat/agent/classify_intent.py` to "a request the assistant is not authorized to serve", and narrow `call_staff` to an explicit request for a human
- [X] T068 [US4] Add the two `unknown` rows to the routing table in `services/chat/src/chat/agent/graph.py`: alone it routes to the hand-off node with `not_authorized`; accompanying a servable label it records the cause, sets `notice_required`, and lets the servable specialists run
- [X] T069 [US4] Add `notice_required` to `_GraphState` and thread it into `compose_answer()` in `services/chat/src/chat/agent/compose_answer.py`, extending the merge prompt with the three obligations. A turn with one servable specialist and a notice owed must take the merge path
- [X] T070 [US4] Run `uv run pytest services/chat/tests/test_graph.py services/chat/tests/test_turn_api.py services/chat/tests/test_compose_answer.py services/chat/tests/test_attention_marks.py services/chat/tests/test_answer_faq.py -q` and observe T059–T066 passing

**Checkpoint**: every cause in [contracts/escalation-causes.md](./contracts/escalation-causes.md) is
reachable, and only those causes reach the queue.

---

## Phase 8: User Story 6 — A small-talk turn is on the record (P3)

**Goal**: the record says what each turn was, and Phase 2 can compute the metric from it.

**Independent Test**: run one small-talk turn and one FAQ turn, then reconstruct from their records
alone which was which, what produced each reply, and whether either called staff.

### Tests (write first, observe failing)

- [X] T071 [US6] In `services/chat/tests/test_graph.py`, assert `intent.classified` carries `stopping_cause` and `notice_required` per [contracts/log-events.md](./contracts/log-events.md), null and false on an ordinary turn
- [X] T072 [P] [US6] In `services/chat/tests/test_node_logging.py` or `test_graph.py`, assert the hand-off node's `node.completed` carries `cause`, so four stopping causes do not produce four indistinguishable records
- [X] T073 [US6] In `services/chat/tests/test_graph.py`, assert a small-talk turn's `node.completed` carries `answer_text` and `answer_chars` and no retrieval fields, and that `turn.completed` carries `answer_source="small_talk"`
- [X] T074 [US6] In `services/chat/tests/test_graph.py`, assert the Phase 2 join is computable from one turn's lines: a turn whose only label is `small_talk` has no `escalation.raised`, and a `distress` turn has one — the deliberate exception the join excludes by cause

### Implementation

- [X] T075 [US6] Add `stopping_cause` and `notice_required` to the `intent.classified` event and `cause` to the hand-off node's span in `services/chat/src/chat/agent/graph.py`
- [X] T076 [US6] Run `uv run pytest services/chat/tests/test_graph.py services/chat/tests/test_node_logging.py -q` and observe T071–T074 passing

---

## Phase 9: Polish, Measurement & Documentation

- [X] T077 Run the full chat unit tier once: `uv run pytest services/chat/tests -q`. Investigate every failure as a regression against SC-010's carve-out — a difference not attributable to FR-001a is a defect, not an expected change
- [X] T078 [P] Run the frontend tier once: `cd services/frontend && npm test`
- [X] T079 [P] Run `make lint` and `make typecheck` — mypy is strict, and the new node's public functions need full annotations
- [X] T080 Execute `specs/009-small-talk-and-escalation/evaluation/procedure.md` against a running stack and record the dated result block in that file: model id, per-set pass counts, and every disagreement with the label the classifier actually produced
- [X] T081 [P] Reconcile `.claude/CLAUDE.md`'s escalation and agent-design bullets with what shipped — any claim there that escalation has three causes, or that `unknown` reaches the FAQ path, is now false and must be corrected in this change, not after it
- [X] T082 [P] Add the small-talk and stopping paths to the README's architecture description, with the tradeoff each one records: a constant instead of a generation, and a routing label instead of triage
- [X] T083 Walk `specs/009-small-talk-and-escalation/quickstart.md` end to end against a running stack, including section 7's regression checks, and fix or file anything that does not behave as written

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1 (Setup)**: independent of everything; T001 can be done at any point before T080
- **Phase 2 (Foundational)**: blocks every story phase — the enums, the cause table and the
  generalized hand-off node are what the stories are written against
- **Phase 3 (US1)** → **Phase 4 (US2)**: US2 constrains the routing US1 introduces, so it follows it
- **Phase 5 (US5)**: depends only on Phase 2 in *content* — it shares no requirement with US1/US2.
  It is not file-disjoint from them, though: Phases 3, 5 and 7 all edit `graph.py`'s routing table
  and the classifier prompt, so two people working simultaneously serialize on those two files. The
  tests are disjoint; the two shared implementation files are not
- **Phase 6 (US3)**: depends on Phase 3 (it needs the small-talk route to exist to diverge from)
- **Phase 7 (US4)**: depends on Phase 2 only; touches `unknown`, which no earlier phase does — with
  the same shared-file caveat as Phase 5
- **Phase 8 (US6)**: depends on Phases 3, 5 and 7 — it records what they decide
- **Phase 9**: depends on everything

### Story independence

| Story | Depends on | Independently demonstrable? |
|---|---|---|
| US1 (P1) | Phase 2 | Yes — the MVP |
| US2 (P1) | US1 | Yes, but meaningless before US1 exists |
| US5 (P1) | Phase 2 | Yes |
| US3 (P2) | US1 | Yes |
| US4 (P2) | Phase 2 | Yes |
| US6 (P3) | US1, US5, US4 | Yes, by reading logs |

### Parallel opportunities

A task carries **[P]** only when no other task in its phase edits any file it edits. Several test
tasks that are conceptually independent share a suite file and are therefore *not* marked parallel —
they are still small, and doing them in listed order costs nothing.

- **Phase 2**: T007 and T008 are parallel (a vitest file and a pytest file nobody else touches); T014
  is parallel with everything in the phase, being the only frontend change. T002–T006 share two
  suites and run in order
- **Phase 3**: no two tasks in the phase are file-disjoint except T017, which is the only one in
  `test_classify_intent.py`
- **Phase 5**: T043, T044, T045 and T046 each own a file nobody else in the phase touches and are
  parallel; the `test_graph.py` and `test_turn_api.py` tasks run in order
- **Across phases**: US5 (Phase 5) and US4 (Phase 7) add disjoint routing rows and disjoint tests, so
  two people can build them at once — but both edit `graph.py` and `classify_intent.py`, so those two
  files are the merge point, not a free-for-all

### Within each story

Tests first, observed failing, then implementation, then the scoped run. Where an existing test
encodes the old contract, updating it *is* the failing test — T059 and T063 are the two places this
applies, and they must not be bundled with T067 or T068.

---

## Implementation Strategy

**MVP**: Phase 1 + Phase 2 + Phase 3 (US1). At that point "Thanks" is answered courteously and calls
nobody, which is the defect the phase exists to fix, and everything else is additive.

**Second increment**: Phase 4 (US2) — ships with the MVP in practice, because US1 without it can
answer a real request with a pleasantry.

**Third increment**: Phase 5 (US5), the safety half. It is P1 and independent; it is sequenced third
only because US1+US2 are one thought and splitting them mid-flight costs more than it saves.

**Then**: Phases 6–8, and Phase 9 last — the manual measurement (T080) is worth running only against
the finished routing, since every earlier run measures a classifier prompt that is still changing.

---

## Phase 10: Convergence

- [X] T084 Drop the small-talk label when `unknown` accompanies it, in `_select_specialists` in `services/chat/src/chat/agent/graph.py`, so `[small_talk, unknown]` routes to the hand-off node as a solo not-authorized turn rather than to the small-talk node with a notice owed — today it streams a courteous reply *and* a composed one, sending the patient two replies and storing only the second, per FR-008 and SC-002 (contradicts). Cover the combination in `services/chat/tests/test_graph.py` first: it is the one pairing no existing test exercises
- [X] T085 Reconcile `stopping_cause` and `notice_required` with `specs/009-small-talk-and-escalation/contracts/log-events.md`, which puts them on `intent.classified` while `services/chat/src/chat/agent/graph.py` sets them on the router's `node.completed` span (partial). Either is workable for Phase 2's join, which correlates by turn id — pick one and make the other match
- [X] T086 Correct the `specialists` line in `specs/009-small-talk-and-escalation/contracts/log-events.md`, which claims the field "may now be empty for a stopping turn": `_select_specialists` returns `["hand_off"]`, as it already did for `call_staff` before this phase (partial)

---

## Phase 11: Convergence

- [X] T087 Record **every** applicable stopping cause into the turn's `EscalationRequests`, not only the winning one, in `classify_intent_node` / `_handoff_reason` in `services/chat/src/chat/agent/graph.py` — precedence still decides the mark and the silence, but a message that is both urgent and distressed currently logs `urgent_condition` alone, so the discarded cause reaches no log line. Per FR-047 ("a discarded cause is still recorded in the turn's log, as it is today") and `EscalationRequests.recorded`'s own documented invariant, which the router bypasses by collapsing before it records (partial). Assert it in `services/chat/tests/test_graph.py` first: the existing precedence tests pass either way, because they check `message_mark`, which resolves identically

---

## Phase 12: Convergence

- [X] T088 CRITICAL — rewrite the module docstring of `services/chat/src/chat/agent/escalation.py`, which still says "three callers", "Four things can decide a conversation needs a person - the classifier labelling the message `call_staff`", and "only the first two silence". There are now seven causes, the classifier raises five of them, and four silence. This is the file a reader opens to learn how escalation works, per Constitution VI ("documentation is updated in the same change that makes it stale") (partial)
- [X] T089 CRITICAL — redraw the graph diagram and correct the header in the module docstring of `services/chat/src/chat/agent/graph.py`, which calls the graph "a router, two specialists that may run concurrently, and a merge" and omits `small_talk` from the diagram; also check the "two specialists write disjoint state keys" note against the three that now do, per Constitution VI (partial)
- [X] T090 Generalize `_SYSTEM_PROMPT`'s opening in `services/chat/src/chat/agent/compose_answer.py` — it tells the composing model the message "had two parts: a question, and something about an appointment. Two specialists have already handled them", which is false on the FR-022d path, where the input is one specialist's output plus a NOT AUTHORIZED block. Unlike a docstring this reaches the patient's reply, per FR-022c1 and FR-022d (partial). Assert the generalized wording in `services/chat/tests/test_compose_answer.py` first
- [X] T091 Update `_select_specialists`'s docstring in `services/chat/src/chat/agent/graph.py`, which names only `call_staff` as taking the whole turn and suppressing every other label — four causes now do, per FR-046 (partial)

---

## Phase 13: Convergence

- [X] T092 Pin FR-003a and FR-003b's prompt clauses in `services/chat/tests/test_classify_intent.py`: that a message asking anything about the clinic, an appointment, a practitioner or the patient's care is never small talk however short, that a bare affirmative answering the assistant's own question belongs to what was asked, and that an off-topic question is small talk and calls nobody. Every other rule in that prompt is pinned this way; these two are the fix for a defect the live run caught (B11, `evaluation/procedure.md` 2026-09-08), so an unpinned prompt edit would regress exactly what was measured (missing)
- [X] T093 Assert `ESCALATE_TO_STAFF.description` in `services/chat/tests/test_escalation.py` refuses the neighbouring cases — a request outside what the assistant can do (a receipt, a letter, a prescription, a billing correction), an unsure answer, a refused booking, a failed tool — and states that calling it stops the assistant replying. The schema, `requires_patient` and `writes` are already pinned; the description is what was wrong in run 1's C16 and is what the module docstring now calls load-bearing, per FR-022b (missing)
- [X] T094 Record the `distress` boundary in `specs/009-small-talk-and-escalation/spec.md` the way FR-003a/FR-003b record the small-talk one: a brief exclamation or a mild reaction on its own ("Oh no", "ugh") is not distress, and dismay about something ordinary is not either. It currently exists only in the classifier prompt, so FR-040's "evident distress" never says what "evident" excludes — while the parallel decision was written down, which makes the two inconsistent (partial)
- [X] T095 Add `evaluation/inputs/` — the 10 committed input files, `drive.py`, `sc004.py` and its README — to the documentation tree in `specs/009-small-talk-and-escalation/plan.md`, which still lists only `messages.md` and `procedure.md` under `evaluation/` (partial)
- [X] T096 Review the `specs/**/*.py` exclusion added to ruff's `extend-exclude` and mypy's `exclude` in `pyproject.toml`. It exists so a spec's committed manual-procedure harness does not fail gates written for application code, and it is commented as such — but no spec, plan or task asked for it, so it should be kept deliberately or dropped (unrequested)
- [X] T097 Reconcile the "no triage" claim in FR-044's rationale and `research.md` §10 with the classifier prompt, which now enumerates clinical red flags (chest pain, difficulty breathing, heavy bleeding, suspected overdose, fainting, stroke signs). The claim is still true of the *reply*, which assesses nothing, and the routing decision is still a routing decision — but the documents should say that the routing rule names clinical signs rather than leaving a reader to discover it in the prompt (partial)
