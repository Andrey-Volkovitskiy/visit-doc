---

description: "Task list for Escalation and the Staff Console (Phase 1d, part 2)"
---

# Tasks: Escalation and the Staff Console (Phase 1d, part 2)

**Input**: Design documents from `/specs/007-escalation-and-staff-console/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: Per Constitution Principle VIII (Test-Driven Development, NON-NEGOTIABLE), test tasks are
mandatory and MUST precede their implementation tasks: contract → test cases → tests (observed
failing) → implementation → tests run (observed passing). Every task group below is ordered that way;
do not reorder it.

**Organization**: Tasks are grouped by user story so each story can be implemented, tested, and
demoed independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US5)
- Exact file paths are included in every task

## Path Conventions

`uv` workspace monorepo (see plan.md "Source Code"): `services/chat/src/chat/`,
`services/scheduler/src/scheduler/`, `services/frontend/src/`, `packages/shared-proto/src/`. Unit
tests are colocated per member (`<member>/tests/`); the frontend's live in
`services/frontend/tests/`; cross-service tests live at `tests/integration/`.

**Unlike 006, `services/frontend/` is the largest single surface here.** It gets its own test tasks
throughout, run by `make test-frontend` (vitest), and it is not an afterthought phase.

**Mocking discipline** (`docs/testing-strategy.md`): every test that exercises a turn MUST mock
`AsyncAnthropic` — including the tool-use responses that make the model call `escalate_to_staff` —
and assert on unmocked artifacts (rows written, Qdrant filters issued, log events emitted), never on
canned model text.

**Where the weight is, and it is not where 006's was.** Three things carry this feature and each
fails differently:

1. **The negative assertions.** SC-002 requires that a silent conversation issues *no* classification,
   retrieval, tool or generation call. A gate placed one node too late passes every "no reply
   appeared" test and fails this one. Assert absences, not just outcomes.
2. **The two axes.** Silencing and emphasis are separate at message level (FR-027c) *and* at
   conversation level (research #1). An implementation that collapses them passes most of the suite
   and fails FR-003d, FR-017b and FR-027e — all of which are one-line tests that must exist.
3. **The filters.** Retrieval's session/live-revision predicate must be a term on the Qdrant search,
   not a post-filter. A post-filter passes every mocked test and leaks another session's corpus in
   the app. Assert against a real Qdrant.

**This deployment is destructive** (FR-039e). Phase 2 deletes every existing session, chat, message,
FAQ entry, patient, practitioner and appointment, and drops the Qdrant collection. That is a
requirement, not a convenience — two new columns are `NOT NULL` — and nothing restores it.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the pre-change baseline and the two new settings. This feature adds **no
dependency in any member, Python or Node** (plan.md "Technical Context"), so there is nothing to
install.

- [X] T001 Verify the 006 baseline is green before any edit: `make sync && make lint && make typecheck && make test` from the repo root, and record the pass
- [X] T002 [P] Confirm the chat test database is reachable, since the state machine and the revision write path both run against a real one: `docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_chat_test -c '\d chats'` shows the pre-007 shape with none of the four new columns
- [X] T003 [P] Confirm Qdrant is reachable and note what the collection currently holds, since Phase 2 drops it: `curl -s localhost:6333/collections/faq_chunks | jq '.result.points_count'`
- [X] T004 [P] Add `ADMIN_SECRET` to the repo-root `.env` per quickstart.md "Prerequisites" — both services read that one file — and record all four new settings in `.env.example` alongside the existing keys, in its `services/chat` section

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The reset, the schema, the shared vocabulary, the settings, and the **FAQ retrieval read
path**. Every user story reads or writes conversation state, so none can begin until the columns
exist; and US1's P1 abstention→escalation depends on retrieval being correctly scoped, so the read
half of the revision design lands here rather than waiting for US5's write half.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

**⚠️ DESTRUCTIVE**: T012 and T014 delete data irreversibly, and T016 drops the Qdrant collection.

### Shared vocabulary and settings

- [X] T005 [P] Write tests in `services/chat/tests/test_models.py` for the three new Python-level closed sets: `MessageSender.STAFF` exists and `MessageSender` still has exactly three members; `AttentionMark` has exactly the four kinds of FR-027a; `EscalationReason` has exactly the three of FR-007a; and a `CLEARABLE_MARKS` constant contains exactly `patient_asked_for_person` and `unanswered` — the `IN` list of the clearing statement, so FR-027c's lifetimes are one fact in one place; observe failing
- [X] T006 Add `MessageSender.STAFF`, `AttentionMark`, `EscalationReason` and `CLEARABLE_MARKS` to `services/chat/src/chat/domain/models.py` per data-model.md "The four kinds"; run T005 to green
- [X] T007 [P] Write tests in `services/chat/tests/test_dependencies.py` (or a new `test_config.py`) that `Settings` exposes `ADMIN_SECRET` defaulting to `""`, `SCHEDULING_HTTP_BASE_URL`, `FAQ_MAX_ENTRIES_PER_SESSION` defaulting to `200`, and `ASSISTANT_PAUSE_SECONDS` defaulting to `120`; observe failing
- [X] T008 Add those four fields to `services/chat/src/chat/core/config.py`, each with the comment recording why the value is what it is (FR-039f's cap is a single configured value; two minutes is chosen, not derived); run T007 to green
- [X] T009 [P] Write a test in `services/chat/tests/test_logging.py` that a log event carrying the live `ADMIN_SECRET` value under any key is redacted, and that a key literally named `admin_secret` is redacted regardless of value (FR-050); observe failing
- [X] T010 Add `ADMIN_SECRET` to `_SECRET_SETTINGS_FIELDS` in `services/chat/src/chat/core/logging.py` — one line, the **existing** redaction path and not a new one; run T009 to green

### The reset and the schema

- [X] T011 [P] Extend `services/chat/tests/test_migrations.py` with the upgrade/downgrade round trip for the conversation-state revision: `chats` has `escalated_at`, `escalation_reason`, `assistant_paused_until`, `attention_since`, all nullable; the `CHECK` rejects a row with `escalated_at` set and `escalation_reason` NULL **and** the reverse; `ix_chats_session_attention` exists; `messages.attention_mark` exists with the **partial** index `WHERE attention_mark IS NOT NULL`; observe failing
- [X] T012 Create `services/chat/alembic/versions/*_add_conversation_attention_state.py` per plan.md "Storage" step 1 — four columns on `chats`, the reason CHECK, the listing index, one column on `messages`, its partial index. **Non-destructive**: every column is nullable by nature, since an ordinary open conversation is one where all of them are NULL
- [X] T013 [P] Extend `services/chat/tests/test_migrations.py` with the FAQ-ownership revision: `faq_entries.session_id` is `NOT NULL` with an FK to `sessions` and `ON DELETE CASCADE`; `faq_entries.live_revision` is `NOT NULL`; there is **no** CHECK constraint on the pair; `ix_faq_entries_session` exists; and inserting an entry with a NULL `session_id` is rejected by the datastore rather than by application code (FR-040 as a schema rule); observe failing
- [X] T014 Create `services/chat/alembic/versions/*_scope_faq_entries_to_sessions.py` per plan.md "Storage" step 2. **DESTRUCTIVE**: it begins with `DELETE FROM sessions;` (cascading chats and messages) and `DELETE FROM faq_entries;`, then adds both columns `NOT NULL` with no CHECK. The deletion belongs inside this migration because it is the only place that can guarantee the table is empty at the instant `NOT NULL` is applied (research #11); its `downgrade()` restores the columns' absence and **cannot** restore the data, and must say so in a comment
- [X] T015 Update `services/chat/src/chat/domain/models.py` to match both migrations: `Chat` gains the four columns and the `CheckConstraint`; `Message` gains `attention_mark`; `FaqEntry` gains `session_id` (FK, cascade) and `live_revision`, both non-nullable; run T011 and T013 to green
- [X] T016 Create `services/scheduler/alembic/versions/*_reset_session_data.py` — a **data-only** revision deleting every practitioner and every patient (appointments follow by the FK cascades 005 created and 006 left status-blind). The scheduler's **schema does not change**; this revision exists only so the reset is ordered and recorded with the deploy rather than remembered (FR-039e). Extend `services/scheduler/tests/test_migrations.py` with its round trip first, and observe it failing
- [X] T017 Record the one **manual** reset step in quickstart.md's prerequisites and verify it by hand: `curl -X DELETE localhost:6333/collections/faq_chunks`, run **before** the chat service starts. Deliberately not automated — nothing the application does at startup should be capable of dropping the corpus (plan.md "Storage")

### Conversation-state primitives

- [X] T018 [P] Write tests in `services/chat/tests/test_chat_repository.py` for the state read and its two derived values, against a real database: `may_assistant_reply` is false while `escalated_at` is set, false while `assistant_paused_until > now()`, and true otherwise; `pause_seconds_remaining` is an integer while a pause runs and NULL once it has elapsed **and** NULL while escalated with no pause; both are computed in SQL against the **database's** clock, so a Python-side `datetime.now()` cannot decide them (research #2); observe failing
- [X] T019 [P] Write tests in `services/chat/tests/test_attention_marks.py` for the clearing statement in isolation: it nulls every `patient_asked_for_person` and `unanswered` mark in one chat in one statement, however many; it leaves `corpus_could_not_answer` and `assistant_failed` untouched; and it touches no other chat's rows; observe failing
- [X] T020 Implement the state primitives in `services/chat/src/chat/repositories/chat_repository.py` — `get_conversation_state()`, `set_escalated()`, `clear_escalation()`, `set_paused_until()`, `set_attention_since()`, `clear_attention()`, `clear_clearable_marks()` — every one of them carrying `session_id` in its `WHERE` clause, and every deadline comparison written as SQL `now()` rather than a Python value; run T018 and T019 to green

### FAQ retrieval — the read half of the revision design

- [X] T021 [P] Write tests in `services/chat/tests/test_qdrant_repository.py` against a real Qdrant: `ChunkPayload` carries `session_id` and `revision` alongside `faq_entry_id`/`chunk_index`/`chunk_text`; a search filtered to a set of live revisions returns only points carrying those revisions; a point belonging to another session's revision is **not returned at all**, rather than returned and discarded; and `ensure_collection` creates payload indexes on `revision`, `faq_entry_id` and `session_id` idempotently; observe failing
- [X] T022 Extend `services/chat/src/chat/repositories/qdrant_repository.py` per data-model.md "Retrieval store": the two payload fields, the payload indexes in `ensure_collection`, and `search()` taking `live_revisions: list[str]` and issuing `Filter(must=[MatchAny(key="revision", any=live_revisions)])` — a **term on the search**, never a check on its results (FR-039a, FR-042d); run T021 to green
- [X] T023 [P] Write tests in `services/chat/tests/test_indexing.py` (retrieval side) that `search_faq` short-circuits on an empty live-revision set: zero embedding calls and zero Qdrant calls are issued, and it returns `[]` — because with no live revisions no filter value could match, so spending two dependencies to learn what the empty list already said is waste (research #14); observe failing
- [X] T024 Change `services/chat/src/chat/rag/retriever.py`'s `search_faq` to take `live_revisions` and short-circuit on the empty set, passing the filter through to `search()`; run T023 to green
- [X] T025 [P] Write a test in `services/chat/tests/test_faq_repository.py` that `live_revisions(session, session_id)` returns the session's live revisions and **only** those, scoped by the `WHERE` clause rather than filtered afterwards; observe failing
- [X] T026 Add `live_revisions()` to `services/chat/src/chat/repositories/faq_repository.py`, and add the session predicate to every existing function there (`get`, `list_all`, `update`, `delete`) so no FAQ read or write can address another session's row (FR-032); run T025 to green
- [X] T027 [P] Write the FR-042j pair in `services/chat/tests/test_turn_api.py`: with a genuinely **empty** corpus the turn abstains and escalates; with the stored rows made **unreadable** mid-turn the request fails as a dependency failure, produces **zero** abstentions, raises `critical.dependency_unreachable`, and never tells the patient the corpus has no answer — these two must never be reported the same way (SC-015f); observe failing
- [X] T028 Read the session's live revisions in `services/chat/src/chat/api/turn.py`, inside the **same** locked transaction that inserts the patient's message, and thread them through `run_turn` into `answer_faq`. A Postgres failure therefore fails the turn before the FAQ path is entered, so "empty" and "unreadable" can never collapse into one value (research #14); run T027 to green
- [X] T029 [P] Thread `live_revisions` through `services/chat/src/chat/agent/graph.py`'s `_GraphState` and into `services/chat/src/chat/agent/answer_faq.py`'s `search_faq` call, and update `services/chat/tests/test_graph.py` and `services/chat/tests/test_indexing.py` for the new signature
- [X] T030 [P] Pin the provisioning cost in `services/chat/tests/test_chats_api.py`: creating a session issues **zero** embedding calls and **zero** retrieval-store writes, and a session created while Qdrant is unreachable still yields a working chat. This asserts an **absence**: session provisioning must not gain a corpus step, so a new session's corpus is empty because nothing seeded it rather than because a seeding step failed (FR-039b, SC-011b). It should pass on its first run — if it does not, something in this phase gave provisioning a corpus step

**Checkpoint**: the stores are reset, the schema carries both axes and both ownership columns, and every FAQ turn retrieves only what its own session's rows vouch for. User story work can begin.

---

## Phase 3: User Story 1 - Reach a human when the assistant cannot help (Priority: P1) 🎯 MVP

**Goal**: A patient asks for a person (or the corpus cannot answer them), the assistant hands the
conversation over and stops replying in it, and a staff member reads the whole thread and writes back
into it — labelled *Staff*, in the patient's own conversation, arriving without a new window.

**Independent Test**: In one chat, ask to speak to a person. Verify the assistant says it has handed
the conversation to staff and that the conversation is marked escalated; send another message and
verify **no** classification, retrieval, tool call or generation is issued for it. Then, from the
staff pane, open that conversation, see the patient's full history, post a reply, and verify it
appears in the patient's own thread labelled *Staff* with the assistant's replies labelled
*AI assistant*.

**Deliberate layering**: this story's staff pane lists conversations with the **existing**
`GET /chats` and reads a thread with the **existing** `GET /chats/{id}/messages`. The console read
model — emphasis, ordering, the attention total, polling — is US2's, and nothing here pre-empts it.

### Tests for User Story 1 (write first, confirm failing) ⚠️

- [X] T031 [P] [US1] Write the collector tests in `services/chat/tests/test_escalation.py`: `EscalationRequests` resolves the conversation's reason to the highest-precedence **silencing** one; resolves the message's mark by `patient_asked_for_person` > `corpus_could_not_answer` > `assistant_failed`; and produces the same result whichever order two requests were recorded in — because two specialists can record concurrently and order must not decide the outcome (research #5, #6); observe failing
- [X] T032 [P] [US1] Write the transition tests in `services/chat/tests/test_escalation.py`: `apply_escalation` with a silencing reason sets `escalated_at`, `escalation_reason` **and** `attention_since`; with `assistant_failed` it sets `attention_since` and marks the message but leaves `escalated_at` NULL (FR-003d); a second escalation against an already-escalated conversation transitions nothing and **does not overwrite** the first reason (FR-007); observe failing
- [X] T033 [P] [US1] Write the tool tests in `services/chat/tests/test_escalation.py`: `escalate_to_staff` is in the registry with an **empty** input schema and `additionalProperties: false`; `requires_patient` is false so it runs in a chat with no patient record (FR-002); its handler performs **no I/O** and writes no state, only records into the turn's collector; it returns `status: "ok"`; observe failing
- [X] T034 [P] [US1] Write the silence-gate tests in `services/chat/tests/test_silence_gate.py` — the story's most important tests, and the ones an implementation passes by accident. In an escalated conversation, a patient message is stored, carries `attention_mark = "unanswered"`, sets `attention_since` if unset, and the response terminates with `{"type":"silent"}`; and **zero** of `intent.classified`, `turn.retrieval_completed`, `turn.groundedness_verdict` and any tool dispatch are emitted, asserted as absences (SC-002); observe failing
- [X] T035 [P] [US1] Write the abstention-escalates tests in `services/chat/tests/test_turn_api.py`: a question the corpus does not answer abstains, escalates with reason `corpus_could_not_answer`, marks its message, and produces **no** speculative answer alongside the abstention (FR-003b); the escalation is recorded **before** any generation call is made; and it fires against an **empty** corpus with no exemption (FR-003c, SC-001a); observe failing
- [X] T036 [P] [US1] Write the failure-path tests in `services/chat/tests/test_handle_booking.py`: `status: "unavailable"`, `status: "unknown"`, a handler raising, and an unregistered tool name each record `assistant_failed`; a `status: "refused"` with any of 006's twelve reasons records **nothing** (FR-003a); and `ToolArgumentError` records **nothing**, because it provably had no effect and the model gets another attempt in the same turn; observe failing
- [X] T037 [P] [US1] Write the FR-006 test in `services/chat/tests/test_turn_api.py`: a turn that escalates **completes** first — its reply is delivered in full and the conversation transitions at the end of the turn, so a mixed-intent message whose FAQ half abstains and whose booking half succeeds delivers both halves and escalates afterwards (spec Edge Cases); observe failing
- [X] T038 [P] [US1] Write the handoff-message tests in `services/chat/tests/test_turn_api.py`: in the same turn it escalates, the assistant tells the patient a staff member has been notified and will reply **in this conversation**, and **names no timeframe** — asserted against a scripted model response, including a turn where the request is mixed with an ordinary question (FR-005, SC-001). The same turn asks the patient for **no confirmation**: unlike a change to an appointment, an escalation alters no record they hold and is reversible (FR-004); observe failing
- [X] T039 [P] [US1] Write the escalation-persistence tests in `services/chat/tests/test_escalation.py`: the escalated mark survives a reload, a second tab, and a **backend restart** — it is a property of the stored conversation, not of an open connection, so a restarted process finds it exactly as it was and still generates no reply in it. T079 covers the pause's half of SC-006; this is the other half, and without it the suite would test persistence only for the silence that expires (FR-012, SC-006); observe failing
- [X] T040 [P] [US1] Write the scope-and-lifetime tests in `services/chat/tests/test_escalation.py`: an escalation binds **exactly one** conversation, so every other chat in the same session classifies, retrieves and answers normally while it is silent (FR-011, US1 scenario 8); and a conversation whose escalation was ended by a staff message can be escalated **again** later, as a fresh escalation with its own `attention_since` rather than a resumption of the first (FR-010); observe failing
- [X] T041 [P] [US1] Write the staff-message tests in `services/chat/tests/test_staff_messages.py`: `POST /console/chats/{id}/messages` stores a message with `sender = "staff"`, ends the escalation, clears `attention_since`, clears every clearable mark at once and leaves permanent ones; it is accepted in an **unescalated** conversation too (FR-024); and a chat id from another session returns 404 indistinguishable from one that never existed (FR-032); observe failing
- [X] T042 [P] [US1] Write the history tests in `services/chat/tests/test_history.py`: a staff message joins the clinic's side of the conversation, so `split_into_bursts` groups it with assistant messages and `to_claude_messages` maps it to role `assistant` — both already written against the patient/not-patient distinction, so this test pins behaviour that should need no code change (research #8, FR-026); observe failing
- [X] T043 [P] [US1] Write the wire tests in `services/frontend/tests/chatStream.test.ts`: `{"type":"silent"}` parses as a terminal event distinct from `done` and `cancelled`; a `Message` accepts `sender: "staff"` and an `attention_mark`; and **no** `staff_name` field exists on any message (FR-021, research #10); observe failing
- [X] T044 [P] [US1] Write the label tests in `services/frontend/tests/MessageView.test.tsx`: a staff message renders the label **"Staff"**, an assistant message renders **"AI assistant"**, a patient message renders **no** label, and no person's name appears on any of them (FR-023, SC-011c); observe failing
- [X] T045 [P] [US1] Write the staff-pane tests in `services/frontend/tests/StaffThread.test.tsx`: it renders the whole thread — patient, assistant and staff messages in one ordered list (FR-025) — and its composer posts to `POST /console/chats/{id}/messages`; observe failing
- [X] T046 [P] [US1] Write the two-pane test in `services/frontend/tests/App.test.tsx`: both sides render at once, with no authentication prompt anywhere (FR-030, FR-031, SC-017); observe failing

### Implementation for User Story 1

- [X] T047 [US1] Create `services/chat/src/chat/agent/escalation.py`: `EscalationRequests` (the per-turn collector, with the precedence of research #6) and `apply_escalation()` — the **one** writer, and the only place a transition is performed; run T031 and T032 to green
- [X] T048 [US1] Create `services/chat/src/chat/agent/tools/staff_tools.py` with `escalate_to_staff` per `contracts/agent-tools.md`: empty schema, `requires_patient=False`, `writes=False`, and a handler that records into the collector and returns `{"status": "ok", ...}` without touching a store
- [X] T049 [US1] Add the collector to `ToolContext` in `services/chat/src/chat/agent/tools/registry.py` as ambient state, so a model can escalate only the conversation it is in and cannot address another; register `STAFF_TOOLS` alongside `SCHEDULING_TOOLS`; run T033 to green. **Since revised**: `turn.py` builds the `ToolContext` only, and each node declares its own tool set and builds its own registry in `agent/graph.py` — a bag shared by the whole graph offers a node capabilities its step was never meant to have
- [X] T050 [US1] Record `corpus_could_not_answer` in `services/chat/src/chat/agent/answer_faq.py`'s abstention branch, on the same signal that produces the abstention and **before** any generation call, and thread the collector through `_GraphState` in `services/chat/src/chat/agent/graph.py` — as a mutable object, deliberately **not** a LangGraph state key, since two specialists may write it concurrently (research #5); run T035 to green
- [X] T051 [US1] Record `assistant_failed` on the three failure statuses in `services/chat/src/chat/agent/handle_booking.py`, mapping 006's result vocabulary exactly as `contracts/agent-tools.md`'s table sets out — and leaving every `refused` alone; run T036 to green
- [X] T052 [US1] Add `ChatSilentEvent` to `services/chat/src/chat/domain/schemas.py` and add `attention_mark` to `MessageOut`; **no `staff_name` field** — `sender` already carries everything the label states (contracts/http-api.md)
- [X] T053 [US1] Implement the silence gate in `services/chat/src/chat/api/turn.py`: read the conversation's state **inside** the existing `lock_chat` section, in the same transaction that inserts the patient's message; when the assistant may not speak, mark the message `unanswered`, set `attention_since` if unset, emit `{"type":"silent"}` and return **without building a registry or constructing the graph** (research #3); run T034 to green
- [X] T054 [US1] Call `apply_escalation()` once in `services/chat/src/chat/api/turn.py` **after** the graph completes, so the turn runs to completion and the state takes effect at the end of it (FR-006); run T037 to green
- [X] T055 [US1] Create `services/chat/src/chat/api/console.py` with `POST /console/chats/{chat_id}/messages` per `contracts/http-api.md`, performing steps 2–5 of its one transaction (insert, end escalation, clear attention, clear clearable marks). **Steps 1 and 6 — cancelling a running generation, and starting the pause — are US3's**, and the endpoint is correct without them; register the router in `services/chat/src/chat/main.py`; run T041 to green
- [X] T056 [US1] Confirm `services/chat/src/chat/agent/history.py` needs no change for a third sender, and run T042 to green. If it does need one, the docstrings in `split_into_bursts` and `to_claude_messages` claiming otherwise are what must be corrected with it
- [X] T057 [P] [US1] Add the `silent` terminal event and the `staff` sender to `services/frontend/src/lib/chatStream.ts`; the client renders **nothing** for `silent` (FR-019); run T043 to green
- [X] T058 [P] [US1] Add the two role labels to `services/frontend/src/components/MessageView.tsx` — "Staff" and "AI assistant", with patient messages unlabelled; run T044 to green
- [X] T059 [US1] Create `services/frontend/src/components/StaffThread.tsx` (thread + composer) and `services/frontend/src/lib/consoleApi.ts`, listing conversations from the existing `GET /chats` for now; run T045 to green
- [X] T060 [US1] Restructure `services/frontend/src/App.tsx` into two panes — patient chats on one side, the staff pane on the other — and add `/console` to the dev proxy in `services/frontend/vite.config.ts` (`/chat` already covers `/chats` by prefix; `/admin` is deliberately **not** proxied, since nothing in the browser calls it); run T046 to green
- [X] T061 [US1] Add the FR-033 records for this story to `services/chat/src/chat/agent/escalation.py` and `services/chat/src/chat/api/console.py` per `contracts/log-events.md`: `escalation.raised` (with `silenced`), `escalation.unchanged`, `escalation.ended`, `message.unanswered`, `staff.message_posted`. Recording is best-effort and MUST NOT gate a transition (FR-034)
- [X] T062 [US1] Write and run the record tests in `services/chat/tests/test_escalation.py`: across a suite that escalates already-escalated conversations, the count of `escalation.raised` equals the number of conversations actually silenced, with every no-op present as `escalation.unchanged` — one escalation record means one handoff (SC-010)

**Checkpoint**: a patient can reach a person and a person can answer them, in the same thread. Everything after this makes that visible, safe, or convenient.

---

## Phase 4: User Story 2 - Know which conversations need a person (Priority: P2)

**Goal**: The staff side lists every conversation in the session, emphasizes the ones needing a
person, sorts them first, keeps a total visible from either pane, and shows on the individual
message *why* it needs attention — all arriving without a reload.

**Independent Test**: With the staff pane open, escalate from the patient pane and verify the
conversation is emphasized and moves to the top without a refresh. Send another patient message and
verify it is marked unanswered and the conversation emphasized. Open it, read it, verify both are
still there — then reply and verify both clear together.

### Tests for User Story 2 (write first, confirm failing) ⚠️

- [X] T063 [P] [US2] Write the read-model tests in `services/chat/tests/test_console_api.py`: `GET /console/conversations` returns **every** chat in the session, emphasized or not (FR-027); `emphasized` is true when escalated **or** when `attention_since` is set; `assistant_may_reply` and `pause_seconds_remaining` are derived, never stored (FR-017a); and `attention_total` counts a conversation **once** however many marks sit inside it (spec Edge Cases); observe failing
- [X] T064 [P] [US2] Write the ordering tests in `services/chat/tests/test_console_api.py`: emphasized conversations sort above unemphasized ones, and within them the one waiting longest — smallest `attention_since` — comes first; a later escalation on an already-emphasized conversation does **not** re-stamp `attention_since`, because it has been waiting since the first (FR-027, research #1); observe failing
- [X] T065 [P] [US2] Write the FR-003d grid test in `services/chat/tests/test_attention_marks.py`: a conversation whose only call to staff was `assistant_failed` is **emphasized but not silenced**, answers the patient's very next message, stays emphasized until a staff reply, and keeps its permanent mark across that reply (SC-009f). This is the single test that catches an implementation which collapsed the two conversation-level axes into one; observe failing
- [X] T066 [P] [US2] Write the FR-027e test in `services/chat/tests/test_attention_marks.py`: a conversation whose only remaining marks are permanent is **not** emphasized, and those marks are still shown on their messages; observe failing
- [X] T067 [P] [US2] Write the cross-session test in `services/chat/tests/test_console_api.py`: the listing never contains another session's conversation, and a well-formed chat id belonging to another session resolves to nothing on every console route (FR-032, SC-011); observe failing
- [X] T068 [P] [US2] Write the poll tests in `services/frontend/tests/useConsolePoll.test.ts`: the hook polls one endpoint on a 2-second interval; a failed poll leaves the last good state and is corrected by the next tick rather than surfacing an error — a poll reads stored state and therefore self-heals, which is the whole argument for it (research #19); observe failing
- [X] T069 [P] [US2] Write the list tests in `services/frontend/tests/StaffConsole.test.tsx`: emphasized conversations render with visual prominence and sort first; the attention total renders; and it stays visible while the patient pane has focus (FR-028); observe failing
- [X] T070 [P] [US2] Write the mark tests in `services/frontend/tests/StaffThread.test.tsx`: a marked message renders its mark, and asking what it means reveals which of the four kinds it is (FR-027a); observe failing

### Implementation for User Story 2

- [X] T071 [US2] Add `list_conversations_for_console()` to `services/chat/src/chat/repositories/chat_repository.py` — one query, session-scoped, computing `emphasized`, `assistant_may_reply` and `pause_seconds_remaining` as derived columns in SQL, joined to each chat's newest message time
- [X] T072 [US2] Add `GET /console/conversations` to `services/chat/src/chat/api/console.py` per `contracts/http-api.md`, returning the ordering, the derived fields and `attention_total`; run T063, T064 and T067 to green
- [X] T073 [US2] Verify the two conversation-level axes hold end to end and run T065 and T066 to green, fixing `services/chat/src/chat/agent/escalation.py` if `assistant_failed` was wired to set `escalated_at` — the mark, `attention_since` and emphasis are its whole effect (FR-003d)
- [X] T074 [P] [US2] Create `services/frontend/src/lib/useConsolePoll.ts` — the 2-second poll of the one endpoint, serving both panes; run T068 to green
- [X] T075 [P] [US2] Create `services/frontend/src/components/StaffConsole.tsx` (the list, emphasis, ordering, the total); run T069 to green
- [X] T076 [US2] Render per-message marks in `services/frontend/src/components/StaffThread.tsx`, with the four kinds distinguishable on request; run T070 to green
- [X] T077 [US2] Refetch the active thread in `services/frontend/src/components/ChatWindow.tsx` when the poll reports that conversation's `last_message_at` has advanced past what it holds — which is what makes a staff reply appear in the patient's pane without a reload, using the same single poll rather than a channel of its own (FR-029c, SC-004)
- [X] T078 [US2] Point `services/frontend/src/components/StaffThread.tsx` and `services/frontend/src/lib/consoleApi.ts` at `GET /console/conversations`, replacing US1's interim use of `GET /chats`

**Checkpoint**: an escalation raised on one pane is visible on the other within ~2 seconds, and a staff member can see which message needs them and why.

---

## Phase 5: User Story 3 - Lead a conversation without the assistant talking over you (Priority: P3)

**Goal**: A staff reply — or the switch alone — silences the assistant in that conversation for two
minutes, restartable, endable early, and self-lifting; and the assistant never goes back to answer
what arrived while it was quiet.

**Independent Test**: Reply as staff in an ordinary, unescalated conversation. Verify the countdown
appears, that a patient message sent within the two minutes gets no reply and is marked unanswered,
and that after two minutes the assistant answers again. Repeat, turning the switch on instead of
waiting. Then, in an untouched conversation, turn the switch **off** without writing anything and
verify the same two-minute silence starts.

### Tests for User Story 3 (write first, confirm failing) ⚠️

- [X] T079 [P] [US3] Write the pause tests in `services/chat/tests/test_assistant_switch.py`: a staff message sets `assistant_paused_until = now() + 2 minutes` whether or not the conversation was escalated (FR-013); a further staff message restarts it (FR-014); it lifts by itself with no staff action (FR-016); and it survives a simulated backend restart with the correct time remaining, because it is a stored deadline and not a timer (FR-018, SC-006); observe failing
- [X] T080 [P] [US3] Write the switch tests in `services/chat/tests/test_assistant_switch.py`: `{"enabled": true}` clears `escalated_at`, `escalation_reason` and `assistant_paused_until`, and **touches neither** `attention_since` nor any mark (FR-017b); `{"enabled": false}` writes the **identical** pause a staff message writes, restarts it if one was running, and can never create an escalation (FR-017b, spec US3 scenario 6a); turning on an assistant that is already on changes nothing and is not an error; observe failing
- [X] T081 [P] [US3] Write the escalation-never-expires test in `services/chat/tests/test_assistant_switch.py`: an escalated conversation left well beyond the pause duration is **still** silent and still marked, and is ended by exactly two things — a staff message, or the switch (FR-009, FR-009a, SC-002b, SC-007); observe failing
- [X] T082 [P] [US3] Write the cancellation tests in `services/chat/tests/test_staff_messages.py`: a staff message posted before the first token, mid-stream, and just before completion each cancels the generation, and **zero** partial replies are persisted or displayed (FR-013a, SC-002c); the same holds when the switch is turned **off** mid-stream (FR-017c); observe failing
- [X] T083 [P] [US3] Write the ordering test in `services/chat/tests/test_staff_messages.py`: the staff-post path and the turn path serialize on the chat's advisory lock, and the turn's task is registered **inside** that lock — so a staff post can never slip between a turn passing the gate and its generation starting. Assert it by racing a staff post against a turn and finding no orphaned reply (research/plan "Cancellation by a staff message"); observe failing
- [X] T084 [P] [US3] Write the FR-019a/b tests in `services/chat/tests/test_history.py`: a patient message carrying the `unanswered` mark is excluded from the burst a later turn answers, so a turn after a pause expires answers **only** the message sent after it; the marked messages remain in the history as context; and `turn.message_received`'s `message_ids_unified` holds exactly one id (US3 scenario 10, spec Edge Cases); observe failing
- [X] T085 [P] [US3] Write the alternation test in `services/chat/tests/test_history.py`: the split of FR-019b creates two consecutive patient-sided bursts, which `to_claude_messages` must rejoin into one `user` entry — the Messages API requires strict alternation, and this is the one place the exclusion touches the model-facing shape (research #9); observe failing
- [X] T086 [P] [US3] Write the switch UI tests in `services/frontend/tests/StaffThread.test.tsx`: the switch **always** states the derived answer rather than appearing only while something is wrong (FR-017); it shows the remaining seconds while a pause runs and no deadline while escalated; it is off on a conversation the patient just escalated, without the staff member inferring it (US2 scenario 9); and it works in both directions; observe failing
- [X] T087 [P] [US3] Write the countdown-sync test in `services/frontend/tests/StaffThread.test.tsx`: the remaining time comes from the server's `pause_seconds_remaining` and is re-synced on each poll rather than counted from a locally-recorded start, so two tabs agree (FR-018); observe failing

### Implementation for User Story 3

- [X] T088 [US3] Add steps 1 and 6 to `POST /console/chats/{chat_id}/messages` in `services/chat/src/chat/api/console.py` — cancel any running generation via the existing `generation_registry`, and set `assistant_paused_until` — completing the one transaction `contracts/http-api.md` specifies; run T079 and T082 to green
- [X] T089 [US3] Move `register_and_cancel_previous` **inside** the `lock_chat` section in `services/chat/src/chat/api/turn.py`, so a turn that passed the gate has registered before the lock is released and a subsequent staff post's cancel cannot miss it; run T083 to green
- [X] T090 [US3] Add `POST /console/chats/{chat_id}/assistant` to `services/chat/src/chat/api/console.py`, both directions per `contracts/http-api.md`: `true` clears both silences, `false` writes the same pause a message writes and cancels a running generation, and **neither** touches `attention_since` or a mark; run T080, T081 and T086 to green
- [X] T091 [US3] Add `exclude_silent_window()` to `services/chat/src/chat/agent/history.py`, splitting the trailing burst at the last `unanswered`-marked message, and call it in `services/chat/src/chat/api/turn.py` after `split_into_bursts`; run T084 to green
- [X] T092 [US3] Rejoin consecutive same-role entries in `services/chat/src/chat/agent/history.py`'s `to_claude_messages`, so the split of T091 cannot produce two consecutive `user` entries; run T085 to green
- [X] T093 [US3] Add the two-way switch and its countdown to `services/frontend/src/components/StaffThread.tsx`, reading `assistant_may_reply` and `pause_seconds_remaining` from the poll; run T087 to green
- [X] T094 [US3] Add the FR-033 pause records to `services/chat/src/chat/api/console.py` per `contracts/log-events.md`: `assistant.paused` with `paused_by` (`staff_message` | `switch`) and `restarted`, and `assistant.resumed` with `resumed_by` (`expiry` | `switch`) — a staff message is **not** a resume, since it ends an escalation and starts a pause
- [X] T095 [US3] Write and run the switch-does-not-answer test in `services/chat/tests/test_attention_marks.py`: turning the assistant off and later on again leaves the emphasis and every mark untouched throughout — neither direction of the switch answers a patient (FR-029a, spec US3 scenario 5d)

**Checkpoint**: a staff member can lead a conversation without being interrupted, and can take or hand back one without writing a word.

---

## Phase 6: User Story 4 - Manage the practitioners the assistant books (Priority: P4)

**Goal**: Add, edit and delete practitioners from a screen, with every rule and every refusal coming
from the scheduling service that owns them, and without the browser ever holding the session
credential.

**Independent Test**: From the console, add a practitioner leaving every field defaulted, and verify
they appear when the assistant is asked which practitioners the clinic has. Edit their working hours
and verify the times the assistant offers change. Delete them and verify their appointments go too.

### Tests for User Story 4 (write first, confirm failing) ⚠️

- [X] T096 [P] [US4] Write the proxy tests in `services/chat/tests/test_practitioner_proxy.py`: each of the four routes forwards to the scheduler's `/practitioners` REST API with `X-Session-Id` taken from the **cookie** session; request and response bodies are relayed unchanged; and the scheduler's own status codes reach the caller — 409 for a duplicate name, 422 for overlapping ranges, 404 for another session's practitioner (FR-035, SC-013); observe failing
- [X] T097 [P] [US4] Write the transport-failure tests in `services/chat/tests/test_practitioner_proxy.py`: unreachable → 503 "nothing was changed"; timed out → 504 "may not have been applied — try again"; and **exactly one attempt is made** on every route, because a retried POST would create two practitioners and an unknown outcome must be reported as unknown rather than resolved (research #20); observe failing
- [X] T098 [P] [US4] Write the credential test in `services/chat/tests/test_practitioner_proxy.py` and `services/frontend/tests/PractitionerAdmin.test.tsx`: the session id appears in **no** response body and in nothing the page can read, and every practitioner request from the browser goes to the chat backend's own origin (FR-036, SC-012); observe failing
- [X] T099 [P] [US4] Write the screen tests in `services/frontend/tests/PractitionerAdmin.test.tsx`: a create with every field blank succeeds and shows back the pool-assigned name; a refusal renders its reason in plain language beside the field; and nothing is changed by a refused request; observe failing
- [X] T100 [P] [US4] Write the round trip in `tests/integration/test_practitioner_proxy_roundtrip.py`: a schedule edited through the console changes the times the assistant offers on the next availability question, and a delete takes the practitioner's appointments with it (FR-037, SC-014); observe failing

### Implementation for User Story 4

- [X] T101 [US4] Create `services/chat/src/chat/clients/scheduler_rest.py` — the HTTP proxy transport over the shared `aiohttp` session, one attempt, a 5-second timeout, no retry, mapping unreachable/timeout to 503/504 exactly as `services/chat/src/chat/api/chats.py` already does for a rename. This is the **only** module that speaks HTTP to the scheduler, as `clients/scheduling.py` is the only one that speaks gRPC
- [X] T102 [US4] Share the `aiohttp` session for the proxy in `services/chat/src/chat/main.py`, reusing the one already created for Voyage rather than opening a second pool
- [X] T103 [US4] Add the four `/console/practitioners` routes to `services/chat/src/chat/api/console.py`, relaying bodies and status codes unchanged and re-implementing **no** rule; run T096, T097 and T098 to green
- [X] T104 [US4] Create `services/frontend/src/components/PractitionerAdmin.tsx` and its fetch layer in `services/frontend/src/lib/consoleApi.ts`; run T099 to green
- [X] T105 [US4] Run T100 to green and confirm the assistant's roster, specialties and offered times follow a console edit on the next question

**Checkpoint**: the roster the assistant books against is editable from a screen, and every rule still lives in the service that owns it.

---

## Phase 7: User Story 5 - Manage what the assistant can answer from (Priority: P5)

**Goal**: Add, edit and delete FAQ entries from a screen and trust what it shows without
qualification — every entry listed is one the assistant can answer from, with the text shown, because
the write path makes any other outcome unrepresentable.

**Independent Test**: Add an entry, ask a question it answers, and verify the answer cites it. Edit
it and verify the cited text changes. Delete it and verify the assistant abstains on the same
question — and that the abstention hands the conversation to staff.

### Tests for User Story 5 (write first, confirm failing) ⚠️

- [X] T106 [P] [US5] Write the sequence tests in `services/chat/tests/test_faq_revisions.py`: a create reserves its id from the sequence, chunks and embeds **before either store is written**, writes chunks under a new revision, and publishes with one local commit that is the **only** moment the entry becomes visible to the console or to retrieval (FR-042a, FR-042c); observe failing
- [X] T107 [P] [US5] Write the three failure-point tests in `services/chat/tests/test_faq_revisions.py` — the story's core, run for create, update and delete alike. Failing at the embedding call, at the chunk write, and at the publishing commit each leaves the entry's content, its live revision and what the assistant answers from **exactly as they were**, reports a failure that can be retried, and performs **no** rollback, revert or compensating write (FR-042e, SC-015a); observe failing
- [X] T108 [P] [US5] Write the staleness-guard test in `services/chat/tests/test_faq_revisions.py`: the publishing `UPDATE` carries `AND live_revision = :expected` in its `WHERE`, with the expected value read **inside** the operation and never supplied by the caller; two saves racing on one entry write disjoint revisions, one commit wins, and the loser is reported as a failed save rather than publishing over it (FR-042c, spec Edge Cases); observe failing
- [X] T109 [P] [US5] Write the retry tests in `services/chat/tests/test_faq_revisions.py`: resubmitting each failed operation once its dependency recovers succeeds with the content last submitted, needs **no** manual repair of the index, and resubmitting one that already succeeded changes nothing, creates no duplicate and leaves the same single revision live (FR-042g, SC-015c); observe failing
- [X] T110 [P] [US5] Write the sweep tests in `services/chat/tests/test_faq_revisions.py`: the sweep deletes this **entry's** chunks whose revision is not the live one — a predicate covering a superseded revision and a never-published one alike; it is idempotent; a forced failure does **not** fail the operation, is **not** reported, and raises **no event of any kind**, expressly including `critical.dependency_unreachable` (FR-042h, SC-015d); observe failing
- [X] T111 [P] [US5] Write the never-widened test in `services/chat/tests/test_faq_revisions.py`: the sweep is scoped to one entry and is **not** a session-wide predicate, which would delete a concurrent save's chunks in the window between their write and the commit that publishes them (FR-042h); observe failing
- [X] T112 [P] [US5] Write the delete-ordering test in `services/chat/tests/test_faq_api.py`: the row is removed **first**, making the entry unanswerable at that instant; a failure to remove its chunks is **not** reported as a failed delete; and at no point is a deleted entry still citable (FR-042f); observe failing
- [X] T113 [P] [US5] Write the cap tests in `services/chat/tests/test_faq_api.py`: a create beyond `FAQ_MAX_ENTRIES_PER_SESSION` is refused with a message naming the reason, **before** chunking or embedding, and changes neither store; editing and deleting on a full corpus still succeed; deleting one makes room immediately; and nothing else a session accumulates is refused for count (FR-039f, FR-039g, SC-015e); observe failing
- [X] T114 [P] [US5] Write the isolation tests in `tests/integration/test_faq_session_isolation.py`: two sessions each build a corpus; one deletes, edits and adds; 100% of the other's answers and citations are unchanged, and zero of either session's chunks are retrieved by, cited by, or counted toward the groundedness of the other (SC-011a); observe failing
- [X] T115 [P] [US5] Write the empty-corpus test in `services/chat/tests/test_faq_api.py`: a new session's `GET /faq` returns `[]` plainly — not an error, not another session's entries (FR-039d); observe failing
- [X] T116 [P] [US5] Write the screen tests in `services/frontend/tests/FaqAdmin.test.tsx`: every listed entry renders with its text, an empty corpus renders as plainly empty, and **no per-entry retrievability state is rendered at all** — there is no second state for it to report, and a signal that can never fire teaches a staff member to rely on one that would not warn them (FR-040, SC-015); observe failing
- [X] T117 [P] [US5] Write the corpus-effect round trip in `tests/integration/test_faq_session_isolation.py`: after adding an entry, 100% of matching questions are answered with a citation to it; after editing it, the cited text is the **new** text; and after deleting it, the questions it alone supported end in **abstention** — which hands the conversation to staff rather than producing an unsupported answer (SC-016, US5 scenario 4, FR-003). This is the one task that proves the write path and the read path agree end to end; observe failing

### Implementation for User Story 5

- [X] T118 [US5] Add `reserve_id()` and the guarded `publish()` to `services/chat/src/chat/repositories/faq_repository.py` — `SELECT nextval('faq_entries_id_seq')` to separate allocating identity from publishing it, so a create's chunks can carry their entry id before the row exists (research #12) — and the publishing `UPDATE` with the staleness guard in its `WHERE`; run T108 to green
- [X] T119 [US5] Add `upsert_chunks(session_id, faq_entry_id, revision, ...)`, `sweep_entry(faq_entry_id, live_revision)` and `delete_by_session(session_id)` to `services/chat/src/chat/repositories/qdrant_repository.py` per data-model.md's three filters; run T110 and T111 to green
- [X] T120 [US5] Rewrite `services/chat/src/chat/rag/indexing.py` as `publish_revision()` and `sweep_entry()`: chunk and embed before either store is written, write chunks additively under a new revision, delete nothing, and swallow the sweep's failure **silently** — raising no event at all
- [X] T121 [US5] Rewrite `services/chat/src/chat/api/faq.py`'s four routes per `contracts/http-api.md`: the session predicate on every one, the cap check before any store is touched, the create and update sequences, and the reversed delete ordering; run T106, T107, T109, T112, T113 and T115 to green
- [X] T122 [US5] **Delete `_revert_faq_update`** from `services/chat/src/chat/api/faq.py`. Not repaired — deleted. A best-effort compensating write that half-succeeds and swallows its own failure is what left the two stores silently disagreeing, and under additive revisions there is nothing for it to compensate for (FR-042e)
- [X] T123 [US5] Add the FAQ records to `services/chat/src/chat/api/faq.py` per `contracts/log-events.md`: `session_id`/`revision` on `faq.entry_created`, both revisions on `faq.entry_updated`, plus the new `faq.publish_conflict` and `faq.create_refused`. **The sweep logs nothing**
- [X] T124 [US5] Create `services/frontend/src/components/FaqAdmin.tsx` and its fetch layer in `services/frontend/src/lib/consoleApi.ts`, rendering no retrievability state; run T116 to green
- [X] T125 [US5] Run T114 to green and confirm one session's corpus edits change nothing another session answers

**Checkpoint**: the corpus the assistant answers from is editable by hand, and a failed edit costs leaked storage rather than a working entry.

---

## Phase 8: Admin session deletion (cross-cutting, no user story)

**Purpose**: FR-046–FR-052. No user story depends on these, and the console never links to them —
they exist so FR-039c has a trigger and so a demonstration can be reset. They come after the stories
because nothing above needs them, and before Polish because FR-039c is a requirement.

### Tests (write first, confirm failing) ⚠️

- [X] T126 [P] Write the rpc tests in `services/scheduler/tests/test_delete_session.py`: `DeleteSession` removes the session's practitioners and patients in one transaction with their appointments following by cascade — **including cancelled ones**, since 006 left those cascades status-blind; it returns the three counts; it is **idempotent**, succeeding with zero counts on an absent session rather than returning `NOT_FOUND`; and it leaves every other session untouched; observe failing
- [X] T127 [P] Write the stub smoke assertions in `packages/shared-proto/tests/test_smoke.py`: `DeleteSessionRequest` and `DeleteSessionResponse` import, and `DeleteSession` is present in the service descriptor; observe failing
- [X] T128 [P] Write the guard tests in `services/chat/tests/test_admin_api.py` — four properties, each with a wrong default. The secret is read from the `X-Admin-Secret` **header** and never a query string or path segment; the comparison is constant-time; both routes are absent from `/openapi.json`; and a **configured** secret that is unset or empty refuses every request, checked before the comparison — since an empty configured secret would otherwise `compare_digest`-match an empty header and admit everyone (FR-048a, SC-019a); observe failing
- [X] T129 [P] Write the disclosure tests in `services/chat/tests/test_admin_api.py`: every refusal is the identical 403 body, never saying which part was wrong, and zero responses, logs or error messages contain the secret (FR-048, FR-050, SC-019); observe failing
- [X] T130 [P] Write the deletion round trip in `tests/integration/test_session_delete.py`: a session holding chats, messages, marks, FAQ entries, patients, practitioners and appointments leaves **nothing** behind in either store, its chunks are gone from the retrieval store, and no other session is affected (FR-047, SC-018); observe failing
- [X] T131 [P] Write the partial-outcome tests in `tests/integration/test_session_delete.py`: with one store made unreachable mid-deletion, the affected session is reported `"incomplete"` and **never** as success; re-running completes without error and leaves the same end state; and deleting **all** sessions offers exactly these guarantees applied to each (FR-051, FR-052, SC-020); observe failing
- [X] T132 [P] Write the chunk-leak test in `tests/integration/test_session_delete.py`: a failure to remove the session's chunks is **not** reported as an incomplete deletion — the rows that vouched for them are already gone, so they are unreachable, and reporting a leak as an incomplete delete would send an admin back to re-run something that already achieved every observable effect (research #23); observe failing

### Implementation

- [X] T133 Apply the delta in `specs/007-escalation-and-staff-console/contracts/scheduling.proto` to `packages/shared-proto/protos/scheduling/v1/scheduling.proto` — one rpc, two messages, nothing else changed
- [X] T134 Regenerate the stubs into `packages/shared-proto/src/shared_proto/scheduling/v1/` following `packages/shared-proto/README.md`, **including the manual import fixup**; run T127 to green
- [X] T135 [P] Add `delete_for_session()` to `services/scheduler/src/scheduler/repositories/practitioner_repository.py` and `services/scheduler/src/scheduler/repositories/patient_repository.py`, each carrying `session_id` on the `DELETE` itself
- [X] T136 Add `DeleteSession` to `services/scheduler/src/scheduler/grpc/servicer.py` and its counts to `services/scheduler/src/scheduler/grpc/converters.py`, in one transaction, with the `session.purged` record of `contracts/log-events.md`; run T126 to green
- [X] T137 Add `delete_session()` to `services/chat/src/chat/clients/scheduling.py` — still the only module importing `shared_proto`
- [X] T138 Create `services/chat/src/chat/api/admin.py` with the two routes per `contracts/http-api.md`, `include_in_schema=False` on the **decorators** (a router cannot retroactively hide its routes from `/openapi.json`), the fail-closed check before the constant-time comparison, and the per-session result shape; register the router in `services/chat/src/chat/main.py`; run T128 and T129 to green
- [X] T139 Implement the deletion sequence in `services/chat/src/chat/api/admin.py`: the scheduler first, then this service's session row (taking chats, messages, marks and FAQ entries by cascade), then that session's chunks by `session_id` — the ordering whose only failure mode is benign, since a crash between the steps leaves a session a re-run clears rather than stranding rows with nothing left to name them; add `session.deleted` and `session.delete_incomplete`; run T130, T131 and T132 to green
- [X] T140 Confirm `admin.refused` carries **only** `route` — not the supplied value, not its length, not which of the three refusal causes it was — in `services/chat/src/chat/api/admin.py`

**Checkpoint**: FR-039c has a trigger, and a demonstration can be reset without shell access.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: The documentation this change makes stale — three documents state things this feature
reverses — and full verification.

- [X] T141 [P] Rewrite the Postgres↔Qdrant ordering bullet in `.claude/CLAUDE.md`'s "Key design decisions to preserve": the delete-then-upsert ordering it describes is **superseded** by additive revisions. Its general principle — that the row is the sole authority on which indexed content is live — is what this feature finally implements, so the edit sharpens it rather than reversing it; add the console's routes to the service notes
- [X] T142 [P] Correct two claims in `docs/ROADMAP.md`'s Phase 1d part 2 bullet: "the only new backend is what the escalation path itself needs" is not so (FR-036's proxy and FR-047's rpc are two capabilities across the service boundary), and "the console surfaces indexing state" describes a screen this feature deliberately does not build, because that state no longer exists (FR-040)
- [X] T143 [P] Add a README section in the pattern the existing six follow, recording the three choices a reader would otherwise reverse-engineer from a migration: **additive chunk revisions** (why a save publishes rather than replaces, and that the trade is leaked storage rather than a lost answer), **session-scoped retrieval** (why a shared corpus stopped being tenable the moment a delete button existed), and **polling** (why the read model is stored state and what that buys over a push channel)
- [X] T144 [P] Confirm `docs/testing-strategy.md` needs no change — no tier is added and no harness convention changes; the frontend tier already existed and is simply used properly for the first time
- [ ] T145 Run the quickstart end to end: `specs/007-escalation-and-staff-console/quickstart.md` Scenarios 1–13, starting with the `\d chats` and `\d faq_entries` checks and the manual Qdrant drop. **NOT RUN** — it needs live Anthropic and Voyage keys, all three services up, and a browser, none of which this change can supply for itself. Every requirement it walks has automated cover in the three tiers (see T146); what it adds is the manual, visual half, and it is the one task still owed
- [X] T146 Run `make lint && make typecheck && make test` and confirm the whole suite — Python and frontend — is green
- [X] T147 Confirm the CI `test` job passes on the branch (`.github/workflows/ci.yml`) with no new services or fixtures required. **Correction**: true of the `test` job, and not of `integration` — that tier now drives chat's own stores (T114/T117/T130), so its job gained a Qdrant service and a `visitdoc_chat_test` database

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies — can start immediately
- **Foundational (Phase 2)**: depends on Setup — **BLOCKS all user stories**. Nothing here is optional: the reset is what makes two columns `NOT NULL`, the columns are what give every story its state, and the retrieval read path is what US1's abstention depends on
- **User Story 1 (Phase 3)**: depends on Foundational only. Independently shippable, and the MVP
- **User Story 2 (Phase 4)**: depends on US1 — it is the read model **over** US1's state, and there is nothing to list until escalation writes something. Its interim reliance on `GET /chats` is replaced in T078
- **User Story 3 (Phase 5)**: depends on US1's staff-message endpoint, which it completes with the pause and the cancellation. Independent of US2
- **User Story 4 (Phase 6)**: depends on **Foundational only** — nothing about practitioners touches escalation. Can be staffed in parallel with US2/US3 from the moment Phase 2 is green
- **User Story 5 (Phase 7)**: depends on Foundational's read path, and on US1 for the abstention→escalation half of its independent test. Independent of US2, US3 and US4
- **Phase 8 (admin deletion)**: depends on US5 for the session's FAQ entries and chunks to exist and be worth deleting; otherwise only on Foundational
- **Polish (Phase 9)**: depends on all desired stories being complete

### Within Each User Story

- Tests MUST be written and observed to FAIL before implementation (TDD, non-negotiable)
- Migration/schema → repository → service/agent → endpoint → frontend → records
- The **negative** assertions come first within US1, because they are the ones an implementation passes by accident: a gate placed one node too late produces no reply and still fails SC-002

### Parallel Opportunities

- T002–T004 run alongside T001
- Within Phase 2: T005, T007, T009 (three independent test files), then T011/T013 (migration tests), then T021/T023/T025 (the retrieval read path) — the two migrations themselves are sequential, since T014 deletes rows T012's columns sit on
- Within each story phase, **all** the test-writing tasks are marked [P] — they live in different files and none depends on another's implementation
- **US4 is the big parallel win**: it shares no file with US1, US2 or US3 and can start the moment Phase 2 is green
- Frontend and backend tasks within a story are largely parallel: they meet only at the contract, which `contracts/http-api.md` fixes before either is written

---

## Parallel Example: User Story 1

```bash
# Launch the whole test-writing front for US1 together (all different files):
Task: "Collector, precedence and transitions in services/chat/tests/test_escalation.py"
Task: "The silence gate's absences in services/chat/tests/test_silence_gate.py"
Task: "Abstention escalates, and FR-006's turn completion, in services/chat/tests/test_turn_api.py"
Task: "Failure-vs-refusal mapping in services/chat/tests/test_handle_booking.py"
Task: "Staff message effects in services/chat/tests/test_staff_messages.py"
Task: "Third sender through history in services/chat/tests/test_history.py"
Task: "The silent terminal event in services/frontend/tests/chatStream.test.ts"
Task: "The two role labels in services/frontend/tests/MessageView.test.tsx"
Task: "Staff thread and composer in services/frontend/tests/StaffThread.test.tsx"
Task: "Two panes, no login, in services/frontend/tests/App.test.tsx"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational — **critical**, destructive, and the largest single block
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: run quickstart Scenario 1 end to end, including the log check that proves nothing ran in a silent conversation
5. A clinic whose assistant can hand a conversation to a person who actually replies is already more useful than one that cannot — deploy/demo if ready

### Incremental Delivery

1. Setup + Foundational → the stores are reset and both axes exist
2. Add US1 → escalate, silence, reply as staff → validate → demo (**MVP**)
3. Add US2 → emphasis, ordering, the total, live arrival → validate → demo
4. Add US3 → the pause, the two-way switch, no retroactive answering → validate → demo
5. Add US4 → practitioner management (can land any time after Phase 2)
6. Add US5 → the corpus, and the write path that cannot lose an answer
7. Phase 8 → the reset path; Phase 9 → docs, quickstart, full suite

### Parallel Team Strategy

Phase 2 is a genuine bottleneck and is best done by one person in one pass — it is two migrations, a
destructive reset across three stores, and one vocabulary, and splitting it invites half a reset
landing separately. After that:

- Developer A: US1 (P1), then US2 (P2) — they are one call path and one state machine
- Developer B: US4 (P4) immediately, then US5 (P5) — neither touches escalation
- Developer C: US3 (P3) once US1's staff-message endpoint exists, then Phase 8

---

## Notes

- [P] tasks = different files, no dependencies
- Every implementation task names the exact file it lands in. Five backend files are **new**
  (`agent/escalation.py`, `agent/tools/staff_tools.py`, `api/console.py`, `api/admin.py`,
  `clients/scheduler_rest.py`), plus six on the frontend; one is **rewritten rather than extended**
  (`api/faq.py`); and one function is **deleted** (`_revert_faq_update`)
- The rules whose violation is invisible in an ordinary green suite are: the silence gate placed
  after classification (T034 is the only thing that catches it), the two conversation-level axes
  collapsed into one (T065 and T066), and the retrieval predicate written as a post-filter rather
  than a search term (T021, T114). Do not skip them, and do not "simplify" any of the three
- `api/admin.py` must never be reachable from `api/console.py`'s router. They are separate modules
  precisely so a maintenance surface cannot drift into a published one
- Commit after each task or logical group; stop at any checkpoint to validate a story independently

---

## Phase 10: Convergence

- [X] T148 **CRITICAL** — Reconcile the mixed-intent rule: `_select_specialists` in `services/chat/src/chat/agent/graph.py` routes a message carrying `call_staff` to `hand_off` alone, suppressing every other label, while `spec.md`'s first Edge Case states that such a turn finishes and "the FAQ half is answered as it would be otherwise". `contracts/agent-tools.md` was updated to the new rule and `spec.md` was not, so the two disagree — which Constitution VI forbids ("documentation is updated in the same change that makes it stale"). Decide which is intended, then either amend the Edge Case through `/speckit-specify` or restore the suppressed half, and make `tests/test_graph.py::test_call_staff_suppresses_a_booking_on_the_same_message` state whichever rule survives per spec Edge Cases / Constitution VI (contradicts)
- [X] T149 Write the end-to-end half of FR-019b in `services/chat/tests/test_turn_api.py`: with an `unanswered`-marked patient message already in the thread and the assistant free to speak again, one turn answers **only** the message sent after the silence, the marked messages remain in the history as context, and `turn.message_received`'s `message_ids_unified` holds exactly one id. `history.exclude_silent_window` is unit-tested and wired into `api/turn.py`, but no test drives a real turn through it, and `message_ids_unified` is asserted nowhere in the suite per FR-019a, FR-019b, SC-009b (partial). **Found a real defect, now fixed**: `answer_faq` read its question off `to_claude_messages`'s last entry, which rejoins the two consecutive patient-sided bursts the exclusion creates - so the silenced message was still being retrieved for and answered. The question now comes from `history.trailing_question(bursts)`, and the held-back messages are rendered into the prompt by `history.render_silent_window` so they stay in front of the model as context - FR-019a asks for both halves, and separating the question alone had dropped them from the model's input entirely. `handle_booking`, which has no question field of its own, rewrites the same rejoined entry the same way
- [X] T150 Exercise the silence gate's **pause** branch in `services/chat/tests/test_silence_gate.py`: every test there reaches the gate through an escalation, so `_silenced_by`'s `"pause"` return in `services/chat/src/chat/api/turn.py` and `message.unanswered`'s `silenced_by: "pause"` are never run. Assert the same absences the escalated case asserts, plus that the message is kept, marked `unanswered`, and emphasizes its conversation per FR-015, SC-003 (partial)
- [X] T151 Assert in `services/chat/tests/test_assistant_switch.py` that a pause expiring on its own clears **no** mark and **no** emphasis — SC-008 names it among the things that must clear neither, and `test_the_pause_lifts_by_itself_with_no_staff_action` checks only the state columns per SC-008 (partial)

---

## Phase 11: Convergence

- [X] T152 Record the fourth `answer_source` in `specs/007-escalation-and-staff-console/contracts/http-api.md`: 005's `contracts/chat-api.yaml` publishes the terminal event as `"answer_source":"faq"|"booking"|"merged"`, a closed set of three, and the code now emits `hand_off` for a turn that fetched a person and produced no answer. The "Terminal events — one added" table records the new `silent` type and nothing about this, so a reader following the published contract would treat `hand_off` as invalid. Note alongside it that such a turn carries `grounded: null` and no citations, for the same reason a booking reply does — it was never retrieved against per 005 contracts/chat-api.yaml, Constitution VI (contradicts)
- [X] T153 Record `turn.completed`'s `outcome: "handed_off"` in `specs/007-escalation-and-staff-console/contracts/log-events.md`, beside the escalation events it already documents. `compose_answer` derives it from the answer source rather than from an absent groundedness verdict — which is what stops every handed-off turn being filed in the log as a booking — and that reasoning belongs with the event per FR-033, Constitution VI (partial)
