# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@CLAUDE.local.md

## Project status

This repository is at the walking-skeleton stage: each service has only a placeholder `main.py`
("Hello from chat!" / "Hello from scheduler!") and no real application code exists yet.
`docs/ROADMAP.md` is the authoritative design document — read it before starting any implementation
work, since it defines the architecture, phased build plan, and the reasoning behind each technology
choice. Treat its "Design principles" and "Phased build plan" sections as binding scope guidance,
not just background: build Phase 0 before Phase 1, don't introduce Phase 3+ platform layers early,
and don't add services/infra beyond what the current phase calls for.

The repo is a **monorepo**: `services/chat`, `services/scheduler`, and `services/frontend` are
independent services, with cross-service Python code (Pydantic schemas, the gRPC contract) factored
out into `packages/`. See "Repository layout" below.

## What this project is

VisitDoc is a conversational AI assistant for a medical clinic (a portfolio project targeting an AI
developer role). Patients book/reschedule/cancel appointments via chat and get grounded,
citation-backed answers to policy/FAQ questions; the assistant abstains and escalates to human staff
rather than confabulating when retrieval is weak. The applied-AI core (agent graph, RAG, tool use,
eval/observability) is the focus — platform/infrastructure work is explicitly scoped as optional
later phases.

## Stack
### AI
- Claude API
- Qdrant

### Backend
- Python / FastAPI / Pydantic
- Postgres / Alembic / SQLModel / SQLAlchemy

### Ffontend
- REACT/Vite


## Repository layout

`uv` workspace (root `pyproject.toml` is a *virtual* workspace root — no `[project]` table of its
own, it only lists members):

```
services/
├── chat/          # FastAPI core backend: agent, RAG, chat, auth (uv member "chat")
│                  # has its own .claude/CLAUDE.md with the Python code style guide
├── scheduler/     # FastAPI + own Postgres, gRPC server (uv member "scheduler")
│                  # has its own .claude/CLAUDE.md with the Python code style guide
└── frontend/      # React + Vite SPA — plain Node project, NOT a uv workspace member
│                  # has its own .claude/CLAUDE.md (network layer, per-turn state, tests)
packages/
├── shared-db/     # engine/session construction + Alembic's async→sync URL swap (uv member "shared-db")
├── shared-logging/# the one structlog processor chain, incl. secret redaction (uv member "shared-logging")
├── shared-models/ # cross-service Pydantic schemas (uv member "shared-models")
└── shared-proto/  # chat<->scheduler gRPC contract: protos/ source + generated *_pb2*.py (uv member "shared-proto")
```

`shared-logging` and `shared-db` exist because `chat` and `scheduler` otherwise hold byte-identical
copies of their infrastructure layer, varying only in a `Settings` field name. Redaction in
particular is a security control: a copy that drifts leaves one service logging in the clear with
nothing to catch it. Each service keeps only what genuinely varies — its secret-field tuples, and
its own module-level engine bound to its own URL setting.

`chat` and `scheduler` depend on `shared-models`/`shared-proto` via `tool.uv.sources` workspace
references. All Python members share **one `uv.lock` and one `.venv`** at the repo root — they
can't pin conflicting versions of a shared dependency.

### Convention: directory-scoped CLAUDE.md files

When a subdirectory needs its own Claude Code guidance (e.g. a service-specific style guide), place
it at `<dir>/.claude/CLAUDE.md`, not `<dir>/CLAUDE.md` — matching this repo's own root convention
(this file lives at `.claude/CLAUDE.md`, not the repo root). Claude Code loads a directory's
`CLAUDE.md` only when files under that directory are being read/edited, so this keeps
service-specific rules out of context everywhere else. `services/chat/.claude/CLAUDE.md` and
`services/scheduler/.claude/CLAUDE.md` are the existing examples — both just `@`-import
`docs/python-style-guide.md` rather than duplicating it.

`services/frontend/.claude/CLAUDE.md` deliberately holds its rules inline instead of `@`-importing
a `docs/frontend-style-guide.md`: the `@`-import exists so two Python services can share one guide,
and there is only one frontend, so a separate file would add indirection with no second reader. If
a second Node project ever appears, extract it then.

## Commands

`.python-version` is pinned to 3.12 for the whole workspace. Common commands are in the
[`Makefile`](../Makefile): `make sync`, `make lint`, `make format`, `make typecheck`,
`make precommit`, `make install-hooks`, `make run-chat`, `make run-scheduler`.

