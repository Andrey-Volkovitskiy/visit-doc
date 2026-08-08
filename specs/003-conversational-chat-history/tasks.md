---

description: "Task list for Conversational Chat History (ROADMAP Phase 1a)"
---

# Tasks: Conversational Chat History

**Input**: Design documents from `/specs/003-conversational-chat-history/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/, quickstart.md

**Tests**: Per the project constitution's Test-Driven Development principle (NON-NEGOTIABLE), test
tasks are mandatory and MUST precede their corresponding implementation tasks: contract → test cases
→ tests (observed failing) → implementation → tests run (observed passing).

**Organization**: Tasks are grouped by user story (spec.md priorities P1/P2/P3) to enable independent
implementation and testing of each story. `POST`, `GET`, and `DELETE /chat` share one router file
(`api/chat.py`, per plan.md's Project Structure) since the `/conversation`→`/chat` path merge put
all three methods on the same resource — tasks against that file are split by HTTP method/story, not
by file, and run sequentially within it even when unmarked `[P]`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- File paths are relative to the repository root

---

## Phase 1: Setup

**Purpose**: Confirm the environment needs no changes before touching any code

- [X] T001 Confirm no new dependencies are required: `chat` already depends on FastAPI, SQLAlchemy
      2.0 async, Alembic, and `python-ulid` (used today for `core/correlation.py`'s `turn_id`); the
      cancel-and-restart registry needs only stdlib `asyncio`. Verify `services/chat/pyproject.toml`
      and the root `uv.lock` already cover all of this — no `uv add` needed (plan.md Primary
      Dependencies).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The `Session`/`Chat`/`Message` persistence layer every user story depends on —
`POST /chat` (US1) needs it to store and read context, `GET /chat` (US2) reads the same rows,
`DELETE /chat` (US3) deletes the same `Chat`/`Message` rows.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

### Tests for Foundational (write first, confirm failing) ⚠️

- [X] T002 [P] Add failing tests for the `Session`, `Chat`, `Message` SQLAlchemy models (required
      fields, FK relationships, `ON DELETE CASCADE` on `chats.session_id` and
      `messages.chat_id`, `Message.sender` as an open-set enum) in `services/chat/tests/test_models.py`
      (data-model.md)
- [X] T003 [P] Add failing tests for `chat_repository.py`'s `create_session`, `get_session`, and
      `get_or_create_chat_for_session` in `services/chat/tests/test_chat_repository.py` — include a
      test asserting `Session.id` values from consecutive calls are **not** sequential/monotonic
      (FR-017's non-guessability requirement, research.md §1)

### Implementation for Foundational

- [X] T004 [P] Add `Session`, `Chat`, `Message` SQLAlchemy models in
      `services/chat/src/chat/domain/models.py` per data-model.md (`Message.sender`:
      `patient`/`assistant` open enum; `id`/`created_at`/`session_id`/`chat_id`/`content`/
      `grounded`/`citations` per the field tables)
- [X] T005 Add an Alembic migration creating `sessions`, `chats`, `messages` tables (generated from
      T004's models) with `ON DELETE CASCADE` on `chats.session_id` and `messages.chat_id`, and **no**
      uniqueness constraint on `chats.session_id` (data-model.md, research.md §1/§7) in
      `services/chat/alembic/versions/<new>_add_sessions_chats_and_messages.py` — depends on T004
- [X] T006 [P] Add cookie name + read/mint helpers in
      `services/chat/src/chat/api/session_cookie.py` (`visitdoc_session_id`; `HttpOnly=True`,
      `SameSite=Lax`, `Secure=False`, `Max-Age`≈400 days, minted only on new sessions — research.md §2)
- [X] T007 Implement `create_session`, `get_session`, `get_or_create_chat_for_session`,
      `create_message`, `list_messages`, `delete_chat` in
      `services/chat/src/chat/repositories/chat_repository.py` — `create_session` MUST generate
      `Session.id` via an explicit `ulid.ULIDGenerator(policy=ulid.PureRandomPolicy())`, never bare
      `ULID()` (verified against the installed library: bare `ULID()` is monotonic by default —
      same-millisecond calls increment the previous randomness by 1 rather than resourcing it,
      which fails FR-017; research.md §1); `create_message`/`list_messages` are append-only, no
      "complete"/pending step
      (research.md §3); `create_message` takes `id` as an explicit caller-supplied parameter (never
      generates one itself) so the same function serves both senders uniformly — the caller passes
      the request's `turn_id` for a patient message and a freshly minted ULID for an assistant
      message (data-model.md, research.md §4); `delete_chat` is a single `DELETE FROM chats WHERE id
      = :id`, letting the FK cascade remove its `messages` (research.md §7) — depends on T004
- [X] T008 Run T002–T003 and confirm they now pass — depends on T005, T006, T007

**Checkpoint**: Session/Chat/Message persistence ready — user story implementation can now begin

---

## Phase 3: User Story 1 - Ask a follow-up that relies on earlier context (Priority: P1) 🎯 MVP

**Goal**: `POST /chat` becomes multi-turn: prior messages inform generation and retrieval, a burst of
unanswered messages is merged into one query/reply, and a message superseded mid-generation is
cleanly cancelled rather than left half-answered.

**Independent Test**: Send two messages in the same chat where the second depends on information
only present in the first; confirm the second reply correctly uses it. Verifiable via the chat API
directly (spec.md US1 Independent Test).

### Tests for User Story 1 (write first, confirm failing) ⚠️

- [X] T009 [P] [US1] Add failing tests for `build_history_messages()`'s same-sender merge pass —
      arbitrary-length runs of consecutive patient or assistant rows, not just pairs — in
      `services/chat/tests/test_history.py` (research.md §5)
- [X] T010 [P] [US1] Add failing tests for the generation registry's cancel-and-restart behavior —
      registering a new task cancels and replaces any existing one for that `chat_id`; a task that's
      no longer current doesn't insert on completion; mock task timing rather than relying on
      wall-clock delay — in `services/chat/tests/test_generation_registry.py` (research.md §9)
- [X] T011 [P] [US1] Add failing tests for `POST /chat` in `services/chat/tests/test_chat_api.py`:
      session cookie issuance on first message and reuse thereafter; a follow-up answer reflects an
      earlier message (spec.md US1 Acceptance Scenarios 1–3); a burst of two messages sent before any
      reply produces exactly one reply reflecting both (Acceptance Scenario 4, FR-013/FR-014); the
      superseded message's stream ends with a `cancelled` event and no reply is stored for it
      (FR-015); a pipeline failure — mocking `answer_faq` to raise, distinct from cancellation —
      leaves the patient message persisted and available as context for the next call, with no
      assistant message stored for the failed attempt (FR-012, quickstart.md Scenario 5)

### Implementation for User Story 1

- [X] T012 [P] [US1] Add `ChatRequest`, `ChatTokenEvent`, `ChatDoneEvent`, `ChatCancelledEvent`
      Pydantic schemas in `services/chat/src/chat/domain/schemas.py` (contracts/openapi.yaml)
- [X] T013 [P] [US1] Implement `build_history_messages()` in
      `services/chat/src/chat/agent/history.py` — `Message` rows → alternating `user`/`assistant`
      dicts, merging any consecutive same-sender run into one entry (research.md §5) — depends on T004
- [X] T014 [P] [US1] Implement the in-flight generation registry (a process-local
      `dict[chat_id, asyncio.Task]`; cancel-then-register on a new message; the cancelled task's
      pipeline unwinds via `asyncio.CancelledError` with nothing to roll back) in
      `services/chat/src/chat/agent/generation_registry.py` (research.md §9)
- [X] T015 [US1] Modify `answer_faq.py` to accept a `history` parameter: prepend it to the Claude
      `messages` call, and pass the same merged trailing `user` entry to `search_faq` as the
      retrieval query instead of the raw current message alone (research.md §5/§6) in
      `services/chat/src/chat/agent/answer_faq.py` — depends on T013
- [X] T016 [US1] Implement `POST /chat` in `services/chat/src/chat/api/chat.py`: capture
      `bind_turn_id()`'s yielded value (`with bind_turn_id() as turn_id:` — currently discarded),
      resolve/create the session (setting the cookie only when new, FR-001/FR-010), get-or-create
      the chat, validate the message (FR-008), insert the patient `Message` with `id=turn_id` — the
      patient message never mints its own id; it reuses the request's existing `turn_id` so a
      message row and its log correlation id are always the same value (data-model.md, research.md
      §4) — cancel-and-register via `generation_registry` (FR-015), query history before insertion,
      call `answer_faq` with it, and insert the assistant `Message` (with a freshly minted ULID id)
      only on successful completion (FR-012); on cancellation, emit `ChatCancelledEvent` and store
      nothing (FR-015/FR-016) — depends on T006, T007, T012, T014, T015
- [X] T017 [US1] Run T009–T011 and confirm they now pass — depends on T016

**Checkpoint**: `POST /chat` is independently functional — persisted, context-aware, burst-safe,
cancel-safe.

---

## Phase 4: User Story 2 - See the chat so far, even after coming back (Priority: P2)

**Goal**: The full chat, including bursts and citations/abstention, is retrievable and displayed in
order after a reload, with live streaming and clean retraction on cancellation.

**Independent Test**: Send several messages in a chat, reload the page, and confirm every prior
message — patient and assistant alike, including citations or abstention — is still displayed in the
order it was sent (spec.md US2 Independent Test).

### Tests for User Story 2 (write first, confirm failing) ⚠️

- [X] T018 [P] [US2] Add failing tests for `GET /chat` in `services/chat/tests/test_chat_api.py`:
      full chronological history with sender labels (Acceptance Scenario 1); persists across a
      simulated reload (Scenario 2); citations/abstention preserved (Scenario 3); a burst displays
      both patient messages followed by one reply, not forced alternation (Scenario 4); empty
      response for a missing/unrecognized cookie (FR-010) rather than an error
- [X] T019 [P] [US2] Add failing tests for chat hydration on mount and non-alternating burst display
      in `services/frontend/tests/ChatWindow.test.tsx`; also cover that a `cancelled` streamed event
      removes the in-progress message bubble and any partial tokens already painted for it, with no
      error state shown (FR-016)
- [X] T020 [P] [US2] Add failing tests for `MessageView.tsx` rendering by sender with no derived
      "unanswered" indicator for a patient message not yet followed by a reply (research.md §8) in
      `services/frontend/tests/MessageView.test.tsx`

### Implementation for User Story 2

- [X] T021 [P] [US2] Add `MessageOut`, `ChatHistoryResponse` schemas in
      `services/chat/src/chat/domain/schemas.py` (contracts/openapi.yaml)
- [X] T022 [US2] Implement `GET /chat` in `services/chat/src/chat/api/chat.py`: read-only, never
      creates a session or chat, returns `list_messages` in chronological order (FR-002), empty
      response rather than an error for a missing/unrecognized cookie (FR-010) — depends on T007, T021
- [X] T023 [P] [US2] Implement `MessageView.tsx` in
      `services/frontend/src/components/MessageView.tsx` — renders one message by sender, reused for
      historical and in-progress messages — depends on T020
- [X] T024 [US2] Modify `chatStream.ts` to add `fetchChatHistory()` and parse the `cancelled`
      streamed event in `services/frontend/src/lib/chatStream.ts`
- [X] T025 [US2] Modify `ChatWindow.tsx` in `services/frontend/src/components/ChatWindow.tsx`: flat
      message-list state, hydrate via `fetchChatHistory()` on mount, render via `MessageView`, abort
      its own in-flight fetch (`AbortController`) when the patient sends a new message, keep painting
      tokens live but cleanly remove the bubble on a `cancelled` event (FR-016, research.md §10) —
      depends on T023, T024
- [X] T026 [US2] Run T018–T020 and confirm they now pass — depends on T022, T025

**Checkpoint**: US1 and US2 both work independently — persisted, context-aware, and fully displayed
chat history.

---

## Phase 5: User Story 3 - Clear the chat and start fresh (Priority: P3)

**Goal**: A confirmed "Clear chat" hard-deletes the current chat and its messages while keeping the
same session/cookie, so the next message starts a genuinely empty chat.

**Independent Test**: With an active chat containing several messages, click "Clear chat," confirm
the chat view is empty, then send a new message referencing something only mentioned in the cleared
chat and confirm the assistant does not use it (spec.md US3 Independent Test).

### Tests for User Story 3 (write first, confirm failing) ⚠️

- [X] T027 [P] [US3] Add failing tests for `DELETE /chat` in `services/chat/tests/test_chat_api.py`:
      hard-deletes the chat and its messages (FR-005), session cookie is untouched and not reissued,
      `204` no-op when there's no current chat (Acceptance Scenarios 1–3), next `POST /chat` starts a
      fresh chat with no memory of the cleared one (FR-006)
- [X] T028 [P] [US3] Add failing tests for `chat_repository.delete_chat`'s cascade behavior (deleting
      a `Chat` removes all its `Message` rows atomically; the `Session` row is untouched) in
      `services/chat/tests/test_chat_repository.py`
- [X] T029 [P] [US3] Add failing tests for the confirmation dialog and `DELETE /chat` call in
      `services/frontend/tests/ClearChatButton.test.tsx`

### Implementation for User Story 3

- [X] T030 [US3] Implement `DELETE /chat` in `services/chat/src/chat/api/chat.py`: hard-delete via
      `delete_chat` (FR-005), `204` no-op if no current chat exists, session/cookie left exactly as
      it was (FR-006) — depends on T007
- [X] T031 [P] [US3] Implement `ClearChatButton.tsx` in
      `services/frontend/src/components/ClearChatButton.tsx`: button + confirmation dialog ("All
      messages in the chat will be deleted. Do you agree?" / "Clear" / "Cancel", FR-004), calls
      `DELETE /chat` — depends on T029
- [X] T032 [US3] Wire `ClearChatButton` into `ChatWindow.tsx` and add `clearChat()` to
      `chatStream.ts` — depends on T025, T031
- [X] T033 [US3] Run T027–T029 and confirm they now pass — depends on T030, T032

**Checkpoint**: All three user stories independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Documentation and end-to-end validation across all three stories

- [X] T034 [P] Add a "Conversational Chat History: technology choices" README section documenting
      the `Session`/`Chat` split, the flat `Message` model, cancel-and-restart, and merged-burst
      retrieval tradeoffs (Constitution Principle VI; plan.md Constitution Check, research.md)
- [X] T035 Run `make lint` and `make typecheck` across all changed files and fix any violations
- [X] T036 Run quickstart.md Scenarios 1–6 end-to-end against a local `chat` service, PostgreSQL, and
      Qdrant, confirming each documented expectation holds

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories (Session/Chat/Message
  persistence is shared by all three)
- **User Stories (Phase 3+)**: All depend on Foundational completion
  - US1, US2, US3 can proceed in parallel if staffed, or sequentially in priority order (P1 → P2 → P3)
  - US2's `GET /chat` and US3's `DELETE /chat` reuse `chat_repository.py` (T007) but not any
    US1-specific code (`history.py`, `generation_registry.py`, `answer_faq.py` changes) — genuinely
    independent of US1 beyond the Foundational layer
- **Polish (Phase 6)**: Depends on all three user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational — no dependency on US2/US3
- **User Story 2 (P2)**: Can start after Foundational — independently testable via the API even
  before US1's UI work lands, though its frontend tasks (T023/T025) share `ChatWindow.tsx`/
  `chatStream.ts` with US1/US3's frontend tasks (sequential within those files)
- **User Story 3 (P3)**: Can start after Foundational — same file-sharing note as US2

### Within Each Phase

- Tests MUST be written and observed to fail before implementation (Constitution Principle VIII,
  NON-NEGOTIABLE)
- Schemas/models before services/repositories; repositories before endpoints; endpoints before
  frontend wiring
- `api/chat.py`'s three methods (T016 POST, T022 GET, T030 DELETE) touch the same file — sequential
  by story, never marked `[P]` against each other
- `ChatWindow.tsx`/`chatStream.ts` are touched by US2 (T024/T025) and US3 (T032) — sequential

### Parallel Opportunities

- T002/T003 (Foundational tests) — different files
- T004/T006 (models, cookie helper) — different files, no cross-dependency
- T009/T010/T011 (US1 tests) — different files
- T012/T013/T014 (US1 schemas/history/registry) — different files, no cross-dependency
- T018/T019/T020 (US2 tests) — different files
- T021/T023 (US2 schemas, MessageView) — different files
- T027/T028/T029 (US3 tests) — different files
- T031 (ClearChatButton) can start alongside T030 (DELETE endpoint) — different files, both only
  need T007/T029

---

## Parallel Example: User Story 1

```bash
# Launch all US1 tests together:
Task: "Add failing tests for build_history_messages() merge logic in services/chat/tests/test_history.py"
Task: "Add failing tests for the generation registry in services/chat/tests/test_generation_registry.py"
Task: "Add failing tests for POST /chat in services/chat/tests/test_chat_api.py"

