---

description: "Task list for the reranked retrieval pipeline (Phase 1e)"
---

# Tasks: Reranked Retrieval Pipeline (Phase 1e)

**Input**: Design documents from `/specs/008-reranked-retrieval-pipeline/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: Per constitution principle VIII (NON-NEGOTIABLE), every test task precedes its
implementation task and MUST be observed failing first. This holds for *changed* behavior as well as
new: where an existing suite encodes the old contract, updating it to the new one is itself the
failing test, and it comes first. No task bundles a test with the implementation it covers.

**Organization**: Grouped by user story. US1 and US2 are both P1 and share Phase 2's gate functions;
they are separated because they fail differently — US1 is about what an answer stands on, US2 about
refusing to answer at all.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable — different files, no dependency on an incomplete task
- Paths are repo-relative from `/home/andrey/visit-doc`

## Running tests while you work

`services/chat/tests` is ~6 minutes and hits real Postgres and Qdrant. Per
`docs/testing-strategy.md`: iterate with scoped runs (`uv run pytest services/chat/tests/test_pipeline.py -q`),
run the full tier **once** at the end, and never start a second database-backed tier alongside it.

---

## Phase 1: Setup

**Purpose**: configuration and the test-safety net, before any code can reach a paid API.

- [X] T001 Add the seven pipeline settings to `services/chat/src/chat/core/config.py`: `RETRIEVAL_POOL_SIZE=25`, `SIMILARITY_FLOOR=0.3`, `SIMILARITY_CAP=5`, `RERANK_FLOOR=0.4`, `RERANK_CAP=3`, `RERANK_TIMEOUT_SECONDS=5.0`, `RERANK_MODEL="rerank-3"`, each commented in the style the existing settings use (why the value, not what it is) — see [data-model.md §4](./data-model.md)
- [X] T002 [P] Add a test to `services/chat/tests/test_config.py` asserting all seven settings exist with their documented defaults, so a rename or a dropped default fails here rather than at runtime
- [X] T003 Add a failing case to `services/chat/tests/test_paid_api_guard.py` proving a real `voyageai.client_async.AsyncClient.rerank` call raises `PaidAPICallInTestError`, mirroring the existing `embed` case
- [X] T004 Make T003 pass by adding `"voyageai.client_async.AsyncClient.rerank": "Voyage rerank (reranking)"` to `_PAID_API_CALLS` in `services/chat/tests/conftest.py`

**Checkpoint**: no reranking code exists yet, but an unmocked reranking call already fails loudly. T003/T004 must land before any task in Phase 3 — a mocking slip after this point costs a test failure instead of a live bill.

---

## Phase 2: Foundational (blocking — every story depends on this)

**Purpose**: the domain types, the pure gate functions, the schema change, and the widened search. No user story can start until this phase is complete.

### Domain types

- [X] T005 Add the `FaqVerdict` str enum with its five values to `services/chat/src/chat/domain/schemas.py` per [data-model.md §2](./data-model.md)
- [X] T006 [P] Add the `ScoredChunk` frozen dataclass (`faq_entry_id`, `chunk_index`, `chunk_text`, `similarity_score`, `rerank_score: float | None`) to a new `services/chat/src/chat/rag/pipeline.py`
- [X] T007 [P] Add the `PipelineOutcome` frozen dataclass (`verdict`, `survivors`, `considered`, `observed`) to `services/chat/src/chat/rag/pipeline.py`

### The gates — tests first

- [X] T008 Write `services/chat/tests/test_pipeline.py` covering `apply_similarity_gate`: floor inclusive at the boundary, floor applied per chunk, cap applied after floor, descending score order, empty input returns empty, never raises — per [contracts/pipeline-gates.md](./contracts/pipeline-gates.md). Observe them fail.
- [X] T009 [P] Extend `services/chat/tests/test_pipeline.py` with `apply_rerank_gate` cases: floor inclusive, cap after floor, **reranked order wins over similarity order**, empty input returns empty. Observe them fail.
- [X] T010 Extend `services/chat/tests/test_pipeline.py` with all five rows of `decide()`'s decision table, plus the `None`-vs-`[]` distinction for `reranked` (no scores obtained vs scored-and-rejected). Observe them fail.
- [X] T011 Extend `services/chat/tests/test_pipeline.py` with **one test per invariant** from [contracts/pipeline-gates.md](./contracts/pipeline-gates.md): survivors non-empty iff answered; each cap respected per verdict; `survivors ⊆ considered ⊆ observed`; `rerank_score` present on every `answered` survivor and `None` on every `answered_unreranked` survivor. Observe them fail.
- [X] T012 Implement `apply_similarity_gate`, `apply_rerank_gate` and `decide` in `services/chat/src/chat/rag/pipeline.py` until T008–T011 pass. Pure functions only — no client, no clock, no I/O.

### Retrieval widening — tests first

- [X] T013 Write a failing test in `services/chat/tests/test_qdrant_repository.py` that `search()` honours a `limit` larger than 5 and returns candidates in descending score order
- [X] T014 Write a failing test in a new `services/chat/tests/test_retriever.py` that `search_faq` returns the pool **whole**, including sub-floor candidates, as `ScoredChunk`s — the gate, not the search, is what drops them
- [X] T015 Change `services/chat/src/chat/rag/retriever.py` so `search_faq` fetches `RETRIEVAL_POOL_SIZE` candidates and returns the whole pool unfiltered, lifting each `RetrievedChunk` into a `ScoredChunk`, until T013–T014 pass. Do **not** add a Qdrant `score_threshold` — [research.md §4](./research.md) explains why the floor must stay in application code.

### Schema and persistence — test first

- [X] T016 Add a failing case to `services/chat/tests/test_migrations.py` asserting the head revision leaves `messages` with `faq_verdict` and without `grounded`, in both directions
- [X] T017 Replace `grounded: Mapped[bool | None]` with `faq_verdict` (native PG enum, nullable) in `services/chat/src/chat/domain/models.py`
- [X] T018 Generate the Alembic migration in `services/chat/alembic/versions/`: create the enum type, add `faq_verdict` nullable, drop `grounded`. **No backfill** (FR-024). Its docstring MUST state that it discards `grounded` and is correct only against an empty `messages` table (FR-024a). Until T016 passes.

### Removing what this replaces

- [X] T019 Delete `services/chat/src/chat/rag/groundedness.py` and `services/chat/tests/test_groundedness.py`. A superseded gate left in the tree is a second gate someone will call.

**Checkpoint**: `uv run pytest services/chat/tests/test_pipeline.py services/chat/tests/test_config.py -q` green; `-k groundedness` collects nothing. Every gate decision in this feature is now proven without a single network call.

---

## Phase 3: US1 — An answer stands only on chunks that answer the question (P1) 🎯 MVP

**Goal**: reranking runs, and the prompt context and citations are exactly its survivors.

**Independent test**: with a corpus where five chunks clear the floor but only two are on-topic, the answer cites exactly those two, at most three citations appear, and a chunk the reranker rejected is in neither the context nor the citations.

### The reranking port — tests first

- [X] T020 [US1] Write `services/chat/tests/test_reranking.py` for the happy path: scores land on the **right chunks by identity, not list position** — per [contracts/reranking-port.md](./contracts/reranking-port.md). Observe it fail.
- [X] T021 [P] [US1] Add a case to `services/chat/tests/test_reranking.py` that a response missing a survivor returns `None` rather than a partially-scored list
- [X] T022 [US1] Implement `rerank_chunks()` in a new `services/chat/src/chat/rag/reranking.py`, shaped like `rag/embeddings.py`'s `embed_texts` — domain types in, domain types out, no provider wire type escaping. Map `RerankingResult.index` back to the input chunk. Until T020–T021 pass.

### Client wiring — test first

- [X] T023 [US1] Write a failing test in `services/chat/tests/test_dependencies.py` that `get_rerank_client(request)` returns the reranking client and binds `voyageai.aiosession` for the request, exactly as the existing `get_voyage_client` test asserts
- [X] T024 [US1] Build a second `AsyncClient(api_key=..., max_retries=0)` for reranking in `services/chat/src/chat/main.py`'s lifespan and expose it on `app.state`, leaving the embedding client's behavior untouched ([research.md §2](./research.md))
- [X] T025 [US1] Add `get_rerank_client(request)` to `services/chat/src/chat/api/dependencies.py` until T023 passes

### The node — tests first

- [X] T026 [US1] Write failing tests in a new `services/chat/tests/test_answer_faq.py` that the generation context and the citations are both exactly the rerank survivors, and that a rejected chunk appears in neither (FR-014, FR-016)
- [X] T027 [P] [US1] Add a failing test to `services/chat/tests/test_answer_faq.py` that `rerank_chunks` is called with **exactly the similarity-gate survivors** — never the full observation pool — so the pool provably costs no extra model work (FR-002b)
- [X] T028 [P] [US1] Add a failing test to `services/chat/tests/test_answer_faq.py` that the text handed to retrieval is the turn's trailing patient message, unchanged from today (FR-035). Sub-query extraction is Phase 1f; this test is what stops T029 drifting into it by accident.
- [X] T029 [US1] Rewrite `answer_faq` in `services/chat/src/chat/agent/answer_faq.py` to drive retrieve → similarity gate → rerank → rerank gate → generate, building context and `Citation`s from `PipelineOutcome.survivors` only. Replace the `is_grounded` call. Until T026–T028 pass.
- [X] T030 [US1] Update `services/chat/tests/test_compose_answer.py` to the new `FaqResult` shape (`verdict`/`scored_chunks` in place of `grounded`/`chunk_scores`), including that a merged reply carries the FAQ half's citations unchanged (FR-017). Observe it fail.
- [X] T031 [US1] Replace `FaqResult.grounded`/`chunk_scores` with `verdict`/`scored_chunks` in `services/chat/src/chat/agent/compose_answer.py`, and rebuild `scored_citations()` from `scored_chunks` so the positional `zip(..., strict=True)` disappears ([research.md §6](./research.md)). Until T030 passes.

**Checkpoint**: US1 is independently demonstrable — a reranked answer with precise citations. This is the MVP.

---

## Phase 4: US2 — A question the corpus cannot answer is refused before a word is generated (P1)

**Goal**: all three abstentions produce the same patient-visible outcome, spend no generation call, and call staff.

**Independent test**: three corpus/threshold setups produce the fixed abstention message, zero generation calls, a staff call with the corpus-gap reason, and three distinct recorded verdicts.

- [X] T032 [US2] Write a failing test in `services/chat/tests/test_answer_faq.py` that an empty corpus abstains **without embedding or searching**, recording `abstained_empty_corpus` (FR-005(a))
- [X] T033 [P] [US2] Write a failing test that a below-floor pool abstains with `abstained_similarity_floor`, with **no reranking call and no generation call** made (FR-005(b), SC-002)
- [X] T034 [P] [US2] Write a failing test that a scored-but-all-below-rerank-floor turn abstains with `abstained_rerank_floor` and **no generation call** (FR-008)
- [X] T035 [US2] Write a failing test that all three abstentions produce the identical patient-facing message and identical escalation, and that no code branches on which verdict it is (FR-018, FR-019, FR-021a)
- [X] T036 [US2] Update `services/chat/tests/test_escalation.py` and `services/chat/tests/test_attention_marks.py` so the corpus-gap reason and mark are asserted against the three abstention verdicts rather than `grounded=False`. **No change needed**: both suites assert against `EscalationReason`/`AttentionMark` directly and never referenced `grounded`, so the escalation contract was already independent of the flag this phase removed. Verified by inspection, not assumed.
- [X] T037 [US2] Implement the abstention branch in `services/chat/src/chat/agent/answer_faq.py`: emit the existing abstention message with no citations, record `EscalationReason.CORPUS_COULD_NOT_ANSWER`, and return before any generation call. Until T032–T036 pass.

**Checkpoint**: the abstention path is complete and all three gates are distinguishable in the record while identical to the patient.

---

## Phase 5: US3 — A reranker outage degrades the answer instead of breaking the turn (P2)

**Goal**: a failing or hung reranker costs precision, never the turn.

**Independent test**: with the reranker failing, a question that clears the similarity floor still gets a cited answer; an error event names the failure; the verdict distinguishes it from a reranked answer.

- [X] T038 [US3] Write a failing test in `services/chat/tests/test_reranking.py` that a slow client yields `None` **within** `RERANK_TIMEOUT_SECONDS`, not after it (FR-010a, SC-009a)
- [X] T039 [P] [US3] Write failing tests that each failure class of FR-010 — transport error, refusal, rate limit, unusable response — returns `None` and raises nothing to the caller
- [X] T040 [P] [US3] Write a failing test that a failing client is called **exactly once** (no retry), and that a failing call followed by a succeeding one succeeds (no cross-turn state, FR-010b)
- [X] T041 [US3] Wrap the provider call in `asyncio.wait_for(...)` in `services/chat/src/chat/rag/reranking.py` and convert every failure to `None`. The bound covers provider-internal retries, not one attempt ([research.md §2](./research.md)). Until T038–T040 pass.
- [X] T042 [US3] Write a failing test in `services/chat/tests/test_answer_faq.py` that a reranker failure yields `answered_unreranked` with the similarity survivors as context and citations, **and makes no staff call** (FR-011, FR-013)
- [X] T043 [P] [US3] Write a failing test that a reranker failure on a turn that already failed the similarity gate still abstains — the fallback never rescues a question the first gate rejected (FR-012)
- [X] T044 [P] [US3] Add a case to `services/chat/tests/test_anthropic_failure.py` confirming reranking failure is **not** a `TurnPipelineError`, unlike embedding and search — a reranker outage must not mark the message assistant-failed
- [X] T045 [US3] Implement the fallback branch in `services/chat/src/chat/agent/answer_faq.py` until T042–T044 pass

**Checkpoint**: the new external dependency cannot take down the path that worked before it existed.

---

## Phase 6: US4 — Every stage's decision is reconstructable from the logs (P2)

**Goal**: the Phase 2 input contract, emitted in full.

**Independent test**: from one answered turn's and one abstained turn's log lines alone, state every candidate and score at each stage, every keep/drop decision with its threshold, and the verdict — without re-running the turn or reading source.

- [X] T046 [US4] Write failing assertions on captured structlog output in `services/chat/tests/test_pipeline.py` for `faq.retrieval_completed`: pool size, pool returned, every candidate with score in order — per [contracts/log-events.md](./contracts/log-events.md)
- [X] T047 [P] [US4] Write failing assertions that a considered candidate carries **full** `chunk_text` and an excluded one carries text cut to 200 chars with `considered: false` (FR-027a)
- [X] T048 [P] [US4] Write a failing assertion that `text_truncated` is `true` **only when the text was actually cut** — a chunk shorter than 200 chars is logged whole and unmarked (FR-027b)
- [X] T049 [P] [US4] Write failing assertions for `faq.similarity_gate` and `faq.rerank_gate`: floor, cap, kept, and **two separate drop lists** (`dropped_by_floor`, `dropped_by_cap`) (FR-028, FR-030)
- [X] T050 [P] [US4] Write failing assertions for `faq.reranking_completed` (model, count, duration, both scores per candidate) and `faq.verdict` (verdict, survivor count, gate, `best_score_seen`) (FR-029, FR-032)
- [X] T051 [US4] Write a failing assertion that `faq.reranking_unavailable` is emitted at **ERROR** level with a `reason` from the closed set, on every fallback path (FR-031)
- [X] T052 [US4] Emit all six events from `services/chat/src/chat/rag/pipeline.py` and `services/chat/src/chat/rag/reranking.py` until T046–T051 pass, inheriting `turn_id`/`node` from bound context rather than passing them (FR-026)
- [X] T053 [US4] Delete the superseded `turn.groundedness_verdict` and `faq.retrieved` events from `services/chat/src/chat/agent/answer_faq.py`
- [X] T054 [P] [US4] Add a case to `services/chat/tests/test_logging.py` confirming chunk text passes through the existing redaction chain with **no new exemption** (FR-033)
- [X] T055 [US4] Write a failing test that `turn.completed` drops `grounded`, adds `faq_verdict`, and carries **both** scores per citation with `rerank_score` absent — not `0.0`, not the similarity score — on an unreranked turn (FR-025, FR-025a)
- [X] T056 [P] [US4] Write a failing test asserting SC-004b — verdict, survivors and both scores are all readable from the single `turn.completed` record with no other event required
- [X] T057 [US4] Update `turn.completed` in `services/chat/src/chat/agent/compose_answer.py` until T055–T056 pass

**Checkpoint**: a threshold can be tuned by reading logs alone, which is what makes the calibration in Phase 8 possible.

---

## Phase 7: US5 — A turn's outcome says which gate it stopped at (P3)

**Goal**: the verdict replaces `grounded` on every recorded and reported surface.

**Independent test**: one turn of each of the five kinds reports the matching verdict in the stored message, the terminal event and the rendered history; a booking-only turn reports none.

### Backend — tests first

- [X] T058 [US5] Write failing tests in `services/chat/tests/test_turn_api.py` that `ChatDoneEvent` carries `faq_verdict` and no `grounded`, for all five verdicts plus the booking-only `None` (SC-007)
- [X] T059 [P] [US5] Write a failing test that a booking-only turn, a patient message and a staff message all carry `faq_verdict = None` — absent, never defaulted (FR-022)
- [X] T060 [P] [US5] Update `services/chat/tests/test_chats_api.py`, `services/chat/tests/test_console_api.py`, `services/chat/tests/test_staff_messages.py`, `services/chat/tests/test_graph.py` and `services/chat/tests/test_silence_gate.py` to the new field. Observe them fail.
- [X] T061 [US5] Replace `grounded` with `faq_verdict: FaqVerdict | None` on `ChatDoneEvent` and `MessageOut` in `services/chat/src/chat/domain/schemas.py`, keeping `citations` on **both** endpoint shapes (FR-016a). **Done early, in Phase 3**: `answer_faq` (T029) emits `ChatDoneEvent`, so the tree does not compile with the field still named `grounded`. The task list was wrong to place this in Phase 7; the tests that cover it (T058–T060) still run there.
- [X] T062 [US5] Persist the verdict in `services/chat/src/chat/api/turn.py` and `services/chat/src/chat/repositories/chat_repository.py`, replacing the `grounded` write

### Frontend — specs first

- [X] T063 [US5] Write failing specs in `services/frontend/tests/MessageView.test.tsx`: `data-faq-verdict` replaces `data-grounded`; citations render only when the new prop says so; `data-testid="verdict-mark"` appears **only** for `answered_unreranked`
- [X] T064 [US5] Update `services/frontend/src/components/MessageView.tsx` — citations behind a prop, `data-faq-verdict`, and the marker with its `title`, visually distinct from spec 007's `attention-mark` (FR-023c). Until T063 passes.
- [X] T065 [US5] Write a failing spec in `services/frontend/tests/ChatWindow.test.tsx` asserting citations are **absent** from the patient pane on every verdict — a rule that only appears as an absence needs a test that fails when the absence stops holding (SC-005a)
- [X] T066 [US5] Update `services/frontend/src/components/ChatWindow.tsx` to pass citations off and render no verdict marker (FR-023b, FR-023d). Per FR-023f, any comment explaining this MUST describe it as not rendered, never as withheld — the data is in the payload and the patient's own session owns the corpus.
- [X] T067 [P] [US5] Write failing specs in `services/frontend/tests/StaffThread.test.tsx` that citations render, the marker renders on `answered_unreranked` only, and **no score appears anywhere** (FR-023e, FR-023g, SC-007a)
- [X] T068 [US5] Update `services/frontend/src/components/StaffThread.tsx` until T067 passes
- [X] T069 [P] [US5] Update the terminal-event type in `services/frontend/tests/chatStream.test.ts` to expect `faq_verdict`. Observe it fail.
- [X] T070 [US5] Update the type and mapping in `services/frontend/src/lib/chatStream.ts` until T069 passes

**Checkpoint**: no surface anywhere still carries `grounded`.

---

## Phase 8: Polish, calibration and documentation

**Purpose**: close the one number the plan left open, verify the outcomes no unit test can reach, and leave the docs true.

- [X] T071 Confirm against the live API that `rerank-3` is the exact model id and that its `relevance_score` is normalized to [0, 1] ([research.md §1](./research.md)). If the scale differs, T073's starting point moves with it.
- [X] T072 Fill in the 20 questions in `specs/008-reranked-retrieval-pipeline/calibration/questions.md` by reading `services/chat/src/chat/rag/default_corpus.py` — including the three vocabulary-overlap questions that must reach the **rerank** gate, which are what prove the reranker rather than the similarity floor
- [X] T073 Run the calibration, sweeping `RERANK_FLOOR`, and record the chosen value, the sweep, and the measured result in `calibration/questions.md`. **The feature is not done until this is filled in** — `0.4` ships provisional, not measured.
- [X] T074 Set the measured value as the `RERANK_FLOOR` default in `services/chat/src/chat/core/config.py`, replacing the provisional one and its ⚠️ marker in [data-model.md §4](./data-model.md)
- [X] T075 Verify SC-006a by hand: the same question at `RETRIEVAL_POOL_SIZE=25` and `=10` gives the same citations, verdict and answer, differing only in the log. If the answer moves, the pool is leaking into the pipeline.
- [X] T076 Verify SC-004a: capture one answered turn's log at `RETRIEVAL_POOL_SIZE=25` and compare its byte size against the same turn on `main`. Roughly today's, not 5×; if it scaled with the pool, the 200-char truncation is not being applied where FR-027a requires.
- [X] T077 Verify SC-009: time one single-intent FAQ turn end to end against the same turn on `main`. The growth must be accounted for by the reranking call plus the wider search and nothing else — no stage run twice, no extra embedding call. Record the two numbers beside the calibration result; they are the baseline Phase 2 will regress against.
- [X] T078 Verify FR-034 by inspection: `git diff main -- services/chat/src/chat/rag/chunking.py services/chat/src/chat/rag/indexing.py` is empty, and no migration or script re-indexes existing corpus content
- [X] T079 [P] Add the reranker to the technology table in `README.md` with its tradeoff, per constitution principle VI
- [X] T080 [P] Reconcile `docs/ROADMAP.md`'s Phase 1e bullets with what shipped — the "defensible chunking" bullet describes retrieval, not chunking, and chunking was deliberately not changed
- [X] T081 [P] Update `.claude/CLAUDE.md`'s architecture notes where they describe the groundedness gate, which no longer exists
- [X] T082 Walk all seven sections of [quickstart.md](./quickstart.md) end to end, including both migration directions and both panes. **Covered by suite rather than by hand**, and quickstart.md's done-list records which: §3's five verdicts and §4's pool-inertness became cases in `test_answer_faq.py` (the shipped corpus is nine chunks, so a manual run cannot tell a pool of 25 from one of 10), §5's two panes are in the frontend specs, §6 is `test_migrations.py`, §7 ran for real. What is **not** done: driving the live stack in a browser, which needs `make services-up` and spends real model calls.
- [X] T083 Run `make test` and `make precommit` once, at the end, and fix what they surface

---

## Dependencies

```text
Phase 1 (Setup)  ──►  Phase 2 (Foundational)  ──┬──►  Phase 3 (US1, P1) ──┐
                                                │                          ├──► Phase 6 (US4, P2)
                                                ├──►  Phase 4 (US2, P1) ──┘         │
                                                │                                   │
                                                └──►  Phase 7 (US5, P3)             │
                                                                                    │
                        Phase 3 ──► Phase 5 (US3, P2) ─────────────────────────────┘
                                                                                    │
                                                                    Phase 8 (Polish) ◄─ all