A few less-common `uv` invocations that don't have a Makefile target (they take arguments):

```bash
uv sync --package chat                    # sync just one member (e.g. in CI)
uv add --package chat <package>           # add a dep to services/chat
uv add --package shared-proto <package>   # add a dep to a shared package
```

### Running the stack by hand

`make run-chat-dev` / `make run-scheduler-dev` / `make run-frontend-dev` each hold a terminal. To
put all three in the background instead — which is what manual testing of a whole flow needs —
`make services-up`, `make services-status`, `make services-down`, with `make migrate` first if the
dev databases are behind. Each service's pid and log live under `.run/` (gitignored).

**Stop them with `make services-down`, never with `pkill -f "chat.main"`.** That pattern also
matches the command line of the shell running it, so it kills the caller — and anything else whose
command line happens to quote the module name. `scripts/dev-services.sh` kills a recorded pid and
`pkill -P` for its child, which cannot match anything by accident.

`scripts/dev-chat.sh` drives a conversation against a running chat service — mint a session, post a
turn and stream the reply, read the thread or the staff console, post as staff, flip the assistant
switch, add a FAQ entry. It exists so that exercising a flow by hand doesn't start with rebuilding
a cookie jar and a `curl` invocation from memory. Run it with no arguments for the usage block.

Regenerating the gRPC stubs (after editing `packages/shared-proto/protos/scheduling/v1/scheduling.proto`)
is documented in `packages/shared-proto/README.md` — it requires a manual import fixup after
running `protoc`, don't skip that step.

Ruff lint rules are configured once, in the root `pyproject.toml`'s `[tool.ruff]`/`[tool.ruff.lint]`
tables, and apply to every Python workspace member automatically via ruff's hierarchical config
discovery (no member has its own `[tool.ruff]`, so the walk-up always lands on the root). Generated
code is excluded via `extend-exclude` rather than hand-reformatted to match style rules — the
established examples are gRPC stubs (`**/*_pb2.py`, `**/*_pb2_grpc.py`) and Alembic migrations
(`**/alembic/versions/*.py`); follow the same pattern for the next generated-code case instead of
fixing lint violations by hand in generated files.

