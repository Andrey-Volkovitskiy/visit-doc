# visit-doc
A conversational AI-assistant for a for medical clinics that automates appointment booking via chat and get grounded answers to policy/FAQ questions.

See `docs/ROADMAP.md` for the full design and phased build plan.

## Repository layout

A `uv`-workspace monorepo:

```
services/
├── chat/          # FastAPI core backend: agent, RAG, chat, auth
├── scheduler/      # FastAPI + own Postgres, gRPC server
└── frontend/       # React + Vite SPA
packages/
├── shared-models/  # cross-service Pydantic schemas
└── shared-proto/   # chat<->scheduler gRPC contract
```

## Installation

Prerequisites: [`uv`](https://docs.astral.sh/uv/) (Python 3.12 is installed automatically by `uv`
if you don't have it).

```bash
uv sync                    # install every service/package into one shared venv
uv run pre-commit install  # install git hooks (lint/type-check before each commit)
```

## Getting started

The `chat` service needs a local Postgres and Qdrant (`docker-compose.yml`, repo root), and a
`.env` file at the repo root with `DATABASE_URL`, `QDRANT_URL`, `ANTHROPIC_API_KEY`,
`VOYAGE_API_KEY` (see `specs/001-grounded-faq-chat/quickstart.md` for the full walkthrough).

```bash
make db-up                                               # start Postgres + Qdrant
uv run --package chat -- alembic upgrade head             # apply DB migrations (from services/chat/)
uv run --package chat -- python -m chat.main             # run the chat service
uv run --package scheduler -- python -m scheduler.main  # run the scheduler service
```

`chat`'s test suite (`services/chat/tests/`) never touches the `visitdoc` database above — it runs
against a separate `visitdoc_test` database in the same Postgres container (schema kept at head
automatically by a session-scoped fixture in `conftest.py`), so manual `curl`/dev work and
automated tests can't pollute each other. `docker-compose.yml` creates `visitdoc_test`
automatically on a fresh volume; if you set up Postgres before this existed, create it once with
`docker exec visitdoc-postgres psql -U visitdoc -c "CREATE DATABASE visitdoc_test;"`. CI
(`.github/workflows/ci.yml`) uses its own fully ephemeral Postgres/Qdrant service containers, so it
needs no such step.

Qdrant gets the same treatment: tests use a `faq_chunks_test` collection (derived from
`QDRANT_COLLECTION_NAME`, default `faq_chunks`) in the same Qdrant instance, created on demand by
the existing idempotent `ensure_collection` — no volume/init-script step needed, since a collection
is just an API-created resource, not something that has to pre-exist like a Postgres database.

See the [`Makefile`](Makefile) for shortcuts (`make sync`, `make lint`, `make format`,
`make typecheck`, `make precommit`, `make install-hooks`, `make run-chat`, `make run-scheduler`,
`make db-up`, `make db-down`).

## Grounded FAQ Chat: technology choices

Phase 0's walking skeleton (`specs/001-grounded-faq-chat/`) made several notable technology
choices, each with a tradeoff — full rationale and alternatives considered live in
[`research.md`](specs/001-grounded-faq-chat/research.md):

- **Embeddings**: Voyage AI (`voyage-3-lite`), since Claude has no embeddings endpoint. Chosen
  over a local model (avoids a heavy ML runtime) and over OpenAI (keeps the stack Claude-centric).
- **Postgres drivers**: `asyncpg` for the app, `psycopg` v3 (sync) for Alembic migrations —
  the conventional SQLAlchemy 2.0 pairing, rather than forcing Alembic's sync runner through
  `asyncpg` via `run_sync`.
- **Agent orchestration**: a plain async function (`agent/answer_faq.py`), not LangGraph — a
  single retrieve→gate→generate path has no branching to justify a graph framework yet.
  LangGraph lands in Phase 1 once real branching (parallel specialist nodes) exists.
- **Streaming transport**: NDJSON over a plain `fetch` + `ReadableStream`, not SSE/`EventSource`
  (which can't carry a POST body) or WebSocket (unnecessary for one request/response stream).
- **Chunking**: fixed-size (~1,000 chars, ~150-char overlap), preferring paragraph/sentence
  boundaries over mid-word cuts — simple and defensible at this phase's scale; semantic chunking
  and reranking are deferred to Phase 1.
- **Groundedness gate**: a pre-generation similarity-threshold check on retrieval, not a second
  LLM call (LLM-as-judge) — satisfies the constitution's mandatory-abstention principle without
  doubling latency/cost on every question; a fuller check lands in Phase 1/2.
- **Citations**: derived structurally from retrieval (which chunks were actually placed in
  Claude's context), not self-reported by the model — avoids hallucinated citations, and lets a
  reviewer directly diff the streamed answer against the verbatim `chunk_text` it cites.

## Structured Logging: technology choices

`specs/002-structured-logging/` instruments `chat`'s agent/RAG pipeline so a turn's full decision
trace can be reconstructed from logs alone — full rationale and alternatives considered live in
[`research.md`](specs/002-structured-logging/research.md):

- **Structured logging library**: `structlog`, not stdlib `logging` + a custom `Formatter` or
  `loguru` — its processor-chain architecture lets truncation/redaction/rendering be centralized in
  one place (`core/logging.py`) and swapped later (e.g. for a Langfuse-ready renderer) without
  touching any of the ~20 call sites that actually log.
- **Correlation IDs**: ULID (`python-ulid`), not a hyphenated UUID4 — same collision resistance,
  but shorter, separator-free (one double-click selects the whole ID in a terminal), and
  lexicographically sortable by creation time. Bound per-request via `structlog.contextvars`
  (`core/correlation.py`), scoped to the current `asyncio` task so concurrent requests never see
  each other's bound `turn_id`/`operation_id`.
- **Severity tiers**: the three standard log levels (`info`/`error`/`critical`) rather than a
  bespoke severity field — `structlog`'s `ConsoleRenderer` already styles by level, so only
  `critical`'s extra prominence over `error` needed a small style override, not a new mechanism.
- **Sub-step logging granularity**: one summarized entry per sub-step (e.g. `faq.chunks_embedded`
  reports a chunk count), not one entry per chunk — keeps log volume proportional to pipeline steps
  rather than content length, while a failure is still fully attributable via the operation's
  `failed_step`.

## Conversational Chat History: technology choices

`specs/003-conversational-chat-history/` (ROADMAP Phase 1a) turns the single-turn `/chat` endpoint
into a persisted, multi-turn chat — full rationale and alternatives considered live in
[`research.md`](specs/003-conversational-chat-history/research.md):

- **`Session` kept separate from `Chat`**: the anonymous visitor identity (the cookie) and the
  chat thread it currently owns are two rows, not one, even though this phase only ever has one
  `Chat` per `Session`. Costs one small table now, but avoids a breaking migration later when a
  `Patient` layer is expected to sit between a `Session` and its chat(s) (spec.md Future Direction).
- **Flat, sender-tagged `Message` log**: no paired request/response turn — each patient or
  assistant message is its own row, ordered by `created_at` ascending (via a dedicated
  `ix_messages_chat_id_created_at` index, not by ULID id — id order isn't reliably equivalent to
  `created_at` order across concurrent writers), with `sender` an open string set (not a DB
  enum) so ROADMAP Phase 1d's `staff` sender can be added later with zero schema migration.
- **`reply_to_message_ids` ties a reply to every turn it actually answers**: an assistant
  message's `reply_to_message_ids` (JSONB list, not a single FK) records every patient message
  id it answers, in order — necessary because a merged burst (FR-014) is answered by exactly one
  assistant message, so a scalar `reply_to_message_id` pointing only at the triggering message
  silently lost the earlier ones. History-building never has to infer that pairing from row
  order — a stray or out-of-order write can no longer corrupt a different turn's history.
- **Append-only persistence, no pending/update phase**: a patient message is inserted the moment
  it's validated; an assistant message is inserted once, in full, only on success. A failed or
  cancelled attempt simply never gets an assistant row — no rollback, no partial-state cleanup.
- **Cancel-and-restart via an in-process registry**: `agent/generation_registry.py` is a plain
  `dict[chat_id, tuple[turn_id, asyncio.Task]]`, not Redis/pub-sub — this phase runs one `chat`
  process, so process-local state is sufficient, and it avoids infrastructure a single-instance
  deployment has no use for yet. The `turn_id` half exists purely so a cancellation can be logged
  (`turn.cancelled`) against the specific patient turn that got superseded, not just an opaque task.
- **`message.persisted`/`turn.cancelled`/`turn.message_received` diagnostic logging**: every
  inserted `Message` logs `message.persisted` (chat id, message id, sender,
  `reply_to_message_ids`), every superseded generation logs `turn.cancelled` (chat id, the
  cancelled turn's id, the superseding turn's id), and `turn.message_received` — logged as soon
  as a turn's pipeline starts, before retrieval — also carries `reply_to_message_ids`, so a log
  reader can already tell from that first line whether a merged burst or a single message is
  being processed, without waiting for the later `message.persisted` line. A patient seeing no
  reply to an earlier message is traceable to an explicit cancellation event in the logs, not
  silently indistinguishable from the message never having been sent.
- **Multi-turn context as Messages-API history, not prompt concatenation**: prior messages are
  passed as a proper alternating `user`/`assistant` list, matching how Claude is trained to use
  multi-turn context, rather than hand-rolled into the current message's prompt string.
- **Retrieval scoped to the merged trailing patient-message run**: a burst of unanswered messages
  (e.g. "When can I see" + "Dr. Josh?") is merged into one retrieval query, reusing the same merge
  pass already needed for the Messages-API history — not a second mechanism or an extra LLM call.
- **Session id generation**: `python-ulid`'s bare `ULID()` constructor turned out to be monotonic
  *by default* in the installed version — same-millisecond calls increment the previous randomness
  by 1 rather than resourcing it, which would fail the non-guessable-identifier requirement for a
  value used as a bearer cookie. `chat_repository.create_session` instead uses an explicit
  `ulid.ULIDGenerator(policy=ulid.PureRandomPolicy())`, verified empirically against the library's
  source before relying on it.
