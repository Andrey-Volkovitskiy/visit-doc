---

description: "Task list for Metrics Over the Golden Set (Phase 2b)"
---

# Tasks: Metrics Over the Golden Set (Phase 2b)

**Input**: Design documents from `/specs/012-golden-set-metrics/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Mandatory (constitution VIII). Every implementation task is preceded by its test task:
contract → tests written → tests run and **observed failing** → implementation → tests observed
passing. No test spends a live model call (research R10). Tests that touch Postgres use the chat
suite's test database the way `services/chat/tests/conftest.py` does; HTTP is stubbed with
`httpx.MockTransport`; gRPC with a fake `SchedulingServiceStub`.

**Organization**: by user story. US1 carries the whole loop (drive → record → align → score →
report); US2 and US3 are further readings of the same stored runs plus what they need to be driven.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1 / US2 / US3, only on user-story phase tasks

## Path conventions

- Harness member: `evals/harness/` — source `evals/harness/src/golden_harness/`, tests
  `evals/harness/tests/`, hand-written recorded runs `evals/harness/tests/fixtures/runs/<name>/`
  (each a `labels.json` in `cases.json` shape, a `run.json` carrying that file's label digests, and
  `cases/<id>.json`, shaped per `contracts/run-record.md`)
- Label data: `evals/golden/`
- The two changes outside the harness (FR-051): the JSON log renderer (`packages/shared-logging/`,
  `services/chat/src/chat/core/`) and the `service.configured` startup event
  (`services/chat/src/chat/main.py`, `services/chat/src/chat/rag/embeddings.py`)

## Decisions settled with the user (2026-09-14)

Recorded in spec.md's Clarifications (Session 2026-09-14). Each names the requirement it landed in,
and tasks cite that requirement rather than this list.

1. **A handed-off turn is scored for classification**, and excluded from retrieval and serving only
   (FR-018a). `run_error`, `silenced_turn`, `cancelled_turn`, `missing_log_slice`,
   `unresolvable_fixture` and `outcome_unknown` exclude a case from every metric. No case in the set
   pairs a `booking` request with a stopping intent, so the booking metrics never meet a handed-off
   turn.
2. **How turn shapes are told apart.** `handed_off_turn` = a `done` terminal event with
   `answer_source == "hand_off"`. `silenced_turn` = a `silent` terminal event (the conversation was
   already silenced — no classification happened). `cancelled_turn` = a `cancelled` terminal event.
   `run_error` = a patient message carrying `attention_mark == "assistant_failed"` with **no** stored
   reply, or a produced intent of `classification_failed` (FR-023). A turn marked `assistant_failed`
   that stored a reply is scored and listed (FR-007e).
3. **Exclusion reasons** are the six case-scoped ones above, `handed_off_turn`, and three that apply
   to one request's retrieval: `reranker_unavailable` and `not_reached_reranker` (rerank stage;
   FR-029, FR-029a) and `no_search` (both stages). Each is a distinct value.
4. **A booking case whose request may have reached the pipeline is never retried** (FR-007d). For a
   fixture-carrying case only `NOT_SENT` is re-driven; `SENT_NO_ANSWER` records `outcome_unknown`.
5. **Conditions come from the running chat service**, through its `service.configured` startup
   event (FR-047c), never from the harness's own environment.
6. **The corpus guarantee is about turns** (FR-011): seeding the run's session spends embedding calls
   before the check, and a mismatch stops the run before its first turn.
7. **A run records a digest of each selected case's scored fields**, and scoring refuses labels whose
   digests differ (FR-044a).
8. **A person approves the 18 fixtures before the first full run** (FR-038a, T050a).
9. **The harness cancels what each case left standing** once its post-state is stored, for every
   case whose chat has a patient (FR-041b). A cleanup that cannot complete stops the run; a resumed
   run repeats it for every recorded case before driving another.
10. **A write the booking loop must confirm is measured over a scripted reply turn** (FR-037b): the
    calendar is read after the first turn (must still be `given`) and after the reply (must match
    `expect`); tool selection spans both turns; classification and retrieval read the first turn.
11. **`no_search` means only an empty corpus**; a labelled FAQ request produced under another intent
    is `not_routed_to_faq` (FR-018a) — T068.
12. **The 18 fixtures are approved** by the project owner (2026-09-14), recorded in
    `evals/golden/PROVENANCE.md` — T050a.
13. **Kept, and documented rather than removed** (T072–T074): the run stops when a booking case's
    appointments cannot be read after its turn (`contracts/run-record.md` §When a case is not
    written); `misclassified` is a third unserved cause (`contracts/metrics.md` §C1); and six driver
    behaviours — a cleanup failed between attempts records `run_error` and stops; a broken stream
    whose reply was stored counts as measured; resume stops when the corpus entry ids changed; a case
    chat that mints a new session is refused; an `expect` naming an unseeded practitioner is rejected
    at load; `run` accepts `--labels`, its label digests recording which set it ran against.
14. **A log-contract or record-invariant break fails the whole scoring pass**, naming the case,
    rather than excluding it (`contracts/log-access.md`) — T075.

Two choices made while generating the tasks, not raised as questions:

- **Plantability is checked against the scheduler's own `SESSION_SEED` and `BOOKING_HORIZON_DAYS`**,
  imported from `scheduler` as a second workspace dependency — the reasoning research R5 gives for
  importing `chat`'s enums rather than copying them.
- **The default artifact directory is `.run/evals/<run_id>/`** — `.run/` is already gitignored, which
  is what FR-048b needs.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: the `golden-harness` workspace member exists, is on every gate, and has its commands.

- [X] T001 Create `evals/harness/pyproject.toml` for distribution `golden-harness` (Python `>=3.12`, `src/` layout, module `golden_harness`) with dependencies `chat`, `scheduler`, `shared-db`, `shared-proto`, `httpx`, `grpcio`, `pydantic`, `python-ulid`, and `tool.uv.sources` workspace entries for the four workspace members — copy the build-backend and sources shape from `services/chat/pyproject.toml`
- [X] T002 Create the package skeleton: `evals/harness/src/golden_harness/__init__.py`, `evals/harness/src/golden_harness/py.typed`, `evals/harness/src/golden_harness/driver/__init__.py`, `evals/harness/src/golden_harness/scoring/__init__.py`, `evals/harness/tests/driver/`, `evals/harness/tests/scoring/`, `evals/harness/tests/fixtures/runs/` (empty `__init__.py` files only where the other members' tests have them)
- [X] T003 Edit root `pyproject.toml`: add `"evals/harness"` to `[tool.uv.workspace] members`; add `evals/harness/src` to `[tool.mypy] files` and to `mypy_path`; add `evals/harness/tests` to `[tool.pytest.ini_options] testpaths`
- [X] T004 Add the JSON-schema validator: `uv add --package golden-harness jsonschema` (updates `evals/harness/pyproject.toml` and `uv.lock`), then `make sync`, then confirm `make lint`, `make typecheck-python` and `uv lock --check` pass on the empty member
- [X] T005 [P] Add Makefile targets in `Makefile`: `eval-run` (`uv run --package golden-harness -- python -m golden_harness run` passing `--cases $(CASES)` / `--family $(FAMILY)` only when set) and `eval-score` (`... score --run $(RUN)`); add both to `.PHONY`

**Checkpoint**: `make test-unit` still green; the new member is linted and type-checked.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the machine-readable log, the label loader, the corpus pin, the record models, the log
slice, and alignment — every story reads through all of them.

**⚠️ CRITICAL**: no user-story work begins until this phase is complete.

### A. The JSON log renderer (`contracts/log-access.md`, research R1)

- [X] T006 Write failing tests in `packages/shared-logging/tests/test_logging.py` for a new `LogFormat` StrEnum (`console`, `json`) and `configure_logging(..., log_format=...)`: (a) default is `console` and today's console output is unchanged; (b) `json` renders exactly one JSON object per line carrying `event`, `level`, `timestamp` and a contextvar-bound `turn_id`; (c) a nested `StrEnum` inside a list renders as its value (the depth-dependence R1 found); (d) a `datetime` argument renders via `default=str` rather than being dropped; (e) a log call carrying a live secret renders `***REDACTED***` under **both** formats, proving the renderer receives an already-redacted event dict; (f) a string over 2,000 chars is truncated under `json`. Run and observe failure
- [X] T007 Implement `LogFormat`, `_build_json_renderer()` (`json.dumps(event_dict, default=str)`, no ANSI) and the `log_format` parameter in `packages/shared-logging/src/shared_logging/logging.py` — the renderer stays the **last** processor, nothing added before it; export `LogFormat` from `packages/shared-logging/src/shared_logging/__init__.py`; update the module docstring's "switching the rendering" sentence to name the two formats. Run T006 and observe it pass
- [X] T008 Write failing tests: in `services/chat/tests/test_config.py` that `Settings.LOG_FORMAT` defaults to `LogFormat.CONSOLE` and accepts `json` from the environment; in `services/chat/tests/test_logging.py` that `chat.core.logging.configure_logging` forwards `settings.LOG_FORMAT`. Run and observe failure
- [X] T009 Add `LOG_FORMAT: LogFormat = LogFormat.CONSOLE` beside `LOG_LEVEL` in `services/chat/src/chat/core/config.py` (with a comment naming the golden harness as the consumer of `json`), and pass `log_format=settings.LOG_FORMAT` in `services/chat/src/chat/core/logging.py`. Run T008 and observe it pass. The scheduler is deliberately untouched

### A2. The settings event (FR-047c, `contracts/log-access.md` §The settings event)

- [X] T009a Write failing tests in `services/chat/tests/test_main.py`: entering the app's lifespan emits exactly one `service.configured` event at INFO carrying `classification_model`, `generation_model`, `embedding_model`, `rerank_model`, `retrieval_pool_size`, `similarity_floor`, `similarity_cap`, `rerank_floor`, `rerank_cap`, `max_segments` and `context_turns`, each equal to what the service runs with (`Settings`, `chat.domain.schemas.MAX_SEGMENTS`, `chat.rag.embeddings.EMBEDDING_MODEL`); overriding a setting through the environment changes the emitted value; the event carries no field named in `_SECRET_SETTINGS_FIELDS` or `_SECRET_URL_SETTINGS_FIELDS`. Run and observe failure
- [X] T009b Rename `_MODEL` to `EMBEDDING_MODEL` in `services/chat/src/chat/rag/embeddings.py` (every use is in that module) and emit `service.configured` from `lifespan` in `services/chat/src/chat/main.py` — once, after the settings are read and before any client is built — with exactly the fields T009a names. Nothing a turn does changes. Run T009a and observe it pass

### B. The label loader (FR-008, FR-014)

- [X] T010 [P] Write failing tests in `evals/harness/tests/test_cases.py`: loading `evals/golden/cases.json` yields 135 cases carrying 190 requests and 117 `faq_question` requests; validation runs against `evals/golden/schema.json` and a malformed copy (written to `tmp_path`) fails naming the offending case id; `select(cases, ids=[...])` and `select(cases, family=...)` return exactly those cases in file order; an unknown id or family raises rather than returning an empty selection; the `IntentLabel` values in `schema.json`'s enum equal `chat.domain.schemas.IntentLabel` minus `classification_failed` (R5 drift guard); `label_digests(cases)` returns one sha256 per case over the canonical JSON (sorted keys, no whitespace) of its scored fields — `id`, `message`, `history`, each request's `intent`/`answerable`/`cites`/`tools`, and `scheduling` — so editing a `gist`, `note`, `source` or `family` leaves a case's digest unchanged and editing an intent changes it (FR-044a). Observe failure
- [X] T011 Implement `evals/harness/src/golden_harness/cases.py`: pydantic `Case`, `LabelledRequest` (intent typed as `IntentLabel`, `gist` kept but documented as never scored — FR-013), `HistoryEntry`; `load_cases(path)` validating with `jsonschema` first; `select(...)` returning a `Selection` (ids + how chosen); `label_digests(...)`. Observe T010 pass

### C. The corpus pin (FR-010–FR-012, research R3)

- [X] T012 [P] Write failing tests in `evals/harness/tests/test_corpus.py`: `corpus_digest(texts)` = sha256 over each text UTF-8 encoded and terminated by `\n`, in index order; the digest of `chat.rag.default_corpus.DEFAULT_FAQ_ENTRIES` texts equals `evals/golden/corpus.json`'s `sha256` and the file names that construction in `algorithm`; `verify(live_entries, pin)` on a one-character edit returns a mismatch naming exactly that entry's pinned id; `map_entry_ids(live_entries, pin)` maps every pinned slug (e.g. `what-to-bring`) to the session's integer entry id by exact text, and raises naming the missing slug when any pinned entry has no live counterpart (FR-012). Observe failure
- [X] T013 Re-take the pin in `evals/golden/corpus.json`: add an `algorithm` field stating the construction in T012 and replace `sha256` with the value it produces over the current `entries[].text`; in `evals/golden/PROVENANCE.md` add a dated section recording the previous value `9552b098…a267`, that none of the seventeen constructions in research R3 reproduced it, and that the texts had not drifted
- [X] T014 Implement `evals/harness/src/golden_harness/corpus.py` (`corpus_digest`, `load_pin`, `verify` → a typed `CorpusCheck` with `matched`, `live_sha256`, `pinned_sha256`, `moved_entry_ids`, and `map_entry_ids`). Observe T012 pass

### D. The record (`contracts/run-record.md`, data-model §2)

- [X] T015 [P] Write failing tests in `evals/harness/tests/test_record.py`: `CaseRun` and `Run` round-trip through JSON unchanged; `assistant_message.request_outcomes` is parsed through `chat.domain.schemas.RequestOutcome`, so an outcome with `verdict=answered` and `answer=None` is rejected on load; an unknown `FaqVerdict` or `IntentLabel` string is rejected, not bucketed; `ExclusionReason` is exactly `run_error, silenced_turn, handed_off_turn, cancelled_turn, missing_log_slice, unresolvable_fixture, outcome_unknown, reranker_unavailable, not_reached_reranker, no_search` (decision 3); `Terminal.kind` is exactly `done|silent|cancelled|error`; `CaseRun.patient_id` is carried when the chat has one, and `CaseRun.cancelled_after` is a list of `AppointmentState`, empty by default (FR-041b); `CaseRun.excluded` accepts only the turn-level reasons (`run_error`, `silenced_turn`, `handed_off_turn`, `cancelled_turn`, `missing_log_slice`, `unresolvable_fixture`, `outcome_unknown`) and rejects the request-level ones, which scoring derives and nothing stores; `Run.entry_ids` maps every pinned slug to an integer entry id and is required; `Run.labels` carries one digest per selected case and is required (FR-044a); `Run.conditions` requires `classification_model`, `generation_model`, `embedding_model`, `rerank_model`, `retrieval_pool_size`, `similarity_floor`, `similarity_cap`, `rerank_floor`, `rerank_cap`, `max_segments` and `context_turns` — the fields of the `service.configured` event; `write_case`/`read_run` write via a temp file + rename so a crash never leaves a half-written case file, and `recorded_case_ids(run_dir)` is a directory listing. Observe failure
- [X] T016 Implement `evals/harness/src/golden_harness/record.py` with `Run`, `RunConditions`, `CorpusRecord`, `CaseRun`, `Terminal`, `StoredMessage`, `ProducedSegmentation` (`segments: list[{position, intent, text}]`, `cap_bound`), `AppointmentState`, `ExclusionReason`, and the atomic read/write helpers. Nothing in it is a score. Observe T015 pass

### E. The log slice (`contracts/log-access.md`, research R2)

- [X] T017 [P] Write failing tests in `evals/harness/tests/driver/test_logslice.py`, one per invariant: reading from a recorded byte offset returns only lines appended after it; lines are grouped by `turn_id`; the group whose `turn.message_received.message_ids_unified` contains the case's patient message id is selected; a slice containing a second, interleaved turn (a browser user) selects only the case's turn; zero matching groups and two matching groups each return a typed `SliceMissing` (never an empty event list); a trailing partial line (no `\n` yet) is not parsed; lines not beginning with `{` — uvicorn's startup and access lines, a traceback — are skipped as foreign, and a slice interleaving them with the turn's events still selects every event; a line beginning with `{` that does not parse returns `SliceMissing`; `require_json_log(path, since_offset)` raises when the bytes after `since_offset` hold no line parsing as a JSON object with an `event` key, including when they hold only uvicorn lines and console-format structlog lines; `service_conditions(path, before_offset)` returns the fields of the **latest** `service.configured` event before `before_offset`, and raises when there is none (FR-047c); `restarts_in(lines)` returns every `service.configured` event inside a slice; `produced_segmentation(events)` reads `intent.classified.segments` and `cap_bound`, and returns `SliceMissing` when the event is absent. Observe failure
- [X] T018 Implement `evals/harness/src/golden_harness/driver/logslice.py` (`log_offset`, `read_slice`, `select_turn`, `require_json_log`, `service_conditions`, `restarts_in`, `produced_segmentation`). Observe T017 pass

### F. Alignment and the metric value (`contracts/metrics.md` §Alignment, FR-015–FR-018, FR-045)

- [X] T019 [P] Write failing tests in `evals/harness/tests/scoring/test_alignment.py` and `evals/harness/tests/scoring/test_metric.py`: a case with equal counts aligns by position; unequal counts mark **all** its labelled requests unaligned; the case-scoped reasons are exactly `run_error`, `silenced_turn`, `cancelled_turn`, `missing_log_slice`, `unresolvable_fixture` and `outcome_unknown`, and a case carrying one is `excluded` and never aligned; `handed_off_turn` still aligns and is excluded only by the retrieval and serving scorers (FR-018a); `AlignmentTotals` over a list of cases sums to the labelled total and the scorer raises `ConservationError` if a request is in no state or two; `Metric` with denominator 0 has `value == "not_measured"` (never 0.0 or 1.0), always publishes numerator, denominator and excluded counts by reason. Observe failure
- [X] T020 Implement `evals/harness/src/golden_harness/scoring/alignment.py` (`align_case`, `align_run`, `AlignmentTotals`, the conservation assertion) and `evals/harness/src/golden_harness/scoring/metric.py` (`Metric`, `Exclusions`, `CASE_SCOPED_REASONS`). Neither module may import anything under `golden_harness.driver` — add a test in `evals/harness/tests/scoring/test_purity.py` that parses each `scoring` module's own source with `ast` and asserts none of its import statements names `httpx`, `grpc`, `sqlalchemy` or `golden_harness.driver` (FR-044) — the check is on direct imports, since `record.py` and `cases.py` legitimately reach `chat` and `scheduler`, which import SQLAlchemy themselves. Observe T019 pass

**Checkpoint**: the JSON log is renderable, the service states its settings at startup, labels and pin load and verify, records round-trip, and
alignment conserves requests — all without a stack.

---

## Phase 3: User Story 1 — Score what the classifier decided (Priority: P1) 🎯 MVP

**Goal**: drive a selection of cases as real turns, store each case, and report request-count
accuracy, intent accuracy, exact segmentation match, the disagreement list, `cap_bound` turns, and
the conditions, with every metric not computed named.

**Independent Test**: `make eval-run CASES=G001,G034` against the stack running with
`LOG_FORMAT=json` produces `.run/evals/<run_id>/run.json` + two case files, and a report carrying
A1–A3 with denominators, alignment totals, and US2/US3 metrics listed as not computed;
`make eval-score RUN=<run_id>` with the stack down prints identical numbers.

### Tests for User Story 1 (write first, confirm failing) ⚠️

- [X] T021 [P] [US1] Hand-write the recorded run `evals/harness/tests/fixtures/runs/us1/` (`run.json` + `cases/*.json`, labelled against a companion `evals/harness/tests/fixtures/runs/us1/labels.json` in `cases.json` shape) covering: two requests produced in the labelled order and intents (AS1); a two-request label with one produced (AS2); a produced `classification_failed` (AS3); a `done` with `answer_source="hand_off"` for a `faq_question + urgent_condition` label whose intents were produced correctly; a `silent` terminal; a `cancelled` terminal; a `cap_bound: true` turn; one case with `attempts: 2`; and a `done` turn whose patient message carries `assistant_failed` beside a stored reply (FR-007e). Record the expected A1/A2/A3 numerators and denominators in a comment block at the top of `evals/harness/tests/scoring/test_classification.py`
- [X] T022 [P] [US1] Write failing tests in `evals/harness/tests/scoring/test_classification.py` over the T021 run: A1 request-count accuracy, A2 intent accuracy over aligned requests with the unaligned count beside it, A3 exact segmentation match; `classification_failed` yields `run_error` and appears in no numerator or denominator (FR-023); the handed-off case is scored for classification (FR-018a); the `assistant_failed`-with-reply case is scored, not excluded (FR-007e); the disagreement list names each disagreeing case id with labelled and produced `{intent}` sequences side by side and never includes `gist` or produced text in any comparison (FR-013, FR-022); `cap_bound` cases are counted and listed (FR-024)
- [X] T023 [P] [US1] Write failing tests in `evals/harness/tests/test_report.py` over the T021 run: the report carries `conditions` and `selection` verbatim from `run.json` (FR-046, FR-047); alignment totals and their sum; exclusions counted per reason with `silenced_turn` and `cancelled_turn` distinct (FR-018, SC-006); `cases_needing_retry == 1` (FR-007c); `assistant_failed_with_reply` lists that case's id (FR-007e); scoring against a `labels.json` in which one case's intent was edited stops before computing anything and names that case, while one in which only a `gist` or `note` was edited scores normally (FR-044a); `drive_seconds` equals the sum of case `elapsed_seconds`, not `finished_at - started_at` (FR-047a); `score_seconds` is present on the report and scoring leaves `run.json` and every case file byte-identical; `not_computed` names every retrieval, serving and booking metric when only classification scorers are registered (FR-046); scoring the same run twice produces byte-identical JSON once `score_seconds` is excluded (SC-003); `render_summary(report)` produces a Markdown summary containing each metric's numerator/denominator (FR-048)
- [X] T024 [P] [US1] Create `evals/harness/tests/conftest.py` carrying what `services/chat/tests/conftest.py` does for the database and nothing else — migrate the chat test database once per session, isolate each xdist worker through `shared_db.testing` (`make test-unit` runs `-n 2`), and clear the `messages`/`chats` rows a test wrote — since that conftest does not apply outside `services/chat/tests`. Then write failing tests in `evals/harness/tests/driver/test_session.py` with `httpx.MockTransport`: `open_run_session` posts `POST /chats`, keeps the `visitdoc_session_id` cookie on the client, and returns the first chat id; `new_case_chat` posts `POST /chats` on the same cookie jar and returns a fresh chat id per call (FR-002, FR-003); `live_corpus` reads `GET /faq`; `chat_identity(db, chat_id)` reads `chats.session_id` and `chats.patient_id` scoped by that `session_id` (against the chat test database)
- [X] T025 [US1] Write failing tests (after T024, whose `conftest.py` they use) in `evals/harness/tests/driver/test_history.py` against the chat test database: `plant_history(db, chat_id, history, before)` inserts one `messages` row per entry with a fresh ULID, `sender` mapped `user → MessageSender.PATIENT` and `assistant → MessageSender.ASSISTANT`, content verbatim, strictly ascending `created_at` all earlier than `before`, and `request_outcomes`, `reply_to_message_ids`, `attention_mark` NULL (research R6); `GET /chats/{id}/messages` served by the chat app's `TestClient` then lists them in order
- [X] T026 [P] [US1] Write failing tests in `evals/harness/tests/driver/test_turn.py` with `httpx.MockTransport`: `post_turn` sends `{chat_id, message, local_now}` with the run clock and parses the NDJSON stream to its terminal event (`done` with `request_outcomes`/`answer_source`, `silent`, `cancelled`); a stream that ends without a terminal event is `error`; `read_thread` returns the stored patient message (with `attention_mark`) and the stored assistant reply for this turn, or none; `classify_attempt` returns `NOT_SENT` when the connection was never established or the service answered with a 429 or any 5xx status before a stream began — the only failures that prove the pipeline never ran; `SENT_NO_ANSWER` for a read timeout, or a stream cut off after it began, when no reply is stored and the patient message carries no `assistant_failed` mark; `MEASURED` for a turn that reached a terminal event, or whose patient message carries `assistant_failed`, with or without a reply; and `HARNESS_FAULT` for any other 4xx (a 404 for a chat the cookie does not own, a 422 for a malformed body) — a request the harness got wrong, which is neither a measurement nor a transient failure (FR-007a, FR-007b, FR-007d, research R9)
- [X] T027 [US1] Write failing tests in `evals/harness/tests/driver/test_run.py` with stubbed session/turn/logslice seams (no network): cases are driven strictly one after another — the stub asserts no second `post_turn` begins before the previous case file is written (FR-003a); every turn gets the same pinned `local_now`, default `2026-03-02T08:00:00` (FR-004); a run directory holding `cases/G001.json` resumes without driving G001 again (FR-007); a resumed run reuses `run.json`'s `session_id` (set as the `visitdoc_session_id` cookie, so no new session is minted), clock, selection and `entry_ids`, verifies the corpus pin again before its first turn, and stops without driving anything when the conditions `service_conditions` reads now, or the selected cases' label digests, differ from `run.json`'s (FR-003, FR-004, FR-044a, FR-047c); `NOT_SENT` and `SENT_NO_ANSWER` are re-driven at most 3 attempts total, each attempt in a **fresh chat** from `new_case_chat` (a retry in the same chat would put the failed attempt's stored patient message into the burst the classifier reads, and FR-002 gives every stored message exactly one case), `chat_id` records the attempt that was measured, `attempts` is recorded, and a case still failing after 3 is written with `run_error`; an `assistant_failed` turn with no stored reply is driven once and recorded as `run_error` (FR-007b), and one with a stored reply is driven once and recorded with no exclusion (FR-007e); a `HARNESS_FAULT` stops the run with the status and body, is never retried, and writes no case file — recording it as `run_error` would report a harness bug as a system failure; a corpus mismatch stops **before** the first `post_turn`, names the moved entries, and leaves no `run.json` on disk (FR-011, US2 AS5, SC-004); `require_json_log` is checked over the bytes appended since the run began, after the session is opened, and failing stops before the first `post_turn` with no case file written; `run.json`'s conditions are those `service_conditions` returns for the offset the run began at, and a log with no `service.configured` before that offset stops the run before the first `post_turn` (FR-047c); a `service.configured` inside a case's slice whose values differ from the run's stops the run without writing that case; a slice that cannot be selected records `missing_log_slice`; `hand_off` / `silent` / `cancelled` terminals record `handed_off_turn` / `silenced_turn` / `cancelled_turn`; the session is never deleted (FR-009); `elapsed_seconds` is recorded per case across attempts (FR-047b); `run.json` records the selection, the label digests and the conditions
- [X] T028 [P] [US1] Write failing tests in `evals/harness/tests/test_cli.py`: `run` accepts `--resume <run_id|path>` (mutually exclusive with `--cases`, `--family` and `--clock`, which a resumed run takes from its `run.json`), `--cases G001,G042`, `--family <name>`, `--artifacts <dir>` (default `.run/evals`), `--log <path>` (default `.run/chat.log`), `--base-url` (default `http://localhost:8000`), `--clock`; `score --run <run_id|path> [--labels <path>]` (default `evals/golden/cases.json`) scores from files alone with `httpx`, `grpc` and the DB engine patched to raise on use; `run` prints the summary after driving

Run T022–T028 and observe every one fail before starting implementation.

### Implementation for User Story 1

- [X] T029 [P] [US1] Implement `evals/harness/src/golden_harness/driver/session.py` (`open_run_session`, `new_case_chat`, `live_corpus`, `chat_identity` using `shared_db.create_engine`/`create_session_factory` on `chat`'s `DATABASE_URL`). Observe T024 pass
- [X] T030 [P] [US1] Implement `evals/harness/src/golden_harness/driver/history.py` (`plant_history`). Observe T025 pass
- [X] T031 [P] [US1] Implement `evals/harness/src/golden_harness/driver/turn.py` (`post_turn` streaming NDJSON through `ChatDoneEvent`/`ChatSilentEvent`/`ChatCancelledEvent`, `read_thread`, `classify_attempt`). Observe T026 pass
- [X] T032 [US1] Implement `evals/harness/src/golden_harness/driver/run.py`: `drive_run(selection, artifacts_dir, clock, ...)` and `resume_run(run_dir, ...)` (restore the session cookie, clock, selection and `entry_ids` from `run.json`; re-verify the pin; refuse on changed conditions or label digests) — validate labels, record the log offset, read the conditions with `service_conditions` at that offset, open the session, `require_json_log` over what the session's creation appended, `verify` the corpus and `map_entry_ids` (stop with no `run.json` on mismatch), write `run.json` with the label digests, then per case in order: skip if recorded, new chat (a fresh one on every attempt), plant history, record log offset, post turn, read thread, take the slice, stop on a changed `service.configured` inside it, derive the exclusion (decision 2), write the case file atomically, update `run.json`'s `cases` and `drive_seconds`. Re-drive `NOT_SENT` and `SENT_NO_ANSWER` per `classify_attempt`, bounded at 3; stop the run on `HARNESS_FAULT`. Store the slug→entry-id map in `run.json` so scoring needs no stack. Depends on T029–T031, T014, T016, T018. Observe T027 pass
- [X] T033 [US1] Implement `evals/harness/src/golden_harness/scoring/classification.py` (A1, A2, A3, disagreement list, `cap_bound` list) over `record.py` + `alignment.py` only. Observe T022 pass
- [X] T034 [US1] Implement `evals/harness/src/golden_harness/report.py`: `score_run(run_dir, labels) -> Report` (refuse labels whose digests differ from `run.json`'s, naming the cases (FR-044a); alignment totals with conservation, registered metric families, exclusions per reason, `cases_needing_retry`, `assistant_failed_with_reply`, `drive_seconds`, timed `score_seconds` carried on the report only — scoring never writes to `run.json` or `cases/`, so a re-score leaves the stored run, including the committed one, untouched (FR-044, FR-047a), `not_computed` list), `to_json(report)` with stable key order, `render_summary(report) -> str` (Markdown: conditions block first, alignment totals second, exclusions third, then metrics — the reading order `quickstart.md` prescribes). Writes `report.json` and `report.md` into the run directory. Observe T023 pass
- [X] T035 [US1] Implement `evals/harness/src/golden_harness/cli.py` and `evals/harness/src/golden_harness/__main__.py` (`run`, `score`). Observe T028 pass; confirm `uv run --package golden-harness -- python -m golden_harness score --run <tmp copy of evals/harness/tests/fixtures/runs/us1> --labels evals/harness/tests/fixtures/runs/us1/labels.json` prints the report — on a copy, since scoring writes `report.json`/`report.md` beside the run and the fixture directory is committed test data
- [X] T036 [US1] Run `make lint`, `make typecheck-python` and `uv run pytest evals/harness/tests packages/shared-logging/tests services/chat/tests/test_config.py services/chat/tests/test_logging.py services/chat/tests/test_main.py`; fix until green

**Checkpoint**: quickstart scenarios 3 (`CASES=G001`) and 4 (`CASES=G034` — the planted assistant
history precedes "Sure" in the thread) pass against the live stack started with
`LOG_FORMAT=json make services-up`. US1 is the MVP.

---

## Phase 4: User Story 2 — Score what retrieval found and what the turn served (Priority: P2)

**Goal**: hit@k and MRR per stage, the two zero-target serving metrics with their named requests,
and the six-value verdict distribution — computed from runs already stored.

**Independent Test**: `make eval-score RUN=<a US1 run>` (no new model calls) now reports B1, B2
per stage, C1, C2, C3, and still reports end-to-end task success and tool selection as not measured.

### Tests for User Story 2 (write first, confirm failing) ⚠️

- [X] T037 [P] [US2] Hand-write the recorded run `evals/harness/tests/fixtures/runs/us2/` (with its `labels.json`) whose case files carry `faq.retrieval_completed`, `faq.similarity_gate`, `faq.reranking_completed`, `faq.reranking_unavailable`, `turn.retrieval_skipped_empty_corpus` events with `segment` and `request_outcomes`, covering: an answerable request whose cited entry's chunk is 2nd in `candidates` and 1st in `scores` (AS1); the same shape with an abstained verdict (AS2); a gap label abstained (AS3); `faq.reranking_unavailable` with verdict `answered_unreranked` (AS4); a `no_search` turn; an unaligned case with an answerable label (lost to count mismatch); a handed-off `faq_question + call_staff` case (excluded from C1 by name, FR-031a); a `faq_question + unknown` case answered (stays in C1's denominator); an `answered` verdict on a labelled gap (SC-010); a request with two cited entries where only the second is ranked; an answerable request whose only cited chunk is ranked in `candidates` but absent from `faq.similarity_gate.kept` (dropped by the floor), so it never reaches `faq.reranking_completed`; a two-FAQ-request turn whose segments interleave in the event list
- [X] T038 [P] [US2] Write failing tests in `evals/harness/tests/scoring/test_retrieval.py` over T037: per-stage ranked lists come from `candidates` (similarity) and `scores` sorted by descending `rerank_score` (rerank), each joined to its request by `segment`; AS1 is a hit at k=3 and k=5 but not k=1 and contributes 0.5 to similarity MRR; a request with no cited chunk ranked contributes 0 to MRR; only aligned `faq_question` requests with `answerable: true` are scored (FR-028); `reranker_unavailable` excludes from the rerank stage only and the request is still scored for similarity (FR-029); `no_search` excludes from both; the rerank stage scores only requests with at least one cited chunk in that segment's `faq.similarity_gate.kept`, and the gate-dropped request is excluded from it as `not_reached_reranker` while still scored for similarity (FR-029a); similarity-gate survival is the share of similarity-scored requests with a cited chunk in `kept`, counting the gate-dropped request as not surviving; rerank-stage hit@5 is published with the statement that it is 1 by construction while `similarity_cap` ≤ 5, the cap read from the run's conditions; cited slugs are compared through the run's stored slug→entry-id map (FR-012); the two stages are never pooled (FR-025)
- [X] T039 [P] [US2] Write failing tests in `evals/harness/tests/scoring/test_serving.py` over T037: C1's denominator is labelled-answerable FAQ requests in non-excluded cases minus handed-off/silenced cases, which are counted and listed by case id (FR-031a, SC-006a); C1's numerator breaks down into `abstained` and `lost_to_count_mismatch` (FR-031); C2's denominator is every abstention produced and its numerator those aligned to an answerable label (FR-032); `answered_unreranked` counts as answered for C1 and C2 and is reported under `degraded_answers` (FR-035); both C1 and C2 list case id and question (the produced `RequestOutcome.question`, or the patient message for an unaligned request) behind every non-zero value, with the stopping gate named for an abstention (FR-034, AS2); `answers_on_labelled_gaps` lists the SC-010 case; the verdict distribution has an entry for all six `FaqVerdict` values including zeros (FR-036)
- [X] T040 [US2] Extend `evals/harness/tests/test_report.py`: with the retrieval and serving scorers registered, B1/B2/B3/C1/C2/C3 appear with numerator, denominator and exclusions; the report states in text that C1 and C2 share part of a numerator and differ in denominator, and publishes both denominators (FR-033); `not_computed` now names only the booking metrics. Observe T038–T040 fail

### Implementation for User Story 2

- [X] T041 [P] [US2] Implement `evals/harness/src/golden_harness/scoring/retrieval.py` (per-stage ranked lists by `segment`, hit@{1,3,5}, MRR, similarity-gate survival, stage exclusions including `not_reached_reranker`). Observe T038 pass
- [X] T042 [P] [US2] Implement `evals/harness/src/golden_harness/scoring/serving.py` (C1 with cause breakdown and FR-031a exclusion list, C2, degraded answers, answers on labelled gaps, verdict distribution). Observe T039 pass
- [X] T043 [US2] Register both scorers in `evals/harness/src/golden_harness/report.py`, add the FR-033 statement, the rerank-stage hit@5 statement (FR-029a) and the retrieval/serving sections to `render_summary`. Observe T040 pass
- [X] T044 [US2] If `log-events.md` (008/010) disagrees with any field the scorers read from a real JSON log line, record the disagreement as a finding in `specs/012-golden-set-metrics/research.md` under a new "Contract findings" heading rather than accommodating it silently (`contracts/log-access.md`); run `make lint`, `make typecheck-python`, `uv run pytest evals/harness/tests`

**Checkpoint**: re-scoring the US1 checkpoint run spends nothing and now reports retrieval and
serving; quickstart scenario 6 (`FAMILY=overriding-segment`) reports C1 over a denominator of 1
with six cases excluded by name.

---

## Phase 5: User Story 3 — Score whether the booking actually landed (Priority: P3)

**Goal**: scheduling fixtures on the 18 booking cases, planted and read through the scheduler's
gRPC contract; end-to-end task success under perfect matching; tool selection per booking half.

**Independent Test**: `make eval-run CASES=G042` plants one appointment at `+1d`, and the report
shows end-to-end task success 1/1; changing the fixture's expected status to `standing` and
running the case again reports a failure naming the unmatched expectation and the unaccounted appointment.

### Tests for User Story 3 (write first, confirm failing) ⚠️

- [X] T045 [P] [US3] Write failing tests in `evals/harness/tests/test_fixture_label.py` (`contracts/fixture-label.md`): `evals/golden/schema.json` accepts the G102 example verbatim; rejects `status` on a `given` entry, a missing `status` on an `expect` entry, a `day` not matching `^[+-][0-9]+d$`, a `time` not matching `^[0-9]{2}:[0-9]{2}$`, and an unknown key; `load_cases` rejects a case carrying a booking request with no `scheduling` and a case with `scheduling` but no booking request (data-model rule 1); `validate_plantable(fixture, clock)` using `scheduler`'s `SESSION_SEED` (pool names `William Osler`, `Andreas Vesalius` in seed order) and `BOOKING_HORIZON_DAYS` rejects a `given` outside the practitioner's weekly range, off the 60-minute grid, not strictly after the clock, or beyond the horizon, and rejects two `given` entries overlapping for one practitioner (rules 2, 3); the committed `evals/golden/cases.json` has `scheduling` on exactly `G042 G044 G048 G049 G050 G051 G053 G062 G087 G088 G089 G090 G091 G092 G093 G094 G095 G102` and every one validates against the default clock (SC-008)
- [X] T046 [P] [US3] Write failing tests in `evals/harness/tests/scoring/test_booking.py` with hand-written recorded cases in `evals/harness/tests/fixtures/runs/us3/`: matching resolves `day` against the run clock's date; an `expect` entry without `time` matches any start that day (AS1); a cancelled planted appointment matches `status: cancelled` (AS2); a read-only case whose turn booked something fails with that appointment listed as unaccounted (AS3); the matching is exhaustive — an ordering of `expect` entries that would fool a greedy matcher still matches; a failure lists `unmatched_expected` and `unaccounted_appointments` separately (FR-041a); a case with two booking requests is scored **once** for tool selection against the union of labelled tools, and the report text says tool selection is per booking half (AS4, FR-042); an extra `check_availability` call is not a miss, a labelled tool never called is (FR-043); tools are read from `booking.tool_called.tool_name`; `unresolvable_fixture` cases are excluded from D1 and D2, never failures
- [X] T047 [P] [US3] Write failing tests in `evals/harness/tests/driver/test_scheduling.py` with a fake `SchedulingServiceStub`: `resolve_roster(stub, session_id)` maps pool full names to practitioner ids through `ListPractitioners`; a fixture naming a name not on the roster returns `unresolvable_fixture` and **no** `BookAppointment` is sent (AS5, FR-039); `plant(stub, session_id, patient_id, given, clock)` sends one `BookAppointment` per entry with `starts_at` = clock date + `day` at `time`, formatted `YYYY-MM-DDTHH:MM:SS`, and `local_now` = the clock; a `BookAppointment` returning a typed booking failure, or an rpc error, yields `unresolvable_fixture` with the failure recorded; `read_post_state(stub, ...)` calls `ListAppointments` with the widest `TimeFilter` and `StatusFilter` the proto defines (all times, standing and cancelled) and maps each `Appointment` to `AppointmentState{practitioner_full_name, starts_at, status}`; `release(stub, session_id, patient_id, clock)` lists the patient's standing appointments and sends one `CancelAppointment` per appointment with `appointment_id`, `expected_starts_at` and `expected_practitioner_id` taken from that listing and `local_now` = the clock, returns what it cancelled, treats a `no_change` response as already cancelled, and raises `CleanupFailed` naming the appointment on a `ChangeFailure` or an rpc error (FR-041b)
- [X] T048 [US3] Extend `evals/harness/tests/driver/test_run.py`: for a fixture-carrying case, the roster is resolved and preconditions planted **before** the log offset is recorded and the turn posted, and the post-state is read after the terminal event and stored as `scheduling_after`; an unresolvable fixture writes the case as `unresolvable_fixture` without posting a turn; a case with no fixture never touches the stub; for a fixture-carrying case `NOT_SENT` is re-driven in a fresh chat as before, while `SENT_NO_ANSWER` is **not** retried — the case is written as `outcome_unknown` with `attempts` as it stands, and its patient's appointments are read and stored as `scheduling_after` when the read succeeds and left absent when it fails; a case with no fixture is still re-driven on `SENT_NO_ANSWER` (FR-007d); `release` runs after the post-state is read — after the read is attempted for `outcome_unknown` — and before the case file is written, for **every** case whose chat has a patient, and its result is stored as `cancelled_after`; a non-fixture case whose patient holds a standing appointment has it cancelled and recorded; a `CleanupFailed` writes the case and then stops the run before the next case; a resumed run calls `release` for every recorded case's `patient_id` before driving a new case, and stops if any fails (FR-041b). In the same task, change T040's assertion in `evals/harness/tests/test_report.py` so that, with the booking scorers registered, `not_computed` is empty for a full-selection run and end-to-end task success and tool selection appear with the per-booking-half statement — the test moves before the code that satisfies it (constitution VIII). Observe T045–T048 fail

### Implementation for User Story 3

- [X] T049 [US3] Extend `evals/golden/schema.json` with the optional `scheduling` property (`given`/`expect` arrays of `AppointmentRef`, patterns and `status` rules as `contracts/fixture-label.md` states, `additionalProperties: false`)
- [X] T050 [US3] Label the 18 booking cases in `evals/golden/cases.json` by hand against the Monday `2026-03-02T08:00` clock and the two-practitioner roster (research R7), following `contracts/fixture-label.md`'s worked examples for G042, G049, G088, G102: a fixture describes what **one turn** should leave behind; read-only tools restate `given`; G049/G053 have `given: []`, `expect: []`; leave `tools` labels untouched. Add a section to `evals/golden/PROVENANCE.md` giving each case's fixture a one-line reason, and add the field to `evals/golden/README.md`'s field list
- [X] T050a [US3] Review gate (FR-038a): present the 18 fixtures to the user — for each case its message, `given`, `expect` and the one-line reason from `evals/golden/PROVENANCE.md` — and wait for their approval; apply any corrections they ask for and re-run `uv run pytest evals/harness/tests/test_fixture_label.py`; record who approved and on what date in `evals/golden/PROVENANCE.md`. This is a person's review: no agent may mark it done on its own, and T063 does not start until it is recorded
- [X] T051 [US3] Extend `evals/harness/src/golden_harness/cases.py` with `SchedulingFixture`, `AppointmentRef`, the booking-iff-fixture rule and `validate_plantable` (importing `SESSION_SEED` and `PHYSICIAN_POOL` order from `scheduler`, `BOOKING_HORIZON_DAYS` from `scheduler.core.config.Settings` defaults). Observe T045 pass
- [X] T052 [P] [US3] Implement `evals/harness/src/golden_harness/driver/scheduling.py` (`scheduling_stub(target)` on `chat`'s `SCHEDULING_GRPC_TARGET`, `resolve_roster`, `plant`, `read_post_state`, `release`). Observe T047 pass
- [X] T053 [P] [US3] Implement `evals/harness/src/golden_harness/scoring/booking.py` (exhaustive perfect matching, D2 with both failure halves, D1 per booking half over `booking.tool_called`). Observe T046 pass
- [X] T054 [US3] Wire fixtures into `evals/harness/src/golden_harness/driver/run.py` (resolve + plant before the offset, read after the terminal event, `unresolvable_fixture` without posting, `outcome_unknown` without retrying on `SENT_NO_ANSWER`, `release` after every case and before resuming) and register the booking scorers in `evals/harness/src/golden_harness/report.py` with the per-booking-half statement in `render_summary`; `not_computed` is now empty for a full-selection run. Observe T048 and the `test_report.py` assertion T048 changed pass
- [X] T055 [US3] Run `make lint`, `make typecheck-python`, `uv run pytest evals/harness/tests`

**Checkpoint**: quickstart scenario 5 (`CASES=G042`, then the case run again under the corrupted
`standing` expectation, since re-scoring refuses a changed label — FR-044a) behaves as described.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: documentation the constitution requires, the gates, and the phase's frozen record.

- [X] T056 [P] Write `evals/harness/README.md`: what the harness is and is not (quickstart's three-way table), prerequisites (`LOG_FORMAT=json make services-up`, `make migrate`), the commands, the artifact layout, the driver/scorer/reporter split and why scoring is pure, where a run's conditions come from (the service's `service.configured` event, FR-047c), the cleanup between cases and why the scheduler shows nothing standing after a run (FR-041b), and what stops a run, and the exclusion reasons — claiming nothing the tests do not demonstrate
- [X] T057 [P] Add a "Metrics Over the Golden Set: technology choices" section to `README.md` following the existing sections' shape: HTTP seam vs in-process, JSON log renderer vs parsing the console, per-case files vs one aggregate, re-taken corpus pin, a startup settings event vs the harness's own environment, strictly sequential runs, and their tradeoffs
- [X] T058 [P] Update `.claude/CLAUDE.md`: add `evals/harness/` (uv member `golden-harness`) and `evals/golden/` to "Repository layout"; add `make eval-run` / `make eval-score` and `LOG_FORMAT=json` to "Commands"; one line under "Key design decisions to preserve" that scoring is a pure function of a stored run and a handed-off turn is scored for classification and excluded from retrieval and serving (FR-018a)
- [X] T059 [P] Update `docs/testing-strategy.md`: `evals/harness/tests` is in the unit tier; `make eval-run` is not a test tier and makes live calls
- [X] T060 [P] Update `scripts/dev-services.sh`'s header comment to note that an exported `LOG_FORMAT=json` is inherited by the chat process and is what the harness requires
- [X] T061 Run the full gates: `make precommit` and `make test-unit`; fix until green
- [X] T062 Quickstart scenarios 1 and 2 (free): the label-load tests pass; editing one character of a `DEFAULT_FAQ_ENTRIES` text in `services/chat/src/chat/rag/default_corpus.py`, restarting chat, and starting `make eval-run CASES=G001` stops before any turn, names the entry, and leaves no `run.json` under `.run/evals/`. Revert the edit and restart chat
- [X] T063 Quickstart scenario 7, once T050a's approval is recorded: take the first full run, `make eval-run` over all 135 cases with the stack started as `LOG_FORMAT=json make services-up`. Confirm 135 case files and alignment totals summing to 190 (SC-002); if interrupted, resume with `uv run --package golden-harness -- python -m golden_harness run --resume <run_id>`
- [X] T064 Quickstart scenario 8: with the stack **down** (`make services-down`), `make eval-score RUN=<the T063 run>` and confirm `report.json` is byte-identical apart from `score_seconds` (SC-003)
- [X] T065 Commit the T063 run as the phase's frozen record (FR-048a): copy `run.json`, `cases/`, `report.json` and `report.md` into `specs/012-golden-set-metrics/evaluation/`, and add `specs/012-golden-set-metrics/evaluation/README.md` stating it is evidence, not a baseline or threshold, and naming the conditions it was measured under. No other run is committed (FR-048b)
- [X] T066 Record what the T063 report found in `specs/012-golden-set-metrics/evaluation/README.md` — the non-zero zero-target requests, `cap_bound` turns, the G020 re-measurement result (SC-010), label disagreements as findings for a person to adjudicate — without re-labelling anything or moving any threshold (spec Out of Scope)
- [X] T067 Correct the two documents that contradicted the handed-off decision: `specs/012-golden-set-metrics/contracts/metrics.md` and SC-006 in `specs/012-golden-set-metrics/spec.md` now say a handed-off turn is scored for classification and excluded from the retrieval and serving metrics only (FR-018a) — done while recording the 2026-09-14 decisions

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: none. T001 → T002 → T003 → T004; T005 alongside.
- **Foundational (Phase 2)**: after Setup; blocks every story. Within it, A (T006–T009) and A2
  (T009a–T009b) are independent of B–F; B, C, D, E, F are mutually independent test-first pairs, except T013 must land
  before T012 can pass and F's purity test (in T020) needs T016's module to exist.
- **US1 (Phase 3)**: after Foundational.
- **US2 (Phase 4)**: after US1 — it registers scorers into US1's `report.py` and is exercised on
  US1's stored runs. Its scoring work (T037–T042) needs only Foundational and could start beside
  US1 if `report.py` integration (T043) waits.
- **US3 (Phase 5)**: after US1 (`driver/run.py`, `report.py`). Independent of US2; T049–T051 (the
  label) can start right after Foundational.
- **Polish (Phase 6)**: after the stories intended for the first committed run — FR-048a's run
  needs all three, so T063–T066 need US3 done and T050a's approval recorded.

### Within each story

Recorded fixture run → tests → **observed failing** → driver/scoring modules → `run.py`/`report.py`
wiring → tests observed passing → gates.

### Parallel opportunities

- T005 beside T001–T004.
- Foundational groups A–F (T006, T010, T012, T015, T017, T019 are all `[P]` test tasks in
  different files).
- US1: T021–T024, T026 and T028 together, then T025 once T024's `conftest.py` exists; T029–T031 together.
- US2: T037–T039 together; T041–T042 together.
- US3: T045–T047 together; T052–T053 together.
- Polish: T056–T060 together.

---

## Parallel Example: User Story 1

```bash
# Tests first, in parallel (different files):
Task: "T022 classification tests in evals/harness/tests/scoring/test_classification.py"
Task: "T023 report tests in evals/harness/tests/test_report.py"
Task: "T024 session tests in evals/harness/tests/driver/test_session.py"
Task: "T026 turn tests in evals/harness/tests/driver/test_turn.py"

# Then the independent driver modules, in parallel:
Task: "T029 driver/session.py"
Task: "T030 driver/history.py"
Task: "T031 driver/turn.py"
```

---

## Implementation Strategy

### MVP first (User Story 1)

1. Phase 1 Setup, Phase 2 Foundational (including the JSON renderer — without it nothing can be
   read).
2. Phase 3 US1 → validate with quickstart scenarios 3 and 4 against the live stack.
3. Stop: classification is now falsifiable, and every run taken from here on is re-scorable for
   free by later stories — except a run selecting a booking case, whose digest T050's fixture changes
   (FR-044a).

### Incremental delivery

1. US1 → classification metrics over real turns.
2. US2 → re-score US1's stored runs for retrieval and serving at no cost.
3. US3 → fixtures, then the first full run (T063) and its frozen record (T065).

---

## Notes

- `[P]` = different files, no dependency on an unfinished task.
- Constitution VIII: a test task's completion includes seeing it fail; do not write the module first.
- New mechanisms (log-slice selection, resume, bounded retry, conservation) each have one test per
  invariant stated in the task — keep it that way when extending them.
- Commit after each checkpoint.

## Phase 7: Convergence

- [X] T068 Give a request aligned by count but produced under a non-FAQ intent its own request-level exclusion reason instead of `no_search`, which data-model.md reserves for an empty corpus, in `evals/harness/src/golden_harness/record.py` and `evals/harness/src/golden_harness/scoring/retrieval.py`, with `evals/harness/tests/scoring/test_retrieval.py`, `evals/harness/tests/test_record.py` and `evals/harness/README.md` updated per FR-018 (contradicts)
- [X] T069 Check `restarts_in` over every log byte range since the last check — before the first turn, between attempts, and before writing any case, including attempts recorded without a slice — in `evals/harness/src/golden_harness/driver/run.py`, with a test per window in `evals/harness/tests/driver/test_run.py` per FR-047c (partial)
- [X] T070 Store the unplantable failure detail on `CaseRun` and list excluded fixture cases with it in the report, in `evals/harness/src/golden_harness/record.py`, `evals/harness/src/golden_harness/driver/run.py` and `evals/harness/src/golden_harness/report.py`, with tests, so a label problem and a scheduler outage are distinguishable per FR-039, FR-018 (partial)
- [X] T071 Wait, within a bound, for a `SENT_NO_ANSWER` turn to settle before releasing its patient, and stop the run if it never settles, in `evals/harness/src/golden_harness/driver/run.py` with tests in `evals/harness/tests/driver/test_run.py` per Edge Case "An earlier case's appointment in a later case's way", FR-041b (partial)
- [X] T072 Review and record in `specs/012-golden-set-metrics/contracts/run-record.md` and `tasks.md`, or replace with a spec-named outcome, the `PostStateUnreadableError` stop in `evals/harness/src/golden_harness/driver/run.py` per contracts/run-record.md (unrequested)
- [X] T073 Review and record in `specs/012-golden-set-metrics/contracts/metrics.md` §C1, or remove, the `misclassified` unserved cause in `evals/harness/src/golden_harness/scoring/serving.py` per FR-031 (unrequested)
- [X] T074 Review and record as decisions, or remove, the unrequested driver behaviours — `run_error` for a cleanup failed between attempts, `MEASURED` for a broken stream with a stored reply and no mark, `EntryIdsChangedError` on resume, `SessionError` when a case chat mints a new session, unseeded `expect` names rejected, `--labels` on `run` — in `evals/harness/src/golden_harness/driver/run.py`, `driver/turn.py`, `driver/session.py`, `cases.py`, `cli.py` per tasks.md (unrequested)
- [X] T075 Decide whether a log-contract or outcome-invariant break in one case fails the whole scoring pass or excludes that case, and record it in `specs/012-golden-set-metrics/contracts/log-access.md`, for `evals/harness/src/golden_harness/scoring/retrieval.py` and `scoring/serving.py` per FR-044 (unrequested)
- [X] T076 Complete `evals/harness/README.md`'s "What stops a run" list and add `httpx.HTTPError` to `_REPORTED_FAILURES` in `evals/harness/src/golden_harness/cli.py`, with a test in `evals/harness/tests/test_cli.py`, per FR-048 (partial)

## Phase 8: Convergence

- [X] T077 Release the attempt's patient before re-raising every stop that follows the chat's identity read (`TurnProtocolError`, `ThreadReadError`, `httpx.HTTPError`, a segmentation `ValidationError`), and on resume release every patient of the run session's chats read from the chat database scoped by `session_id`, not only recorded cases', in `evals/harness/src/golden_harness/driver/run.py`, with a test per stop in `evals/harness/tests/driver/test_run.py` and the overclaiming docstring in `evals/harness/src/golden_harness/driver/scheduling.py` corrected, per FR-041b and Edge Case "An earlier case's appointment in a later case's way" (partial)
- [X] T078 When a fixture case's turn was sent and a stream-protocol or thread-read failure then stops the run, attempt the post-state read and release and write the case as `outcome_unknown` before stopping, so a resume never re-posts it, in `evals/harness/src/golden_harness/driver/run.py`, with tests in `evals/harness/tests/driver/test_run.py` and `evals/harness/README.md` §"What stops a run" updated, per FR-007d, FR-007b (contradicts)
- [X] T079 Detect a log that was truncated in place and grew back past the saved offset (e.g. by fingerprinting the bytes before the offset) and re-read it from the start, in `evals/harness/src/golden_harness/driver/logslice.py` and `evals/harness/src/golden_harness/driver/run.py`, with a test in `evals/harness/tests/driver/test_logslice.py` and the `_RestartWatch` docstring aligned, per FR-047c (partial)

## Phase 9: Convergence

- [X] T080 Raise `ThreadReadError` from `read_thread` when the thread body does not parse, so a posted fixture case is released and written as `outcome_unknown` and any case's patient is released before the stop, in `evals/harness/src/golden_harness/driver/turn.py` and `evals/harness/src/golden_harness/driver/run.py`, with tests in `evals/harness/tests/driver/test_turn.py` and `evals/harness/tests/driver/test_run.py` per FR-007d, FR-041b (contradicts)
- [X] T081 For a fixture case, read the post-state, release the patient and write the case as `outcome_unknown` before re-raising a segmentation `ValidationError`, in `evals/harness/src/golden_harness/driver/run.py`, with a test in `evals/harness/tests/driver/test_run.py` and `evals/harness/README.md` §"What stops a run" updated, per FR-007d (contradicts)
- [X] T082 Record in `specs/012-golden-set-metrics/contracts/log-access.md` whether a posted fixture case caught by a restart with other settings stays unwritten (re-posted on resume) or is written as `outcome_unknown`, and make `evals/harness/src/golden_harness/driver/run.py` and `evals/harness/tests/driver/test_run.py` follow it, per FR-007d vs contracts/log-access.md (contradicts)
- [X] T083 State when `terminal`, `patient_message` and `scheduling_after` are absent (`unresolvable_fixture`; an `outcome_unknown` case whose stream or thread was not read) in `specs/012-golden-set-metrics/contracts/run-record.md` and `specs/012-golden-set-metrics/data-model.md` per contracts/run-record.md (partial)

## Phase 10: Scripted reply turn (amendment, 2026-09-14)

**Purpose**: decision 10. The seven cases whose message asks for a write (G042 G051 G088 G090 G093
G095 G102) gain a scripted `reply`; the driver posts it as a second full turn and reads the calendar
after each turn; booking scoring checks both reads and counts both turns' tool calls.

- [X] T084 Write failing tests in `evals/harness/tests/test_fixture_label.py`: `evals/golden/schema.json` accepts an optional non-empty string `reply` on `scheduling` and rejects a non-string or empty one; `SchedulingFixture.reply` loads; a fixture with a `reply` changes its case's label digest (FR-044a); the committed `evals/golden/cases.json` carries a `reply` on exactly G042 G051 G088 G090 G093 G095 G102 (FR-037b)
- [X] T085 Add `reply` to the `scheduling` object in `evals/golden/schema.json` and to `SchedulingFixture` in `evals/harness/src/golden_harness/cases.py`; observe T084's schema and model tests pass
- [X] T086 Write the seven replies into `evals/golden/cases.json` — G042 G051 G090 G093 "Yes, please go ahead."; G088 "William Osler, please. Yes, book it."; G095 "William Osler, please. The earliest time you have on Friday is fine, yes please book it."; G102 "Yes, please cancel Friday. The earliest Monday time with William Osler is fine." — leaving every other field untouched, and replace the confirmation-rule question in `evals/golden/PROVENANCE.md`'s draft-fixtures section with the decision (reply turn, FR-037b), adding the field to `evals/golden/README.md`'s list; observe T084 pass
- [X] T087 Write failing tests in `evals/harness/tests/test_record.py`: `CaseRun.reply_turn` (terminal, patient_message, assistant_message, events) and `CaseRun.scheduling_before_reply` round-trip and are optional — whether a case's fixture has a reply is a label fact the record cannot see, so the record asserts shape and optionality only, and the driver and scorer tests own the rest (FR-037b)
- [X] T088 Add `ReplyTurn`, `CaseRun.reply_turn` and `CaseRun.scheduling_before_reply` to `evals/harness/src/golden_harness/record.py`; observe T087 pass
- [X] T089 Write failing tests in `evals/harness/tests/driver/test_run.py` for a reply case: after the first turn completes with a `done` reply, the patient's appointments are read and stored as `scheduling_before_reply`, then the reply is posted in the same chat with the run clock, its log slice taken by the same offset + `turn_id` rule, its thread read, and only then the post-state read, release and case write; the reply is not posted when the first turn is handed off, silenced, cancelled or failed without a reply, and the case keeps that turn's exclusion; a `NOT_SENT` on either turn retries the whole exchange in a fresh chat; `SENT_NO_ANSWER` on the reply records `outcome_unknown` without retrying; an `assistant_failed` reply turn without a stored reply is `run_error`; a case without a reply drives exactly one turn as before (FR-037b, FR-007b, FR-007d, FR-041b)
- [X] T090 Drive the reply turn in `evals/harness/src/golden_harness/driver/run.py`, reusing the existing turn, thread, slice, restart-check and release machinery; observe T089 pass
- [X] T091 Write failing tests in `evals/harness/tests/scoring/test_booking.py` and `evals/harness/tests/test_report.py` with hand-written reply cases in `evals/harness/tests/fixtures/runs/us3/`: D2 succeeds only when `scheduling_before_reply` matches `given` as standing AND `scheduling_after` matches `expect`; a first turn that already cancelled fails with the failing read named; a correct first turn whose reply wrote nothing fails naming the second read; D1 unions `booking.tool_called` across `events` and `reply_turn.events`, so a `cancel_appointment` called only in the reply turn is a hit; classification and retrieval ignore `reply_turn`; the report summary says tool selection spans both turns of a reply case (FR-041, FR-041a, FR-042)
- [X] T092 Implement both reads in D2 and the two-turn union in D1 in `evals/harness/src/golden_harness/scoring/booking.py`, and the statement and read-naming failure detail in `evals/harness/src/golden_harness/report.py`; observe T091 pass
- [X] T093 Update `evals/harness/README.md` (the reply turn, the two reads, what `reply_turn` and `scheduling_before_reply` hold), then run `uv run pytest -q evals/harness/tests`, `make lint`, `make typecheck-python`

## Phase 11: Booking messages that need no clarification (amendment, 2026-09-14)

**Purpose**: FR-038b. Six messages left the practitioner open or named the clock's own weekday.

- [X] T094 Add failing guards in `evals/harness/tests/test_fixture_label.py` — G044 G062 G088 G094 G095 G102 name William Osler, and no booking case's message or reply names the run clock's weekday — and bring its copies of the G102 example, the seven replies and G088's expectation to the decided labels; observed 12 failing (FR-038b)
- [X] T095 Reword G044 G062 G088 G094 G095 G102 in `evals/golden/cases.json` (William Osler named; Monday → Wednesday, `+7d` → `+2d` for G088 and G102's new booking; G088, G095 and G102 replies no longer name the practitioner; G094 stays read-only); observed T094 pass (FR-038b)
- [X] T096 Record the rewording in `evals/golden/PROVENANCE.md`, the labeller rules and worked examples in `specs/012-golden-set-metrics/contracts/fixture-label.md`, and the quoted cases in spec FR-037a and `specs/012-golden-set-metrics/data-model.md` (FR-038b)

## Phase 12: Decisions of 2026-09-14, second round

**Purpose**: G062 gains a reply (FR-037b); a handed-off reply turn is `handed_off_turn` (FR-037b,
FR-018a); T071 is decided as a bounded wait (FR-041c); T082 is decided as re-post on resume
(FR-007d, recorded in `contracts/log-access.md` — the driver already behaves so, and T082 is closed
by that record).

- [X] T097 Change `evals/harness/tests/test_fixture_label.py` so the committed set carries a reply on exactly eight cases (G062 with "The earliest Wednesday time is fine, yes please book it.") and G062 expects one standing William Osler appointment at `+2d`, any time; observe failing, then add the reply and expectation to G062 in `evals/golden/cases.json` and its row in `evals/golden/PROVENANCE.md`; observe pass (FR-037b)
- [X] T098 Write failing tests in `evals/harness/tests/driver/test_run.py` that a reply turn ending in a `done` with `answer_source="hand_off"` records `handed_off_turn` on the case, and in `evals/harness/tests/scoring/` that such a case is scored for classification and booking and excluded from retrieval and serving; then implement in `evals/harness/src/golden_harness/driver/run.py` (and the scorers if they need it); observe pass (FR-037b, FR-018a)
- [X] T099 Implement T071 as FR-041c: failing tests in `evals/harness/tests/driver/test_run.py` that a `SENT_NO_ANSWER` turn (first or reply) is polled until the thread holds a stored reply to its patient message or the `assistant_failed` mark, then read and released; that polling is bounded by a named constant with an injectable clock/sleep so tests spend no real time; and that an unsettled turn writes the case as `outcome_unknown` and stops the run; then implement in `evals/harness/src/golden_harness/driver/run.py` (and `driver/turn.py` if a settle check belongs there); observe pass; mark T071 done
- [X] T100 Update `evals/harness/README.md` (reply-turn hand-off, the settle wait and its bound, re-post on resume after a restart), then run `uv run pytest -q evals/harness/tests`, `make lint`, `make typecheck-python`

## Phase 13: Settle wait details (amendment, 2026-09-14)

**Purpose**: FR-041c as refined — 5 s interval, 1 min bound; a mid-answer contract break waits too;
a turn that settles is scored as completed; each poll checks for a restart.

- [X] T101 Change the bound in `evals/harness/src/golden_harness/driver/run.py` to `SETTLE_INTERVAL_SECONDS = 5.0` and `SETTLE_TIMEOUT_SECONDS = 60.0`, with the constants test in `evals/harness/tests/driver/test_run.py` changed first and observed failing (FR-041c)
- [X] T102 Write failing tests in `evals/harness/tests/driver/test_run.py` that a `TurnProtocolError` raised mid-stream by `post_turn` (first turn or reply) settles before release exactly as `SENT_NO_ANSWER` does, then implement in `evals/harness/src/golden_harness/driver/run.py` (FR-041c)
- [X] T103 Write failing tests in `evals/harness/tests/driver/test_run.py` that a turn which settles with a stored reply or the `assistant_failed` mark is then judged as a completed turn — thread, log slice and post-state read, exclusion derived as for any completed turn (a stored reply scored; `assistant_failed` without a reply `run_error`), a reply case continuing to its reply turn — and is never recorded `outcome_unknown` or retried; then implement in `evals/harness/src/golden_harness/driver/run.py` (FR-041c, FR-007b, FR-037b)
- [X] T104 Write failing tests in `evals/harness/tests/driver/test_run.py` that a `service.configured` with other settings appearing while a turn is being waited on stops the run on that poll — not after the bound — leaving the case unwritten as FR-007d's restart exemption says; then implement; update `evals/harness/README.md` for T101–T104 and run `uv run pytest -q evals/harness/tests`, `make lint`, `make typecheck-python` (FR-041c, FR-047c)

## Phase 14: What a settled turn lost (amendment, 2026-09-15)

**Purpose**: FR-041d. A settled turn has no terminal event; read its hand-off from `turn.completed`,
and make a contract-breaking stream visible in the report.

- [X] T105 Write failing tests in `evals/harness/tests/driver/test_run.py` that a turn which settled without a terminal event and whose log slice's `turn.completed` has `outcome: "handed_off"` is treated as handed off — a first turn posts no reply and keeps booking scored, a reply turn records `handed_off_turn` — while one with any other outcome is not, and one whose slice has no `turn.completed` is judged as today; then implement in `evals/harness/src/golden_harness/driver/run.py` (reading the field name from `services/chat/src/chat/agent/compose_answer.py` `_OUTCOME_BY_SOURCE`) (FR-041d, FR-037b, FR-018a)
- [X] T106 Write failing tests in `evals/harness/tests/test_report.py` that a case whose first or reply turn broke the stream contract and then settled is listed by id with the turn in `report.json` and the summary, and a case whose stream was merely timed out or cut off is not; record on the case whatever the scorer needs to tell a contract break from a cut-off (in `evals/harness/src/golden_harness/record.py`, set in `driver/run.py`) without inventing a terminal event; then implement in `evals/harness/src/golden_harness/report.py`; update `evals/harness/README.md`, `specs/012-golden-set-metrics/contracts/run-record.md` and `data-model.md` for any new field, and run `uv run pytest -q evals/harness/tests`, `make lint`, `make typecheck-python` (FR-041d)