`specs/**/*.py` is excluded from **both** ruff and mypy for the same reason, though it is not
generated: a spec directory holds documentation plus the data and one-off harnesses a manual
procedure is run from (spec 009's `evaluation/inputs/` is the first). None of it is application
code, nothing imports it, and it ships with a spec rather than with a service — so the rules written
for the app do not apply to it, and annotating a throwaway driver to satisfy `strict` would buy
nothing. Note that `make typecheck` runs `mypy .`, which overrides the `files` list in
`[tool.mypy]`, so mypy needs its own `exclude` entry rather than relying on `files` alone. **Do not
put application code under `specs/`** — it is unchecked by both gates.

Mypy is configured once, in the root `pyproject.toml`'s `[tool.mypy]` table, in `strict` mode
(aligns with the style guide's "annotate every function" rule). Unlike ruff, mypy doesn't do
per-file hierarchical discovery — it's pointed explicitly at each workspace member's `src/` via
`files`/`mypy_path`, with `explicit_package_bases = true` since each member is its own package root.
The generated gRPC stubs have their errors suppressed via a `[[tool.mypy.overrides]]` entry
(`ignore_errors = true` for `shared_proto.scheduling.v1.*`) since protoc-generated code isn't
statically typed; `types-protobuf`/`types-grpcio` are installed as dev dependencies so *your* code
using `google.protobuf`/`grpc` directly still gets real type checking.

Testing conventions (folder layout, naming, why `--import-mode=importlib` is required, why `tests/`
is excluded from mypy) are documented in `docs/testing-strategy.md`. In short: unit tests are
colocated per workspace member (`services/chat/tests/`, `services/scheduler/tests/`,
`packages/shared-db/tests/`, `packages/shared-models/tests/`, `packages/shared-proto/tests/`); integration/e2e tests are
centralized at `tests/integration/`/`tests/e2e/` (placeholders for now). Run via `make test` /
`make test-unit`, `make test-integration`, `make test-e2e`; only the unit tier runs in CI so far
(`test` job in `.github/workflows/ci.yml`, alongside `pre-commit`).

### Pre-commit hooks

`.pre-commit-config.yaml` (repo root) defines four **local** hooks (`language: system`, no
Astral/pre-commit-mirror version pins — they all shell out to the exact ruff/mypy/uv versions
already resolved in `uv.lock`, so there's one source of truth for tool versions, not two):

1. `uv-lock-check` — `uv lock --check`: fails if `uv.lock` is stale relative to `pyproject.toml`.
2. `uv-sync-check` — `uv sync --check`: fails if `.venv` is stale relative to `uv.lock` (read-only —
   does not install anything; run a plain `uv sync` to fix).
3. `ruff-check` — `uv run ruff check .`
4. `mypy-check` — `uv run mypy .`

Installed locally via `uv run pre-commit install`. Each contributor needs to run that once after
cloning (it's a `.git/hooks/` entry, not tracked by git).

## Architecture

### Core design principles
- Follow SOLID, major clean architecture principles, and best industry paractices.
- Dependency Inversion: orchestration/high-level code (e.g. a LangGraph node) should depend only on
  domain types, never on a specific provider's wire format (e.g. Anthropic's `MessageParam`).
  Translating domain data into a provider's request shape is that provider-calling function's own
  responsibility, done internally, not the caller's — keeps provider-specific knowledge in one
  place and out of orchestration code.
- **One value, one meaning.** A return value, status, or empty result must never stand for two
  different situations. An empty list that means both "this exists and has nothing" and "this does
  not exist", or a `None` that means both "already done" and "the dependency was unreachable",
  forces every caller to guess — and in an agent, a guess becomes a confident false statement to a
  patient. When two situations are genuinely different, give them different answers: a distinct
  return type, a typed failure, a `NOT_FOUND` status. If a value's docstring needs the word "or" to
  describe what it means, that is the smell.
- **Every query carries its session/tenant predicate.** Scoping is a `WHERE` clause on the read,
  never a check applied to the result afterwards, so an id from another session simply does not
  resolve. This applies especially where it feels unnecessary: a lookup by a globally-unique key
  (an idempotency key, a ULID) still needs the scope, because "unique" only means no *collision* —
  it says nothing about who is allowed to read the row. A scoped check placed *after* an unscoped
  lookup that already returned data is not a check at all.
- **A timeout never proves the server did nothing.** A deadline is the caller's, not the callee's:
  it expiring means the answer did not arrive, not that the work did not happen. The same is true
  of a server-side error status, which means the request *was* processed. Code may report "nothing
  was created" only when it actually knows that — every attempt failed to reach the server. When
  the outcome is genuinely unknown for a write, say so and stop, rather than guessing "nothing
  happened" and inviting a retry that duplicates a real, uncancellable side effect.

### Target shape (AI-core phase, per ROADMAP)

- **Core backend** — FastAPI, hosting the agent, RAG, chat, and auth. Single deployable for
  everything except Scheduling. Beyond `/chat`, `/chats` and `/faq` it publishes `/console/*` (the
  staff side: the polled conversation listing, posting as staff, the assistant switch, and a proxy
  of the scheduler's practitioner API) and `/admin/*` (the session listing and session
  deletion — guarded by one header secret, and declared `include_in_schema=False` so they
  appear in no published schema). Those two live in separate modules on purpose: a
  maintenance surface sharing a module with a published one is one refactor away from
  sharing its router.
- **Scheduling service** — a separate FastAPI service with its own PostgreSQL, the one deliberate
  service boundary in this phase. Owns doctor calendars, availability, booking, and changes to a
  booking. Talks to the core backend over gRPC (`CheckAvailability`, `BookAppointment`,
  `RescheduleAppointment`, `CancelAppointment`, `ListAppointments`). Double-booking is prevented at
  the database level via PostgreSQL exclusion constraints on interval/range types, not application
  code — **partial** ones (`WHERE status = 'standing'`) since 006, so a cancelled appointment stops
  occupying its slot at the datastore rather than by an application filter. A *change* is likewise
  one conditional `UPDATE` whose `WHERE` clause carries the staleness guard, never a check
  performed before the write.
- **Vector store** — Qdrant, for RAG over clinic policy/FAQ documents.
- **Agent framework** — LangGraph, with real branching (parallel specialist nodes + merge) for
  mixed-intent messages rather than a linear chain.
- **Frontend** — a minimal React + Vite streaming chat UI.
- **Tracing/eval** — Langfuse (self-hosted) for per-step latency, token cost, and decision traces.

### Key design decisions to preserve

- Intent classification uses **structured output**, not free-text parsing, and a cheap/fast model —
  reserve the stronger model for generation. Since 009 the label set says what a message *is*
  before anything decides who handles it: `faq_question`, `booking`, `small_talk` (asks for nothing
  that can be acted on), `urgent_condition`, `distress`, `booking_for_another`, `call_staff` (an
  explicit request for a human, and nothing else), `unknown` (a request the assistant is not
  authorized to serve). `unknown` no longer falls through to the FAQ path.
- Capabilities are exposed to the agent as **MCP tools** (`search_faq`, `check_availability`,
  `book_appointment`, `escalate_to_staff`) so agent logic stays decoupled from implementation.
- RAG must include defensible chunking, a reranking step, citations to source documents — derived
  structurally from what was actually retrieved and placed in context, never self-reported by the
  LLM (avoids hallucinated citations) — and an explicit **abstention path**. Since 008 the
  "groundedness check" is **two gates before generation**, not a boolean after it: a per-chunk
  similarity floor, then a cross-encoder rerank floor, either of which abstains without spending a
  generation call. `rag/groundedness.py` and the `grounded` flag are gone; a turn now carries a
  five-value `FaqVerdict` naming which gate stopped it, because "an answered turn is grounded" made
  `true` uninformative while `false` covered three situations needing three different fixes.
  Per-turn *post-generation* groundedness verification is deliberately not done — `docs/ROADMAP.md`
  assigns answer groundedness to Phase 2's offline eval harness.
- **Postgres decides what Qdrant may answer from.** For any entity with both a Postgres row and a
  derived Qdrant index (e.g. `FaqEntry`/`FaqChunk`), the row is the sole authority on which indexed
  content is live, and retrieval carries that as a predicate on the search itself. Points the row
  does not vouch for — superseded by a later write, orphaned by a failed one, left behind by a
  delete — must be unreachable: never retrieved, never cited, never counted toward groundedness.
  Removing them is housekeeping that may fail, retry, or lag; it must never be what makes them
  unanswerable. Leaked points are an accepted cost, an answer drawn from one is not.
  Concretely, since 007: a save is **additive**. It writes its chunks under a new revision, deletes
  nothing, and publishes with one local commit whose `WHERE` carries the revision it expects to
  supersede — so a failure at any step leaves the entry answering the text it was answering a
  moment ago, and there is no compensating write to half-succeed. The delete-then-upsert ordering
  this bullet used to prescribe is superseded by that, and a delete now removes the **row first**,
  which is what makes the entry unanswerable at that instant.
- **A new session is created holding the starter corpus.** `chat.rag.default_corpus` declares it and
  `chat.api.provisioning.seed_default_corpus` plants it as part of session creation, superseding
  spec 007's FR-039b ("a new session's corpus MUST start empty"). It is planted the way a save is —
  chunks written first under revisions nothing names live, one commit publishing all of them — so a
  failure plants none of it, and it never fails chat creation: the session simply starts empty, and
  that is logged rather than raised. Every other property of a session's corpus is unchanged, and
  a seeded entry is an ordinary entry the session may edit or delete.
- **The request, not the message, is the unit of work** (010). The classifier returns an ordered list
  of `{intent, text}` segments rather than a list of labels — `IntentClassificationResult.intents`
  survives as a *derived* property, which is what let every routing rule keep reading the value it
  read before. Each specialist is handed only its own segments and substitutes them into the trailing
  conversation entry via `history.replace_trailing_entry`, so isolation is structural: the other
  half's clause is not in the prompt to be answered. The retrieval pipeline runs once per FAQ
  segment, concurrently, **never pooled** — a shared shortlist under the 3-chunk cap lets the
  stronger question crowd the other out, which is the defect splitting exists to remove — and each
  segment's answer is generated from its own shortlist alone. The fan-out lives *inside*
  `answer_faq`, not in the graph: one node still writes one state key, so LangGraph needs no channel
  reducer. Two rules bound it: the cap of 3 cannot be expressed in the request schema (the API
  rejects array bounds in a constrained-output schema), so it is stated in the prompt and an
  over-long result is *rejected*, never trimmed; and each request's own outcome is what the turn
  reports, since 011 — see the entry below.
- **The verdict, the answer and the citations live on the request** (011). There is no turn-level
  verdict anywhere: `messages.faq_verdict` and `messages.citations` are gone, replaced by one
  `messages.request_outcomes` JSONB column holding `{position, question, answer, verdict, citations}`
  per request in ascending position order — and the same list is what `ChatDoneEvent`, `MessageOut`
  and `turn.completed` carry. NULL means no FAQ half ran; `[]` is never written. `summarize_verdict`
  is deleted and no equivalent reduction may reappear under another name: a turn may answer one
  request and abstain on another, and any single value describing both is the "one value, two
  meanings" defect this phase removed. `RequestOutcome` enforces the pair that follows from a
  verdict — `answer` is None exactly when the verdict is an abstention, and `citations` is empty
  exactly then — so no reader has to decide which of two fields to believe. Citations are
  deduplicated **within** a request, so a chunk that supported two requests is cited under each.
  On the reply itself: the FAQ half contributes one part per answered request plus exactly one for
  the gap however many requests fell into it, which keeps `_actual_parts` bounded by the
  routing-time `_expected_parts`; a turn whose every request abstained still takes the collapse
  path and gets `_ABSTENTION_MESSAGE` byte for byte with no composing call. The composer gains a
  gap block and three constraints — never soften a gap, never extend an answer to cover one, name
  each gap in your own words rather than by quoting the classifier's restatement — and, being
  model-obeyed, their effect is measured by hand against `specs/011-answer-what-you-can/evaluation/`
  and re-checkable afterwards from the stored parts beside the stored reply. `turn.completed`'s
  `outcome` names the turn's **shape** (`faq`, `booking`, `small_talk`, `handed_off`, `merged`),
  never a verdict, and `answer_source` left that event once the two said one thing;
  `abstention_message` is set only when *every* request abstained. `agent/escalation.py` is
  unchanged and stores nothing new — what staff read as "the unserved requests" is derived from the
  reply's outcomes at render time.
- **A turn that ends without a reply calls a person** (011, FR-026). Any pipeline failure — the
  composing call, a generation, an embedding, a retrieval, or a bug this build cannot name —
  records `assistant_failed` into the turn's own collector and applies it before the error
  propagates (`_call_staff_for_the_failure` in `chat/api/turn.py`). Before 011 only the booking
  loop's tool failures did, so a broken turn left the patient with an error and nobody looking at
  it. Three properties make it safe to put on every failure path: it is the **weakest** precedence,
  so a turn that already called staff for a corpus gap or an unauthorized request keeps that cause
  and that mark; it does **not** silence, because the thing that broke may already be working
  again; and a cancellation reaches it at all — `CancelledError` is a `BaseException`, and a
  superseded turn is not a failure. If the staff call itself fails it is logged as
  `turn.staff_call_failed` and swallowed: the original error is what the turn has to report.
- Repository functions take the `AsyncSession` as an explicit parameter (e.g.
  `faq_repository.create(session, content)`) rather than a repository class holding session state —
  matches FastAPI's own documented pattern, keeps repository functions stateless and reusable across
  callers, and lets the API layer own the transaction boundary (one session per request via
  `Depends`). New repositories, including `scheduler`'s, should follow this shape.
- **A person is called in four situations and no others** (009): the patient asked for one; the
  patient asked for something the assistant cannot provide; the message needs a person on safety or
  authority grounds; something failed. A message that requests nothing calls nobody — before 009,
  "Thanks" took the FAQ path, abstained, and paged a human. The middle two are recorded as two
  causes each (`corpus_could_not_answer` vs `not_authorized`; `urgent_condition` vs `distress` vs
  `booking_for_another_person`) because they call for different fixes. `agent/escalation.py` holds
  the structures that decide what each cause does: `_PRECEDENCE` orders them, `_SILENCING` says
  which stop the assistant, `_MARK_BY_REASON` derives the message mark, and `HANDOFF_TEXT` gives a
  constant to each of the five that *end* a turn — a corpus gap and a failure have none, because
  neither ends one. Four causes
  silence the conversation; `not_authorized`, a corpus gap and a failure do not. The four texts are
  constants, not generated: a stopping reply makes no claim about the clinic, which is the only
  thing retrieval could have grounded. The assistant does not triage — `urgent_condition` is a
  routing decision whose reply points at emergency services rather than judging anything.
- Scheduling failure handling (timeouts, retries, agent behavior when Scheduling is unreachable) is
  part of the design, not an afterthought.
- Each significant technology choice should be documented with its tradeoff in the README, so later
  additions should follow that pattern rather than going undocumented.

See `docs/ROADMAP.md` for the full phased plan (Phase 0 walking skeleton → Phase 1 agent → Phase 2
eval/observability → Phase 3+ optional platform layers) and the target microservices reference
architecture.
