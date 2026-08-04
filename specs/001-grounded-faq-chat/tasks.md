---

description: "Task list for Grounded FAQ Chat (Phase 0 Walking Skeleton)"
---

# Tasks: Grounded FAQ Chat (Phase 0 Walking Skeleton)

**Input**: Design documents from `/specs/001-grounded-faq-chat/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/openapi.yaml, quickstart.md (all present)

**Tests**: Mandatory per Constitution Principle VIII (Test-Driven Development, NON-NEGOTIABLE) — every
task with testable behavior has a corresponding test task before it, written first and confirmed
failing before implementation. This includes Foundational-phase infrastructure (session factory,
models, migrations, repositories, indexing orchestration, app skeleton) — each gets a minimal
preceding test, not just the two business-rule modules (validation, chunking) that had them in the
first draft of this file (`/speckit-analyze` finding D1).

**Organization**: Tasks are grouped by user story (spec.md) so each story is independently
implementable and testable. Both User Story 1 and User Story 2 depend only on the Foundational
phase, not on each other — see "User Story Dependencies" below for the one nuance this creates.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: `US1` or `US2` — maps to spec.md's user stories. Setup, Foundational, and Polish
  tasks carry no story label.
- Every task names an exact file path.

## Path Conventions (this repo, per plan.md — NOT the generic single-project layout)

- Backend: `services/chat/src/chat/...` (uv workspace member `chat`); unit/contract tests are
  **colocated and flat** at `services/chat/tests/test_*.py` (`docs/testing-strategy.md` — no
  `tests/contract/` or `tests/unit/` subfolders in this repo).
- Frontend: `services/frontend/src/...`; tests at `services/frontend/tests/*.test.{ts,tsx}`
  (Vitest + React Testing Library).
- `tests/integration/` and `tests/e2e/` (repo root) stay untouched placeholders — this feature has
  no cross-service boundary (plan.md Structure Decision).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Get both workspace members to the point where Foundational work can start.

- [X] T001 Add backend dependencies to `services/chat` — `fastapi`, `uvicorn[standard]`,
      `sqlalchemy[asyncio]`, `alembic`, `asyncpg`, `psycopg[binary]`, `anthropic`, `qdrant-client`,
      `voyageai`, `pydantic-settings` — via `uv add --package chat <dep>` for each (updates
      `services/chat/pyproject.toml` and root `uv.lock`)
- [X] T002 [P] Scaffold `services/frontend` as a Vite + React + TypeScript project: `package.json`,
      `vite.config.ts`, `tsconfig.json`, `index.html`, `services/frontend/src/main.tsx` placeholder
- [X] T003 Add frontend test tooling (`vitest`, `@testing-library/react`,
      `@testing-library/jest-dom`, `jsdom`) to `services/frontend/package.json` and create
      `services/frontend/vitest.config.ts` (depends on T002 — same file; not marked `[P]`, per
      `/speckit-analyze` finding F1)
- [X] T004 Initialize Alembic scaffolding in `services/chat/alembic/` — `alembic.ini`,
      `services/chat/alembic/env.py` configured for a **sync `psycopg`** engine reading
      `DATABASE_URL` (research.md #2) (depends on T001; not marked `[P]`, per F1)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared data models, repositories, and RAG plumbing that both user stories build on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T005 [P] Create `Settings` (`DATABASE_URL`, `QDRANT_URL`, `ANTHROPIC_API_KEY`,
      `VOYAGE_API_KEY`) via `pydantic-settings` in `services/chat/src/chat/core/config.py`
- [X] T006 [P] Minimal unit test for the async engine/session factory — asserts it builds an
      `AsyncSession` bound to `Settings.DATABASE_URL` — in `services/chat/tests/test_db_session.py`
- [X] T007 Create the async engine/session factory (`asyncpg`) in
      `services/chat/src/chat/db/session.py` — confirm T006 fails first, then implement until it
      passes (depends on T005, T006)
- [X] T008 [P] Minimal unit test for the `FaqEntry` SQLAlchemy model — asserts table name
      `faq_entries`, `id` is an integer PK, and there is no `title` column (data-model.md) — in
      `services/chat/tests/test_models.py`
- [X] T009 Create the `FaqEntry` SQLAlchemy 2.0 declarative model (`id` int PK/IDENTITY, `content`
      str, `created_at`, `updated_at` — data-model.md, no `title` field) in
      `services/chat/src/chat/domain/models.py` — confirm T008 fails first, then implement until it
      passes
- [X] T010 [P] Minimal test that `alembic upgrade head` creates the `faq_entries` table with the
      expected columns, run against a real test database, in
      `services/chat/tests/test_migrations.py`
- [X] T011 Generate and commit the Alembic migration creating the `faq_entries` table in
      `services/chat/alembic/versions/` — confirm T010 fails first, then implement until it passes
      (depends on T004, T009, T010)
- [X] T012 [P] Unit test for the shared "meaningless content" check (FR-009 cases: whitespace-only,
      dash-only, bare `Question:`/`Answer:` labels with nothing after them, and valid short content
      that must pass) in `services/chat/tests/test_validation.py`
- [X] T013 Implement the shared meaningless-content check in
      `services/chat/src/chat/domain/validation.py` (research.md #14) — confirm T012 fails first,
      then implement until it passes
- [X] T014 [P] Unit test for chunking + degenerate-chunk filtering — fixed-size chunking (~1,000
      chars, ~150-char overlap, research.md #3), returned as a typed `ChunkedText(chunk_index,
      chunk_text)` sequence (not raw tuples/dicts — `/speckit-analyze` finding C3), and the FR-017
      invariant that at least one chunk always survives filtering when the source entry passed
      FR-009 — in `services/chat/tests/test_chunking.py`
- [X] T015 Implement chunking in `services/chat/src/chat/rag/chunking.py`: define a small frozen
      `ChunkedText` dataclass (`chunk_index: int`, `chunk_text: str` — the pre-embedding, pre-entry-
      association shape of a `FaqChunk`, data-model.md) and have the chunker return `list[
      ChunkedText]`, using `domain/validation.py` (T013) to drop degenerate chunks — confirm T014
      fails first, then implement until it passes
- [X] T016 [P] Minimal unit test for the Voyage AI embeddings wrapper — `embed_texts` calls the
      (mocked) Voyage client and returns one vector per input string, no real network call — in
      `services/chat/tests/test_embeddings.py`
- [X] T017 Implement the Voyage AI embeddings client wrapper (`embed_texts`) in
      `services/chat/src/chat/rag/embeddings.py` (research.md #1) — confirm T016 fails first, then
      implement until it passes
- [X] T018 [P] Minimal test for the Qdrant repository and `faq_chunks` collection bootstrap —
      bootstrap is idempotent (safe to call twice), and `upsert_chunks`/`search`/`delete_by_entry`
      round-trip correctly against a real local Qdrant — in
      `services/chat/tests/test_qdrant_repository.py`
- [X] T019 Implement the Qdrant repository (`upsert_chunks(faq_entry_id: int, chunks:
      list[ChunkedText], vectors: list[list[float]])`, `search`, `delete_by_entry`) and
      `faq_chunks` collection bootstrap in
      `services/chat/src/chat/repositories/qdrant_repository.py` — `upsert_chunks` builds each
      Qdrant point's payload (`faq_entry_id`, `chunk_index`, `chunk_text`) from the `ChunkedText`
      values (T015) paired with their embedded vectors — confirm T018 fails first, then implement
      until it passes (depends on T005, T015, T017 for vector size)
- [X] T020 [P] Minimal unit test for the Postgres `FaqEntry` repository — `create`/`get`/`list`/
      `update`/`delete` round-trip against a real test database — in
      `services/chat/tests/test_faq_repository.py`
- [X] T021 Implement the Postgres `FaqEntry` repository (`create`, `get`, `list`, `update`,
      `delete`) in `services/chat/src/chat/repositories/faq_repository.py` — confirm T020 fails
      first, then implement until it passes (depends on T007, T009, T020; not marked `[P]`, per F1)
- [X] T022 [P] Minimal unit test for indexing orchestration — `index_faq_entry` calls chunk (getting
      back `list[ChunkedText]`) → embed each chunk's `.chunk_text` → upsert the `ChunkedText`/vector
      pairs, in that order; `deindex_faq_entry` calls delete-by-entry; chunking/embeddings/Qdrant
      repository dependencies mocked so this test stays isolated and fast — in
      `services/chat/tests/test_indexing.py`
- [X] T023 Implement indexing orchestration — `index_faq_entry` (chunk → embed each `ChunkedText`'s
      text → upsert) and `deindex_faq_entry` (delete) — in `services/chat/src/chat/rag/indexing.py`
      — confirm T022 fails first, then implement until it passes (depends on T015, T017, T019)
- [X] T024 [P] Minimal test that the FastAPI app builds without error, its lifespan hook ensures the
      Qdrant collection exists, and router registration points for `chat`/`faq` are present — in
      `services/chat/tests/test_main.py`
- [X] T025 Create the FastAPI app skeleton in `services/chat/src/chat/main.py`: lifespan hook that
      ensures the Qdrant collection exists (T019), router registration points for `chat` and `faq`
      — confirm T024 fails first, then implement until it passes (depends on T005, T019, T024)
- [X] T026 [P] Configure the Vite dev server proxy (`/chat`, `/faq` → `http://localhost:8000`) in
      `services/frontend/vite.config.ts`

**Checkpoint**: Foundation ready — both user stories can now start.

---

## Phase 3: User Story 1 - Ask a question and get a grounded answer (Priority: P1) 🎯 MVP

**Goal**: A visitor asks a free-text question and gets a grounded, cited, streamed answer — or an
explicit abstention when nothing relevant is found.

**Independent Test**: Seed one `FaqEntry` directly via `faq_repository`/`rag.indexing` (T021, T023
— bypassing the `/faq` API, which is User Story 2's job), `POST /chat` with a matching question,
confirm a streamed grounded answer citing the seeded chunk; `POST /chat` with an unrelated question,
confirm abstention.

### Tests for User Story 1 (write first, confirm failing) ⚠️

- [X] T027 [P] [US1] Contract/behavior tests for `POST /chat` — grounded answer with citations
      (fixture-seeded entry), abstention on no relevant content, and message-length validation
      (empty / >2000 chars, FR-001a) — in `services/chat/tests/test_chat_api.py`
- [X] T028 [P] [US1] Unit test for the groundedness similarity-threshold gate (above threshold →
      proceeds to generation; below → abstains without calling Claude) in
      `services/chat/tests/test_groundedness.py`
- [X] T029 [P] [US1] Test for the NDJSON stream parser (token events accumulate into text, terminal
      `done` event carries `grounded`/`citations`) in `services/frontend/tests/chatStream.test.ts`
- [X] T030 [P] [US1] Test for `ChatWindow` rendering streamed tokens, citations, and the abstention
      message in `services/frontend/tests/ChatWindow.test.tsx`

### Implementation for User Story 1

- [X] T031 [P] [US1] Add `ChatRequest`, `ChatTokenEvent`, `ChatDoneEvent`, `Citation` Pydantic
      schemas (per contracts/openapi.yaml) to `services/chat/src/chat/domain/schemas.py`
- [X] T032 [P] [US1] Implement `search_faq` retriever (embed query via T017, search via T019) in
      `services/chat/src/chat/rag/retriever.py`
- [X] T033 [US1] Implement the groundedness threshold gate in
      `services/chat/src/chat/rag/groundedness.py` — confirm T028 fails first, then implement until
      it passes (depends on T032)
- [X] T034 [US1] Implement `answer_faq` orchestration (retrieve → gate → call Claude directly, no
      agent framework, research.md #9 → stream tokens + citations) in
      `services/chat/src/chat/agent/answer_faq.py` (depends on T032, T033)
- [X] T035 [US1] Implement `POST /chat` (NDJSON `StreamingResponse`) in
      `services/chat/src/chat/api/chat.py` and register its router in `main.py` — confirm T027
      fails first, then implement until it passes (depends on T025, T031, T034)
- [X] T036 [P] [US1] Implement the `fetch` + `ReadableStream` NDJSON parser in
      `services/frontend/src/lib/chatStream.ts` — confirm T029 fails first, then implement until it
      passes
- [X] T037 [P] [US1] Implement the `ChatWindow` component (input box, streamed answer, citations,
      abstention state) in `services/frontend/src/components/ChatWindow.tsx` — confirm T030 fails
      first, then implement until it passes
- [X] T038 [US1] Wire `ChatWindow` into `services/frontend/src/App.tsx` and
      `services/frontend/src/main.tsx` (depends on T036, T037)

**Checkpoint**: User Story 1 is fully functional and independently testable/demoable (using
fixture-seeded data — the real content-authoring path is User Story 2).

---

## Phase 4: User Story 2 - Add, update, and delete FAQ content via API (Priority: P2)

**Goal**: A staff member can create, list, read, update, and delete FAQ entries via an open API;
changes are reflected in chat retrieval with no manual re-indexing step.

**Independent Test**: `POST /faq` to create an entry, confirm it via `GET /faq` and
`GET /faq/{id}`, `PUT /faq/{id}` with new content and confirm the change, `DELETE /faq/{id}` and
confirm both a subsequent `GET` and a repeat `DELETE` return 404 — all independent of the chat flow.

### Tests for User Story 2 (write first, confirm failing) ⚠️

- [X] T039 [P] [US2] Contract/behavior tests for `POST /faq`, `GET /faq`, `GET /faq/{id}`,
      `PUT /faq/{id}`, `DELETE /faq/{id}` — create, list, get-404, update-reflected-in-retrieval
      (a follow-up `/chat` call returns the updated content), delete-reflected-in-retrieval (a
      follow-up `/chat` call for the same question now returns `grounded: false` instead of citing
      the deleted entry — spec.md SC-006/FR-016, quickstart.md Scenario 6), delete-then-404 on both
      a subsequent `GET` and a repeat `DELETE`, and validation rejections (missing / >20,000 chars /
      whitespace-dash-only / label-only content) — in `services/chat/tests/test_faq_api.py`

### Implementation for User Story 2

- [X] T040 [P] [US2] Add `FaqEntryWrite`/`FaqEntry` schemas (per contracts/openapi.yaml — `content`
      required, 1–20,000 characters, FR-015) to `services/chat/src/chat/domain/schemas.py`, wiring
      `content`'s field validator to `domain/validation.py`'s meaninglessness check (T013)
- [X] T041 [US2] Implement `POST /faq` (create → `faq_repository.create` + `rag.indexing.
      index_faq_entry`) and `GET /faq` (list) in `services/chat/src/chat/api/faq.py` — confirm the
      relevant T039 cases fail first, then implement until they pass (depends on T021, T023, T040)
- [X] T042 [US2] Implement `GET /faq/{id}` and `PUT /faq/{id}` (update → `faq_repository.update` +
      re-index via `rag.indexing.index_faq_entry`, 404 on unknown id) in
      `services/chat/src/chat/api/faq.py` (depends on T041)
- [X] T043 [US2] Implement `DELETE /faq/{id}` — `rag.indexing.deindex_faq_entry` **before**
      `faq_repository.delete` (data-model.md ordering, so a partial failure never leaves orphaned
      retrievable chunks), 404 on unknown id — in `services/chat/src/chat/api/faq.py` (depends on
      T042)
- [X] T044 [US2] Register the `faq` router in `services/chat/src/chat/main.py` (depends on T041)

**Checkpoint**: User Stories 1 and 2 both work independently — and together they satisfy spec.md's
full SC-005 demo loop through the real API, not just fixtures.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Documentation and validation that spans both stories.

- [X] T045 [P] Document this feature's technology choices (embedding provider, migration/app driver
      split, streaming transport, chunking, groundedness gate — research.md) in the README, per
      Constitution Principle VI
- [X] T046 [P] Run `make lint` and `make typecheck` across the new `services/chat` source and fix
      any findings
- [X] T047 Manually run all 6 `quickstart.md` scenarios end to end against `make db-up` +
      `make run-chat` + the frontend dev server; confirm every "Expected" result (depends on all
      prior phases)
- [X] T048 [P] Confirm `tests/integration/` and `tests/e2e/` remain untouched placeholders — this
      feature introduces no cross-service surface (plan.md Structure Decision)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS both user stories.
- **User Story 1 (Phase 3)** and **User Story 2 (Phase 4)**: Both depend only on Foundational
  completing, not on each other (see nuance below). Can proceed in priority order (P1 → P2) or in
  parallel if staffed.
- **Polish (Phase 5)**: T047 (full quickstart validation) depends on both user stories being done;
  T045/T046/T048 can start as soon as their relevant code exists.

### User Story Dependencies

- **User Story 1 (P1)**: Depends only on Foundational. Its tests seed data directly through
  `faq_repository`/`rag.indexing` (Foundational), so it does **not** structurally depend on User
  Story 2's HTTP endpoints — genuinely independently testable, per spec.md's Independent Test.
- **User Story 2 (P2)**: Depends only on Foundational. Independently testable via its own CRUD
  cycle; the "update/delete reflected in chat" scenarios call `/chat` as a black box, not any
  User Story 1 source file.
- **The nuance**: spec.md's SC-005 (the full walking-skeleton demo: add via API → ask in chat → get
  a cited answer) requires **both** stories — User Story 1 alone can only be demonstrated with
  fixture-seeded data, not through a human adding real content. See Implementation Strategy below.

### Within Each User Story

- Tests written and observed to fail before implementation (Constitution Principle VIII,
  NON-NEGOTIABLE) — including Foundational-phase infrastructure, not just business-rule modules.
- Schemas/models before services; services before endpoints; backend before frontend wiring.
- Story complete (checkpoint) before moving to the next priority, if working sequentially.

### Parallel Opportunities

- T001 and T002 (backend deps / frontend scaffold) can start together.
- All Foundational tasks marked `[P]` — T005, T006, T008, T010, T012, T014, T016, T018, T020, T022,
  T024, T026 — can run in parallel once their own direct prerequisite, if any, is met. Their paired
  implementation tasks (T007, T009, T011, T013, T015, T017, T019, T021, T023, T025) are
  deliberately **not** marked `[P]`, since each has an explicit same-phase dependency (its own test,
  plus whatever it's built on) — see F1 in the `/speckit-analyze` report this revision addresses.
- Once Foundational is done, all of User Story 1's and User Story 2's test tasks can be written in
  parallel (different files, no cross-dependency).
- User Story 1 and User Story 2 implementation can proceed in parallel by two developers once
  Foundational is complete — they touch disjoint files except `domain/schemas.py`, where each adds
  its own schemas without conflicting.

---

## Parallel Example: User Story 1

```bash
# Tests (write together, confirm all fail):
Task: "Contract/behavior tests for POST /chat in services/chat/tests/test_chat_api.py"
Task: "Unit test for the groundedness gate in services/chat/tests/test_groundedness.py"
Task: "Test for the NDJSON stream parser in services/frontend/tests/chatStream.test.ts"
Task: "Test for ChatWindow in services/frontend/tests/ChatWindow.test.tsx"

# Independent implementation pieces (after their tests fail):
Task: "Add Chat* schemas to services/chat/src/chat/domain/schemas.py"
Task: "Implement search_faq retriever in services/chat/src/chat/rag/retriever.py"
Task: "Implement chatStream.ts NDJSON parser in services/frontend/src/lib/chatStream.ts"
Task: "Implement ChatWindow.tsx in services/frontend/src/components/ChatWindow.tsx"
```

## Parallel Example: User Story 2

```bash
Task: "Contract/behavior tests for /faq endpoints in services/chat/tests/test_faq_api.py"
Task: "Add FaqEntryWrite/FaqEntry schemas to services/chat/src/chat/domain/schemas.py"
```

---

## Implementation Strategy

### Suggested MVP scope

User Story 1 (P1) is the technically central, hardest deliverable (retrieval, groundedness gate,
grounded generation, streaming) and is fully testable in isolation once Foundational is done, using
fixture-seeded data. **However**, spec.md's SC-005 — the actual walking-skeleton demo — needs User
Story 2 as well, since there is no way to add real content without its API. Recommended order:

1. Complete Phase 1 (Setup) + Phase 2 (Foundational).
2. Complete Phase 3 (User Story 1) — validate with fixture-seeded data; this proves the hard,
   AI-core part works (Constitution Principle II).
3. Complete Phase 4 (User Story 2) — this unlocks the real, demoable end-to-end loop.
4. Complete Phase 5 (Polish), including the full `quickstart.md` run-through.

### Incremental Delivery

1. Setup + Foundational → foundation ready, nothing user-visible yet.
2. + User Story 1 → the AI core works, provable via tests/fixtures.
3. + User Story 2 → SC-005's full demo works end to end.
4. + Polish → documented, linted, type-checked, manually validated.

### Parallel Team Strategy

With two developers: both do Setup + Foundational together, then split — one on User Story 1
(retrieval/generation/streaming/frontend), one on User Story 2 (CRUD endpoints) — reconverging at
Phase 5 for the full quickstart validation.

---

## Notes

- [P] tasks = different files, no dependency on an incomplete task.
- [US1]/[US2] labels map every user-story-phase task back to spec.md for traceability.
- Tests must be written and observed to fail before their implementation task (NON-NEGOTIABLE).
- Commit after each task or logical group.
- Stop at either story's checkpoint to validate it independently before continuing.