# Launch independent US1 implementation pieces together:
Task: "Add ChatRequest/ChatTokenEvent/ChatDoneEvent/ChatCancelledEvent schemas in services/chat/src/chat/domain/schemas.py"
Task: "Implement build_history_messages() in services/chat/src/chat/agent/history.py"
Task: "Implement the generation registry in services/chat/src/chat/agent/generation_registry.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: run quickstart.md Scenarios 1, 3, 5, 6 against `POST`/no history UI yet
5. Deploy/demo if ready — a context-aware, burst-safe `POST /chat` is a real, demoable increment
   even before history display or clearing exist

### Incremental Delivery

1. Setup + Foundational → shared persistence ready
2. Add User Story 1 → validate independently → demo (MVP: context-aware chat via API)
3. Add User Story 2 → validate independently → demo (chat window shows full history on reload)
4. Add User Story 3 → validate independently → demo (Clear chat works end to end)
5. Each story adds value without breaking the previous ones

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: User Story 1 (backend-heavy: history merge, retrieval, cancellation)
   - Developer B: User Story 2 (GET endpoint + frontend display)
   - Developer C: User Story 3 (DELETE endpoint + Clear chat UI)
3. Coordinate on shared frontend files (`ChatWindow.tsx`, `chatStream.ts`) between B and C; A's
   backend work has no frontend file overlap with B/C until US1's own UI polish (streaming display)
   lands, which this task list places inside US2's `ChatWindow.tsx` work (T025) since that's where
   the message-list UI is first built

