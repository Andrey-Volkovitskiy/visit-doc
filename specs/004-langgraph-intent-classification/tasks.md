---

description: "Task list template for feature implementation"
---

# Tasks: Adopt LangGraph + Intent Classification (Phase 1b)

**Input**: Design documents from `/specs/004-langgraph-intent-classification/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/log-events.md, quickstart.md

**Tests**: Per Constitution Principle VIII (TDD, non-negotiable), test tasks precede their
corresponding implementation tasks. `classify_intent_node`'s full behavioral contract — FAQ-behavior
preservation, log/task-lifecycle ordering, multi-label passthrough (FR-001), catch-all handling
(FR-003), and the FR-007 failure-sentinel mapping — is entirely specified by `test_graph.py` (T007)
**before** `graph.py` (T010) is implemented, so every piece of new production logic this feature adds
has a failing test ahead of it. Phases 4 and 5 (US2/US3) then add **integration/acceptance-level**
tests that exercise the same, already-unit-tested contract end-to-end through `POST /chat` — this is
additional coverage on top of a satisfied TDD gate, not a substitute for one.

**Organization**: Tasks are grouped by user story (spec.md priorities P1/P2/P3) to enable
independent verification of each story once the shared mechanism exists.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1/US2/US3)
- File paths are exact, relative to the repo root

## Path Conventions

Single project, backend-only, entirely under `services/chat/` (plan.md Structure Decision) — no
`services/frontend` change, no new workspace member.

---

## Phase 1: Setup

**Purpose**: Add the one new dependency this feature needs.

- [X] T001 Add the `langgraph` dependency to `services/chat` (`uv add --package chat langgraph`,
      updates `services/chat/pyproject.toml` and the root `uv.lock`) — latest stable release
      (plan.md Primary Dependencies)

**Checkpoint**: `langgraph` importable from `services/chat`'s `.venv`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The shared, story-agnostic building blocks the graph wrapper (Phase 3) is built from —
the intent taxonomy, the bounded context window, and the classification call itself. None of these
alone is independently user-observable; they're what `graph.py` composes.

**⚠️ CRITICAL**: No user story can be exercised via `POST /chat` until Phase 3 (which depends on
this phase) is also done — but this phase's own units are independently unit-testable now.

- [X] T002 [P] Add `IntentLabel` (`StrEnum`, 5 members: `FAQ_QUESTION`, `BOOKING`, `CALL_STAFF`,
      `UNKNOWN`, `CLASSIFICATION_FAILED`) and `IntentClassificationResult` (`intents:
      list[IntentLabel]`, non-empty) to `services/chat/src/chat/domain/schemas.py` (data-model.md
      "IntentLabel"/"IntentClassificationResult")
- [X] T003 [P] Write `bound_to_last_n_turns()` truncation-boundary test cases in
      `services/chat/tests/test_history.py` (research.md #5/#9: exactly `n` complete
      patient-burst-then-response-burst pairs kept, a trailing unanswered burst not itself counted
      as a turn, fewer than `n` turns available returns everything) — confirm these fail (function
      doesn't exist yet)
- [X] T004 Implement `bound_to_last_n_turns(bursts: list[list[Message]], n: int = 5) ->
      list[list[Message]]` in `services/chat/src/chat/agent/history.py`, alongside
      `split_into_bursts`/`derive_reply_to_message_ids`/`to_claude_messages` (research.md #5/#9) —
      makes T003 pass
- [X] T005 [P] Write `services/chat/tests/test_classify_intent.py`, and add a shared conftest.py
      fake for the structured-output call: `classify_intent()` parses a mocked
      `AsyncAnthropic.messages.parse` structured-output response into `IntentClassificationResult`
      (single-label and multi-label cases), and raises when the mocked call errors or returns an
      unparseable/invalid result (research.md #3). Add a `fake_classify_intent_client(...)`-style
      helper to `services/chat/tests/conftest.py`, mirroring the existing
      `fake_anthropic_client`/`fake_anthropic_client_gated` pattern already used for
      `.messages.stream`, so every later test that needs to mock `.messages.parse` (T007, T012-T013)
      reuses one shared fixture instead of duplicating mock setup (Constitution Principle VII,
      testing-strategy.md's shared-fixture convention) — confirm the `test_classify_intent.py` cases
      fail (function doesn't exist yet)
- [X] T006 Implement `classify_intent(anthropic_client: AsyncAnthropic, context_messages:
      list[MessageParam]) -> IntentClassificationResult` in new
      `services/chat/src/chat/agent/classify_intent.py` — `claude-haiku-4-5-20251001` (module-level
      constant, mirroring `answer_faq.py`'s `_MODEL`), native JSON Outputs
      (`client.messages.parse(..., output_format=IntentClassificationResult)`, research.md #3),
      whose request schema's `enum` excludes `CLASSIFICATION_FAILED` so the model can structurally
      never produce it (research.md #3/#4) — makes T005 pass

**Checkpoint**: `IntentLabel`/`IntentClassificationResult` exist, `bound_to_last_n_turns` correctly
bounds history, `classify_intent()` correctly calls Haiku 4.5 and raises (never returns a failure sentinel)
on error — all independently unit-tested, with a shared mock fixture ready for reuse. Nothing yet
reachable from `POST /chat`.

---

## Phase 3: User Story 1 - FAQ answers keep working after the internal swap (Priority: P1) 🎯 MVP

**Goal**: Wrap the existing FAQ-answering pipeline in a LangGraph `StateGraph` — sequential
`classify_intent_node -> answer_faq_node -> END` — without changing anything observable about an
FAQ answer itself (spec.md SC-001), while `classify_intent_node` correctly implements every part of
its own contract (multi-label, catch-all, failure handling).

**Independent Test**: Send a known FAQ question that previously worked, confirm the answer is still
correct and grounded (or correctly abstains), and confirm a same-topic follow-up still resolves
using earlier turns.

### Tests for User Story 1 (write first, confirm failing) ⚠️

- [X] T007 [P] [US1] Write `services/chat/tests/test_graph.py` covering `graph.py`'s full contract
      before it exists:
      - `graph.run_turn(...)` yields the exact same `ChatTokenEvent`/`ChatDoneEvent` sequence
        `answer_faq()` itself would, for both a grounded and an abstained case (byte-for-byte
        streaming/citation/abstention preservation, research.md #1)
      - `classify_intent_node`'s `intent.classified` log line is emitted before any
        `answer_faq_node` event, and the whole invocation runs inside one `asyncio.Task` that
        `generation_registry` can cancel — cancelling that task mid-classification suppresses the
        `intent.classified` log line entirely (research.md #1/#2)
      - a mocked multi-label `classify_intent()` result (e.g. `["faq_question", "booking"]`) is
        passed through into `intent.classified` unchanged (FR-001)
      - a mocked catch-all result (`["unknown"]`) is logged as a normal, successful classification,
        not a failure (FR-003)
      - a mocked `classify_intent()` failure (raises) is caught, recorded as `intent.classified`
        with `["classification_failed"]`, and does **not** prevent `answer_faq_node` from running or
        the request from failing (FR-007)

      Use the T005 shared fake for mocking `AsyncAnthropic.messages.parse` — confirm all of the
      above fail (`graph.py` doesn't exist yet)
- [X] T008 [P] [US1] Extend `services/chat/tests/test_chat_api.py`'s
      `test_grounded_turn_logs_full_trace_under_one_turn_id` and
      `test_abstained_turn_logs_full_trace_under_one_turn_id`: assert `intent.classified` appears
      for the same `turn_id`, after `turn.message_received` and before `turn.completed`
      (contracts/log-events.md §3) — confirm these fail (no `intent.classified` event emitted yet)

### Implementation for User Story 1

- [X] T009 [US1] Modify `services/chat/src/chat/agent/answer_faq.py`: remove its own
      `turn.message_received` log call (the `logger.info("turn.message_received", ...)` block) —
      retrieve/gate/generate/stream logic itself is otherwise unchanged (research.md #8)
- [X] T010 [US1] Create `services/chat/src/chat/agent/graph.py`: build/compile a `StateGraph`,
      `START -> classify_intent_node -> answer_faq_node -> END`. `classify_intent_node` calls
      `classify_intent()` (T006) with `bound_to_last_n_turns()`-bounded bursts (T004) run through
      `to_claude_messages`, catches any exception and records `[IntentLabel
      .CLASSIFICATION_FAILED]` instead (FR-007), logs `intent.classified` with only `intents`
      (contracts/log-events.md §1-2), and always continues via a single unconditional edge to
      `answer_faq_node`. `answer_faq_node` wraps `answer_faq()` unchanged, forwarding its events via
      LangGraph's custom stream-writer mechanism (`get_stream_writer()`). Exposes `run_turn(...)`
      taking `bursts`/`reply_to_message_ids` — not the same signature `answer_faq()` had, since each
      node now bounds/formats `bursts` itself rather than receiving a pre-formatted history
      (research.md #1/#9) — makes T007 pass (depends on T002, T004, T006, T009)
- [X] T011 [US1] Modify `services/chat/src/chat/api/chat.py`: log `turn.message_received` inside
      `_event_stream`, immediately after `history.split_into_bursts(history_rows)`/
      `history.derive_reply_to_message_ids(bursts)` compute `bursts`/`reply_to_message_ids` (over
      `history_rows` with the just-persisted patient message folded in) and before the graph task is
      created (research.md #8/#9); change `run_pipeline`'s call from `answer_faq(...)` to
      `graph.run_turn(...)` (research.md #1/#2) — makes T008 pass (depends on T010)

**Checkpoint**: `POST /chat` runs the full graph end-to-end. Send a known FAQ question — grounded
and abstained behavior is unchanged, `intent.classified` appears in the expected log position.
SC-001 confirmed; User Story 1 independently functional; `classify_intent_node`'s full contract
(multi-label, catch-all, failure handling) was test-first per Constitution Principle VIII.

---

## Phase 4: User Story 2 - Non-FAQ and mixed-intent messages are still classified and handled gracefully (Priority: P2)

**Goal**: Prove, end-to-end through `POST /chat`, that classification actually captures every intent
present (including mixed-intent messages) and that a failed classification attempt never blocks or
corrupts the FAQ reply.

**Independent Test**: Send a booking-flavored message, an escalation-flavored message, and a
mixed FAQ+booking message; confirm each gets a coherent response and every intent present is
recorded. Separately, force the classification step to fail and confirm the response is still
coherent and the outcome is recorded as "classification failed."

**Note**: `classify_intent_node`'s multi-label/catch-all/failure-handling contract was already
proven test-first at the unit level in T007 (Phase 3), satisfying Constitution Principle VIII. This
phase's tasks are integration-level acceptance tests confirming the same contract holds through the
real `POST /chat` path, per plan.md's Testing section — additional coverage, not the TDD gate.

- [X] T012 [P] [US2] Add one parametrized `services/chat/tests/test_chat_api.py` test covering four
      cases — booking, escalation, catch-all, and a mixed FAQ+booking message. For each case, mock
      `classify_intent`'s underlying `AsyncAnthropic.messages.parse` call (via the T005 shared
      fixture) to return that case's expected label(s), `POST` that case's message, and assert:
      (a) HTTP 200 with non-empty streamed content that never claims a booking, hold, or hand-off was
      actually made (no fabrication, FR-004) — a concrete, checkable stand-in for "a coherent
      response" rather than an unverifiable adjective — and (b) `intent.classified` records exactly
      the expected label(s) (FR-001/FR-003, spec.md Acceptance Scenarios US2.1-US2.3, quickstart
      Scenario 2)
- [X] T013 [US2] Add a `services/chat/tests/test_chat_api.py` test: `classify_intent()` raises
      (mocked failure/invalid output via the T005 shared fixture) — the FAQ reply still streams
      normally (HTTP 200, non-empty content) as if nothing happened, and `intent.classified` records
      `["classification_failed"]`, never `"faq_question"` and never omitted (FR-007, spec.md
      Acceptance Scenario US2.4, quickstart Scenario 5)
- [X] T014 [US2] Add a `services/chat/tests/test_chat_api.py` regression test (mirroring the
      existing `test_burst_cancels_earlier_generation_and_yields_one_reply` timing pattern): when a
      message's turn is cancelled by a rapid follow-up, **no** `intent.classified` line is ever
      emitted for it — the end-to-end confirmation of T007's node-level cancellation-suppression
      case — while the surviving message's own `intent.classified` line reflects **both** messages'
      content via `bound_to_last_n_turns`'s context window (research.md #2, quickstart Scenario 3,
      FR-005/FR-006)
- [X] T015 [US2] Add a `services/chat/tests/test_chat_api.py` regression test: `turn.message_received`
      is emitted for **every** incoming patient message, including one whose turn is later
      cancelled, and always appears before that turn's `intent.classified`/`turn.cancelled` line
      (research.md #8's regression case, plan.md Testing)

**Checkpoint**: Booking/escalation/mixed/catch-all/failed/cancelled scenarios all verified via
`POST /chat`. User Story 2 independently functional (builds on US1, doesn't regress it).

---

## Phase 5: User Story 3 - Classified intents are reviewable before they're trusted for routing (Priority: P3)

**Goal**: Confirm a maintainer can review what was classified for a handful of messages purely from
already-captured logs, with no need to re-run the conversation.

**Independent Test**: After sending several messages with obviously different intents, confirm
each message's classified intent can be looked up/reviewed without re-running the conversation.

**Note**: Reviewability is a direct consequence of `intent.classified` already carrying `turn_id` +
`intents` (Phase 2/3, contracts/log-events.md) — this phase's task confirms that end-to-end, per
spec.md's own framing of US3 as "necessary groundwork" rather than new capability.

- [X] T016 [US3] Add a `services/chat/tests/test_chat_api.py` test: send several messages with
      different mocked intents (e.g. FAQ, booking, escalation) on one chat, capture logs across all
      of them, and assert each message's `turn_id` has exactly one retrievable `intent.classified`
      line with the expected `intents` — demonstrating lookup without re-running the conversation
      (SC-002, spec.md Acceptance Scenario US3.1, quickstart Scenario 4)

**Checkpoint**: All three user stories independently functional and verified.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, and final regression/validation/success-criteria passes.

- [X] T017 [P] Add a "## LangGraph + Intent Classification: technology choices" section to
      `README.md`, documenting the sequential-graph-shape decision, native JSON Outputs vs. forced
      tool-use, Haiku 4.5 model routing, and the log-only (no new table) persistence choice with
      their tradeoffs — matching the existing `## Structured Logging`/`## Conversational Chat
      History` sections' style and linking to `specs/004-langgraph-intent-classification/research.md`
      (Constitution Principle VI)
- [X] T018 Run `make lint`, `make typecheck`, and `make test-unit` from the repo root; fix any
      failures before considering the feature done
- [X] T019 Manually walk through `specs/004-langgraph-intent-classification/quickstart.md` Scenarios
      1-4 against a locally running `chat` service (`make run-chat`), confirming the observed log
      lines and streamed responses match each scenario's "Expected" section
- [X] T020 [P] Build a small hand-labeled sample (~15-20 representative patient messages, including
      at least one message mixing more than one intent and one short, context-dependent message per
      spec.md's Edge Cases) with an expected intent label set for each. Run each through
      `POST /chat` against the real, unmocked Haiku 4.5 model (not the test suite's fakes) and record
      whether `intent.classified`'s output matches the expected label(s). This is the concrete
      artifact spec.md's SC-003 refers to — confirms the ≥80% accuracy target
- [X] T021 [P] Measure and record the time-to-first-token added by `classify_intent_node` — e.g.
      compare a `POST /chat` request's wall-clock time to its first streamed `token` event against
      the graph as built vs. a build with `classify_intent_node` temporarily bypassed, or difference
      the timestamps of consecutive `turn.message_received`/first-`token`-emission log lines across
      several real (unmocked) runs — confirms SC-004's "does not add more than 1-2 seconds on
      average" budget

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Phase 1 (`langgraph` installed isn't actually required by
  Phase 2's own units, but Phase 1 is trivial and ordered first regardless). T002/T003/T005 are
  parallel-startable; T004 depends on T003, T006 depends on T002 and T005.
- **User Story 1 (Phase 3)**: Depends on Phase 2 completing in full (T002-T006) — `graph.py` composes
  every Foundational unit, and T007 additionally relies on T005's shared mock fixture. BLOCKS Phases
  4/5 (they exercise the same `POST /chat` mechanism T010/T011 build).
- **User Story 2 (Phase 4)**: Depends on Phase 3 completing (the graph must exist, and its contract
  must already be unit-tested via T007, to test scenarios through it). Independently testable from
  US3.
- **User Story 3 (Phase 5)**: Depends on Phase 3 completing. Independently testable from US2.
- **Polish (Phase 6)**: Depends on Phases 3-5 all being done. T020/T021 additionally need a runnable,
  real (non-mocked) deployment — same precondition as T019's quickstart walkthrough.

### Within Each Phase

- Tests are written and observed failing before their corresponding implementation task (T003→T004,
  T005→T006, T007/T008→T009/T010/T011) — Constitution Principle VIII. T007 in particular is written
  to cover the *entirety* of `classify_intent_node`'s behavior (not just the FAQ-preservation slice)
  so no part of T010's implementation is written ahead of a failing test for it.
- T009 (remove `answer_faq`'s own log call) must land together with T010/T011 (graph wraps
  `answer_faq`, `api/chat.py` logs it earlier instead) to avoid either a missing or a duplicated
  `turn.message_received` line in the final state.

### Parallel Opportunities

- Phase 2: T002, T003, T005 in parallel (different files, no inter-dependency).
- Phase 3: T007, T008 in parallel (different test files).
- Phase 4: T012 (the parametrized four-case test) can run in parallel with Phase 5's T016; T013-T015
  depend on the same fixtures as T012 but not on T012 itself, so can also proceed alongside it —
  sequencing within `test_chat_api.py` only matters for merge conflicts, not correctness.
- Phase 6: T017, T020, T021 in parallel with each other and with T018/T019 (docs vs. lint/typecheck
  vs. the two success-criteria measurement tasks are all independent activities).

---

## Parallel Example: Phase 2 (Foundational)

```bash
# Launch together:
Task: "Add IntentLabel/IntentClassificationResult to services/chat/src/chat/domain/schemas.py"
Task: "Write bound_to_last_n_turns() tests in services/chat/tests/test_history.py"
Task: "Write test_classify_intent.py + shared conftest fake for .messages.parse"
```

## Parallel Example: Phase 6 (Polish)

```bash
# Launch together:
Task: "Add README.md technology-choices section"
Task: "Build SC-003 hand-labeled accuracy sample"
Task: "Measure SC-004 added latency"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (`langgraph` installed).
2. Complete Phase 2: Foundational (schemas, `bound_to_last_n_turns`, `classify_intent`, shared mock fixture)
   — each unit-tested in isolation.