```

- **Phase 2 blocks everything.** The gate functions, the domain types and the schema change are prerequisites for every story.
- **US3 depends on US1** — you cannot degrade a stage that does not exist yet.
- **US4 depends on US1+US2+US3** — it logs decisions those phases make. It is P2 rather than P1 only because the pipeline works without it; the phase's *value* does not.
- **US5 is independent of US3/US4** and can be built any time after Phase 2. It is last by priority, not by dependency.
- **T073 gates completion**, not just Phase 8: the shipped `RERANK_FLOOR` is unmeasured until it runs.

## Parallel opportunities

| Where | Tasks |
|---|---|
| Setup | T002 alongside T003/T004 |
| Foundational | T006 ‖ T007; T009 alongside T008 once the types exist |
| US1 | T021 alongside T020; T027 ‖ T028 |
| US2 | T033 ‖ T034 |
| US3 | T039 ‖ T040; T043 ‖ T044 |
| US4 | T047 ‖ T048 ‖ T049 ‖ T050 — four separate event contracts, one test file, independent cases; T054 ‖ T056 |
| US5 | T059 ‖ T060; T067 ‖ T069 |
| Polish | T079 ‖ T080 ‖ T081 — three different documents |

Backend and frontend work inside US5 can proceed in parallel by two people; `make test-frontend` is jsdom-only and may run alongside any Python tier (`docs/testing-strategy.md`).

## Implementation strategy

**MVP = Phase 1 + Phase 2 + Phase 3 (US1).** That delivers the phase's headline claim — an answer that stands only on chunks a cross-encoder judged relevant, with citations that mean it. It is demonstrable on its own.

**Then Phase 4 (US2)**, which completes the P1 pair: the pipeline that can answer precisely can also refuse. Together these two are the whole user-visible behavior change.

**Then Phase 5 (US3)** before Phase 6, because an outage story is cheap to build and expensive to discover in production, and Phase 6 has to log its events anyway.

**Phase 6 (US4) is not optional despite being P2.** The roadmap makes these logs the input to Phase 2's calibration, and Phase 8's own T073 cannot run without them.

**Phase 7 (US5) last**, as a contract sweep over surfaces the earlier phases already made correct.

## Format validation

All 83 tasks carry a checkbox, a sequential ID, a `[P]` marker where genuinely parallel, a `[USn]`
label in story phases only (none in Setup, Foundational or Polish), and an explicit file path or a
named artifact. No implementation task precedes the test that covers it, and no task bundles the two.

---

## Phase 9: Convergence

Appended by `/speckit-converge` after the implementation pass. Each item names the requirement it
traces to and the kind of gap it closes.

- [X] T084 Derive the retrieval event's `considered` flag from the similarity gate's `kept` set rather than from a candidate's position in `services/chat/src/chat/agent/answer_faq.py`'s `_log_retrieval`, per FR-027a and SC-004 (partial). Today it marks the first `SIMILARITY_CAP` candidates considered regardless of the floor, so a pool of `[0.9, 0.8, 0.1, 0.05, 0.04]` logs five considered candidates with full text where the gate kept two. Add the case to `services/chat/tests/test_answer_faq.py`: the existing coverage missed it because every chunk in those pools cleared the floor.
- [X] T085 Run the full unit tier to completion and fix what it surfaces, per T083 and plan.md's Testing Strategy (missing). The last complete run was 1151 passed / 38 failed; all 38 traced to one missing argument in `test_graph.py`'s `run_turn` helper, which is fixed and whose suites pass in isolation — but no full run has confirmed the whole tier since. Run it with nothing else touching the database.
- [X] T086 Emit both `faq.retrieval_completed` and `faq.similarity_gate` only when a search was actually issued, per FR-026 and SC-004 (contradicts). An empty-corpus turn currently logs both with empty payloads, so a reader cannot tell "the floor rejected everything" from "there was nothing to search" — the distinction the five-verdict split exists to preserve. `turn.retrieval_skipped_empty_corpus` already records that case.
- [X] T087 Add `duration_ms` to `faq.reranking_completed` in `services/chat/src/chat/rag/reranking.py`, per FR-029 and `contracts/log-events.md` (partial). It is the only per-stage latency signal for the reranking call, and SC-009's latency claim has nothing else to rest on.
- [X] T088 Run `make test-integration` and fix what it surfaces, per `docs/testing-strategy.md` (missing). `tests/integration/test_faq_session_isolation.py` had five verdict assertions rewritten during this phase and the tier has not been run since. It needs a live scheduler and must not run alongside another database-backed tier.
- [X] T089 Walk `quickstart.md` §2–§5 against a running stack (`make services-up`), per quickstart.md (missing). **Done 2026-09-06 against live Claude, live `voyage-4-lite` and live `rerank-3`.** All five verdicts reproduced end to end; the six log events verified field by field; both panes driven in Chromium. Findings recorded in quickstart.md's done-list. Every section is covered by automated tests, but nobody has driven the real thing: this is the only step that exercises live embeddings, a live reranker, and both panes in a browser at once.
- [X] T090 Document `GateResult` and `FaqVerdict.answered` in `data-model.md` §3, or fold them into the types already listed there, per data-model.md (unrequested). Both are load-bearing — the two drop lists and the answered/abstained predicate — and neither appears in any artifact.

---

## Phase 10: Convergence

Appended by a second `/speckit-converge` pass, after Phase 9 closed. Three documentation-accuracy
gaps; no functional defect found.

- [X] T091 **CRITICAL** — Correct the pipeline-step comment at `services/chat/src/chat/api/turn.py:57`, per Constitution VI (contradicts). It still names `"groundedness" (pure computation)` as a step whose failure is not an outage, but no code can produce that stage: `TurnPipelineError` now carries only `embedding`, `retrieval`, `generation` and `persistence`. The stage was deleted with `rag/groundedness.py` in this same feature, and Constitution VI requires documentation to be updated in the change that makes it stale. Reranking is the case the comment should name in its place — a failure there is absorbed and never reaches this classification at all.
- [X] T092 Add `unexpected` to the closed set of reasons in `contracts/log-events.md`'s `faq.reranking_unavailable` entry, per FR-031 (partial). `_reason_for` returns it for anything the provider does not raise as one of its own errors, and it fired during the live walk when a test guard raised through the reranker. The value is deliberate — filing an unknown failure under a cause it was not is worse than naming it unknown — but the contract lists five reasons and the code emits six. Extend the contract, and add a test asserting the emitted reason is always one of the documented set so the two cannot drift again.
- [X] T093 Tighten the four remaining Phase 1e bullets in `docs/ROADMAP.md` to name what shipped, per Constitution VI (partial). T080 fixed the two that were factually wrong; these four are accurate in substance but imprecise now that the numbers exist — "the answer is provided based on cosine step only" is the ≤5 similarity survivors, "the caps" are 0.3/5 and 0.58/3, and "every step is logged" is the six named events in `contracts/log-events.md`. Preserve the author's own framing; this is a precision pass, not a rewrite.

---

## Phase 11: Convergence

Appended by a third `/speckit-converge` pass. One finding: the gate contract no longer describes the
shape the gates actually return.

- [X] T094 **CRITICAL** — Correct `contracts/pipeline-gates.md` to describe what the gates return, per Constitution VI and FR-028 (partial). It declares `apply_similarity_gate(pool, *, floor, cap) -> list[ScoredChunk]` and `apply_rerank_gate(scored, *, floor, cap) -> list[ScoredChunk]`; both return **`GateResult`**, and the contract does not mention that type or its two rejection lists anywhere — despite citing FR-028, whose entire subject is telling dropped-by-floor apart from dropped-by-cap. Fix all three: the two signatures, a description of `GateResult`'s three fields (matching the entry `data-model.md` §3 already carries), and the two "returns empty" lines, which now mean an empty `GateResult` rather than an empty list. No code change — the code is right and the document is behind it.