---

## Notes

- `[P]` tasks = different files, no dependency on an incomplete task
- `[Story]` label maps a task to its user story for traceability back to spec.md
- Every phase's tests MUST be written and confirmed failing before its implementation tasks begin
  (Constitution Principle VIII, NON-NEGOTIABLE — no exceptions)
- `api/chat.py`, `domain/schemas.py`, `ChatWindow.tsx`, and `chatStream.ts` are each touched by more
  than one phase — expected, given the `/conversation`→`/chat` path merge and the shared chat window;
  treat same-file tasks as strictly sequential regardless of phase
- Commit after each task or logical group
- Stop at any checkpoint to validate a story independently before continuing

---

## Phase 7: Convergence

**Purpose**: Close documentation drift found by `/speckit-converge` — real, correct code changes made
during post-implementation debugging (`created_at` message ordering + index, `reply_to_message_id`
turn-linkage, `turn.cancelled`/`message.persisted` diagnostic logging) were not accompanied by the
design-doc updates Constitution Principle VI requires in the same change.

- [X] T037 Update `specs/003-conversational-chat-history/data-model.md` per Constitution Principle VI
      (contradicts): add `reply_to_message_id` to the `Message` field table (self-referential FK to
      the patient message an assistant reply answers, `ON DELETE SET NULL` — `domain/models.py`,
      migration `044df0236efe`); correct the "Ordering" note — messages are now listed by `created_at`
      ascending via a dedicated `ix_messages_chat_id_created_at` index, not by `id` ascending, because
      ULID `id` order is not reliably equivalent to `created_at` order across concurrent writers
      (`chat_repository.list_messages`); correct the "Runtime state" section — the in-flight
      generation registry is now `dict[chat_id, tuple[turn_id, asyncio.Task]]`, not
      `dict[chat_id, asyncio.Task]` (`generation_registry.py`)
- [X] T038 Update the "Conversational Chat History: technology choices" section in `README.md` per
      Constitution Principle VI (contradicts): correct the "ordered by ULID id" claim to describe
      `created_at`-based ordering and why (ULID order isn't reliably equivalent under concurrent
      writers); correct `generation_registry.py`'s dict shape description; add an entry documenting
      `reply_to_message_id` (ties an assistant reply to the specific patient turn it answers, so a
      stray or out-of-order write can no longer corrupt a different turn's history) and the
      `message.persisted`/`turn.cancelled` diagnostic logging, each with its tradeoff — depends on T037