3. Complete Phase 3: User Story 1 — `test_graph.py` (T007) pins down `classify_intent_node`'s entire
   contract first; the graph wrapper then goes live behind `POST /chat`, FAQ answers are provably
   unaffected (SC-001), and `intent.classified` starts appearing in logs for every completed turn.
4. **STOP and VALIDATE**: run `test_graph.py` and the extended `test_chat_api.py` trace tests;
   confirm no regression against the pre-existing FAQ test suite.
5. This is already a demoable increment: the framework swap is proven, decoupled from whether the
   classifier's *output* is any good yet (that's US2/US3/Phase 6's job).

### Incremental Delivery

1. Setup + Foundational → shared building blocks ready, independently unit-tested.
2. Add User Story 1 → the graph runs in production, fully test-first; FAQ regression-free (MVP).
3. Add User Story 2 → multi-label/failure/cancellation scenarios confirmed end-to-end.
4. Add User Story 3 → reviewability confirmed.
5. Polish → README tradeoffs documented, full suite green, quickstart walked manually, SC-003/SC-004
   empirically measured against the real model.

---

## Notes

- [P] tasks touch different files (or independent cases in the same file with no shared mutable
  fixture state) and have no ordering dependency on each other.
- `classify_intent_node`'s full contract (multi-label, catch-all, failure handling) is tested before
  `graph.py` is implemented (T007 → T010), satisfying Constitution Principle VIII for every piece of
  new production logic this feature adds. Phases 4/5's tests are additional, integration-level
  coverage of an already-TDD'd contract, not a delayed first test for it.
- No new database table/column/migration, no new HTTP endpoint, no `services/frontend` change — see
  plan.md/data-model.md; nothing in this task list should introduce any of those.
- Commit after each task or logical group (e.g. a T00X test + the implementation task that makes it
  pass, together).

---

## Phase 7: Convergence

- [X] T022 CRITICAL: Mock `classify_intent()`'s underlying `AsyncAnthropic` call in the three
      pre-existing `POST /chat` tests in `services/chat/tests/test_chat_api.py` that currently mock
      only `embed_texts` and never touch the Anthropic client —
      `test_abstention_on_unrelated_question`, `test_followup_still_abstains_when_neither_message_is_grounded`,
      and `test_get_chat_history_preserves_abstention`. Since `classify_intent_node` now runs on
      every turn (this feature), these tests make live, non-deterministic calls to the real
      Anthropic API for classification instead of a fake — verified: `test_get_chat_history_preserves_abstention`
      failed non-deterministically on a live run, and the other two take seconds (real API latency)
      instead of milliseconds. Use the existing `fake_anthropic_client(...)`/`fake_classify_intent_client(...)`
      pattern every other `POST /chat` test already follows, so the abstention path being exercised
      no longer implies an unmocked network call. Per Constitution "Technology Foundations" (test
      suite MUST pass) and `docs/testing-strategy.md`'s mocking discipline ("only the paid
      third-party APIs ... are faked") (contradicts)
