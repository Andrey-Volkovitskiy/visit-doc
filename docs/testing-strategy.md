# Testing strategy

## Layout

- **Unit tests are colocated per workspace member**: `services/chat/tests/`,
  `services/scheduler/tests/`, `packages/shared-models/tests/`, `packages/shared-proto/tests/`.
  Each member already owns its own `pyproject.toml`/`src/` — its `tests/` dir is part of that same
  self-contained unit, not a separate tree. A package's `tests/` dir *is* the unit tier; there's no
  extra `tests/unit/` nesting.
- **Integration and e2e tests are centralized at the repo root**: `tests/integration/`,
  `tests/e2e/`. These inherently cross service boundaries, so they can't belong to a single package.
  `tests/integration/` is real as of spec 005: it runs `chat`'s own gRPC client against a live
  scheduling servicer backed by a real `visitdoc_scheduler_test` database, covering the booking
  round trip, the idempotent replay, and the cross-store deletion cascade — the contract the chat
  unit tier's fakes stand in for. Since 007 it also drives `chat`'s **own** stores, so its
  `conftest.py` isolates `DATABASE_URL` and `QDRANT_COLLECTION_NAME` exactly as the chat unit tier
  does: the FAQ corpus's session isolation, the practitioner proxy against a live REST surface, and
  the two-store session delete all need both sides real at once. `tests/e2e/` is still a placeholder (no full frontend+chat+
  scheduler flow is automated yet).

## Naming

- Files: `test_*.py`.
- Functions: `test_<behavior>`, e.g. `test_book_appointment_rejects_overlapping_slot` — the name
  documents the case, so no docstring is required (see below).
- Classes: only `Test*` when grouping tests that share setup; prefer flat functions otherwise.
- `conftest.py`: one per package, scoped to that package's own unit fixtures. For
  integration/e2e (once built), `tests/integration/conftest.py` and `tests/e2e/conftest.py` hold
  fixtures that cross service boundaries — e.g. a shared gRPC test channel wiring `chat` to a real
  `scheduler` instance, or a shared HTTP client hitting both services. Since those live in the
  centralized `tests/` tree rather than under any one package, they're the natural home for
  fixtures no single package should own.

## Mocking discipline

Don't assert against a value you hardcoded into a mock — that only proves the mock did what you
told it to, not that the code under test behaved correctly. E.g. `AsyncAnthropic` is fully mocked
in chat's API tests (`FakeAnthropicStream` yields whatever tokens the test passes in, ignoring the
actual prompt/context it's called with); asserting the streamed answer text equals those same
tokens is a tautology. Assert on something the *unmocked* part of the system produced instead —
e.g. `citations`/retrieved `chunk_text`, which come from a real Qdrant search and would actually
change if retrieval broke. If nothing unmocked is left to assert on, either too much is mocked, or
this test isn't actually the right place to verify that behavior.

Every test that exercises a code path capable of calling a paid, remote AI API — Anthropic
(`AsyncAnthropic`, both `answer_faq`'s generation call and `classify_intent`'s classification call)
or Voyage (`embed_texts`) — MUST mock that call, even if the test's own assertions never touch its
output. A test only caring about an *earlier* pipeline stage still runs later stages that call these
APIs unconditionally — e.g. `classify_intent_node` (`agent/graph.py`) runs ahead of every FAQ answer
regardless of whether the test is exercising the grounded, abstained, or failure path — so leaving
any of them unmocked doesn't just burn real API tokens on every test run, it makes the test
genuinely non-deterministic (network latency and model output both vary run to run) and dependent on
network access and a valid API key. This happened in practice: three tests written before intent
classification was wired into every turn (`test_abstention_on_unrelated_question`,
`test_followup_still_abstains_when_neither_message_is_grounded`,
`test_get_chat_history_preserves_abstention`) never needed to mock `AsyncAnthropic` at the time, but
silently started making live calls once `classify_intent_node` began running unconditionally ahead
of every FAQ answer — caught only when one of them failed non-deterministically on a live run.
Use `conftest.py`'s `fake_anthropic_client(...)` (covers the generation stream, the classification
call, *and* the booking loop's tool-use call with one mock — the last two share
`.messages.create` and are told apart by the parameter each sends) even when a test's own
assertions never touch any of them. A test that needs the booking loop to actually call tools
passes `booking_tool_calls=[...]`.

**This rule is enforced, not just documented.** `services/chat/tests/conftest.py`'s autouse
`_paid_apis_are_blocked` patches the paid SDK calls themselves — Anthropic's
`AsyncMessages.create`/`.stream` and Voyage's `AsyncClient.embed` — so a test that reaches one
fails immediately with `PaidAPICallInTestError`, naming the call it made and the fake it should
have used, instead of quietly billing a live request. The block sits on the SDK class, not on this
codebase's wrappers, so it also catches a client the app's own lifespan builds in a test that
forgot to patch `chat.main.AsyncAnthropic`. Two consequences worth knowing:

- **A new paid call belongs in `_PAID_API_CALLS` in the same change that introduces it** — a new
  SDK method, or a new provider entirely. A guard with a gap is worse than no guard, because the
  suite now reads as if it were covered.
- **`services/chat/tests/test_paid_api_guard.py` asserts the guard is armed.** A renamed SDK
  attribute would make the `patch` target stop resolving, and a guard failing open looks exactly
  like a suite that never calls a paid API — which is the state it exists to distinguish from.

The **scheduling gRPC boundary** is faked the same way and for the same reason: no scheduler runs
alongside chat's unit tests, and the app's channel is bound to the lifespan's event loop rather
than the test's. `services/chat/tests/conftest.py`'s autouse
`_scheduler_is_unreachable_by_default` makes every chat unit test see an unreachable scheduler
unless it patches the boundary itself — which is also the honest default, since that is exactly
what a chat service with no scheduler running really sees.

An autouse fake at a boundary MUST cover **every** call reachable across it, not just the one a
test happened to need when it was written. A boundary fake is a guarantee the whole suite leans
on, so a gap in it is not "that path is untested" — it is a live call on the wrong event loop,
failing with a loop-binding or timeout error attributed to whatever the test was actually about.
When a new function is added to a faked boundary, add it to the autouse fixture in the same
change: `_scheduler_is_unreachable_by_default` fakes `ensure_session_provisioned`,
`delete_patient_for_chat`, `rename_patient` and `delete_session` for exactly this reason.

## Live paid APIs: manual testing and e2e only

The repo-root `.env` holds working `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY` values, and **manual**
testing is free to use them. Running the services by hand (`make run-chat-dev` /
`make run-scheduler-dev`), walking a feature's `quickstart.md`, or reproducing a reported problem
interactively all talk to the live Claude and Voyage APIs — that is what they are for. It is the
only place the real model's wording, latency, tool-calling and retrieval quality are ever observed,
and no amount of mocked coverage substitutes for it. Spending real tokens that way is expected, not
something to apologise for or work around.

**The unit and integration tiers may never reach either API.** The reasons are in "Mocking
discipline" above and none of them is about cost alone: a live call makes the test
non-deterministic, dependent on network access and a valid key, and slow. Both tiers already fake
the two boundaries — `services/chat/tests/conftest.py`'s `fake_anthropic_client(...)` under the
autouse `_paid_apis_are_blocked` guard, and `tests/integration/conftest.py`'s own stand-ins for
`AsyncAnthropic` and Voyage embeddings. These two tiers are the fast gate: they run on every push,
must pass with no key configured at all, and give the same answer every time.

**The e2e tier may.** `tests/e2e/` drives the whole system as a user does — a browser against
running services — and stubbing the model there would remove the only thing that tier tests that
the others do not. Three consequences follow from that permission, and they are the price of it:

- **e2e never joins the per-push gate.** It runs on pull requests to `main`, and by hand before a
  demo. A tier that spends tokens and depends on a remote service cannot be the thing standing
  between a commit and a merge.
- **Assert on structure, never on wording.** That an answer arrived carrying citations, that a
  stream was cancelled, that a mark cleared, that a booking reached the scheduler's database — all
  reproducible. The sentence the model wrote is not, and an assertion on it is a test that fails on
  a Tuesday for no reason.
- **A missing key fails the tier loudly, and skips nothing.** A silently skipped e2e run reads
  exactly like a passing one.

So the dividing line is where a test sits, not what it feels like: **unit and integration mock the
paid boundary; e2e and a human at a keyboard are what exercise it.** A unit test written to check
"does the real model actually do this?" is still in the wrong place — that question belongs to e2e
or to a quickstart scenario.

## Fixtures

**Derive a fixture from production, never restate it.** When a test needs a default, a schema, or
a set of names that production also declares, import the production declaration rather than typing
an equal-looking literal into `conftest.py`:

```python
DEFAULT_SCHEDULE = list(
    practitioner_repository.DEFAULT_SCHEDULE
)  # not a re-typed Mon-Fri tuple
f"TRUNCATE TABLE {', '.join(all_table_names())} ..."  # not a hand-written table list
```

A restated constant does not fail when production changes — it silently keeps testing the old
value. That is worse than a broken test: the suite goes green while covering a configuration the
system no longer creates, so the change that needed scrutiny gets none. The two shapes this takes
here are seeding defaults (`DEFAULT_SCHEDULE`/`DEFAULT_SPECIALTY`/`DEFAULT_DURATION_MINUTES`, which
come from `practitioner_repository`) and the per-test `TRUNCATE` list (which comes from each
service's `all_table_names()`, read off `Base.metadata`, so a table added later is cleaned up
without anyone remembering to add it).

The same applies across test tiers: `tests/integration/conftest.py` and a package's own
`conftest.py` must not each declare their own copy of an isolation rule. The `_test`-suffix scheme
lives once in `shared_db.testing` (`isolated_database_url`/`with_test_suffix`) and both import it —
otherwise the tier that was not updated ends up pointed at the developer's own database.

## Config notes

- Root `pyproject.toml`'s `[tool.pytest.ini_options]` sets `--import-mode=importlib`. This is
  required, not optional: with four separate `tests/` dirs and no `__init__.py` in any of them,
  pytest's default `prepend` import mode raises an import collision the moment two packages each
  have a same-named file — which happens immediately, since every package has its own
  `conftest.py`. `importlib` mode gives each file a unique module identity without that
  restriction.
- `tests/` is excluded from mypy (`exclude = ["(^|/)tests/"]`) rather than added to `[tool.mypy]
  files`. Adding it would hit the same duplicate-module problem mypy has no equivalent fix for, and
  the Makefile's `mypy .` invocation ignores the `files` list entirely once a path is passed on the
  command line — so the exclusion has to be explicit. Ruff still lints test files normally.
- `D101`/`D102`/`D103` (missing-docstring rules) are ignored under `**/tests/**` — a descriptive
  test name replaces a docstring.
- `testpaths` only lists the four per-package `tests/` dirs, not the root `tests/` tree — a bare
  `uv run pytest` runs the unit tier only. `tests/integration`/`tests/e2e` are run explicitly via
  their own Makefile targets, so they don't fail the default run with "no tests ran" before either
  tier has real tests.

## Test databases (chat and scheduler)

Both services' unit tests are unit-scoped by location but hit **real** Postgres (and, for chat,
real Qdrant) — only the paid third-party APIs (Voyage embeddings, Claude) and the cross-service
gRPC boundary are faked. To keep that real I/O from colliding with manual/dev use of the same local
containers, each suite runs against an **isolated, `_test`-suffixed database**, never the one
`.env` points a locally-running service at:

- **Postgres (chat)**: `services/chat/tests/conftest.py` reads the base `DATABASE_URL` from
  `Settings()`, then (via `shared_db.testing.isolated_database_url`) overrides the `DATABASE_URL`
  env var to a `<db>_test`-suffixed database
  (`visitdoc_chat` → `visitdoc_chat_test`) *before* any `chat.*` module is imported by a test
  file. Because env vars take priority over `.env` file
  values in `pydantic-settings`, every later `Settings()` call — including Alembic's `env.py`,
  which reads `DATABASE_URL` directly — consistently resolves to the isolated database. A
  session-scoped, autouse fixture (`_apply_migrations_to_test_database`) runs `alembic upgrade
  head` against it once before any test, so the isolated database's schema is always current
  regardless of test order or whether it's ever been migrated before.
- **Qdrant**: the same suffixing helper (`shared_db.testing.with_test_suffix`) is applied to
  `QDRANT_COLLECTION_NAME` (default `faq_chunks`, overridden to `faq_chunks_test`), which
  `chat.repositories.qdrant_repository.COLLECTION_NAME` reads at import time. Unlike Postgres, no
  pre-provisioning is needed — a Qdrant collection is just an API-created resource, and the
  existing idempotent `ensure_collection` creates it on demand the first time a test touches it.
- **Postgres (scheduler)**: `services/scheduler/tests/conftest.py` does exactly the same for
  `SCHEDULER_DATABASE_URL` (`visitdoc_scheduler` → `visitdoc_scheduler_test`), with its own
  session-scoped `alembic upgrade head` against the scheduler's own migration tree. It has no
  Qdrant side. It additionally truncates every scheduling table before each test — the list read
  from `Base.metadata` via `all_table_names()`, not hand-written — since the repositories under
  test issue real commits and nothing else cleans them up.

Locally, `docker-compose.yml` creates all four databases automatically via
`/docker-entrypoint-initdb.d` init scripts (`docker/postgres-init/01-create-test-db.sql`,
`02-create-scheduler-dbs.sql`) — but only on a *fresh* `postgres_data` volume; a pre-existing
volume needs the missing ones created once by hand (see
`specs/005-scheduling-and-booking/quickstart.md`). In CI (`.github/workflows/ci.yml`), the `test`
job's Postgres service container is provisioned with `POSTGRES_DB: visitdoc_chat_test` directly and
an explicit step creates `visitdoc_scheduler_test` alongside it; Qdrant needs no such step at all
(ephemeral either way, collection created on demand).

## Async engine and event loops

SQLAlchemy's async engine (`chat.db.session`) is a module-level singleton, and its connection pool
binds to the *first* event loop that touches it. Multiple `TestClient(app)` instantiations across
different tests can each spin up their own event loop, which then collides with that binding —
surfacing as `RuntimeError: ... Future attached to a different loop` or "another operation is in
progress" on whichever test touches the engine second.

Two pieces of config fix this together — neither alone is sufficient:

- `asyncio_default_test_loop_scope = "session"` and `asyncio_default_fixture_loop_scope = "session"`
  in root `pyproject.toml`'s `[tool.pytest.ini_options]` — keeps every async test/fixture on the
  same event loop for the whole test session, rather than a fresh loop per test.
- An autouse fixture, `_reset_engine_pool_between_tests` in `conftest.py`, that disposes the
  engine's connection pool (`engine.dispose()`) after every test — still needed even with the
  loop-scope fix, since separate `TestClient(app)` instantiations across tests otherwise keep
  exhibiting pool-binding conflicts.

`scheduler` needs both pieces too, and has them: the loop-scope settings are shared (they live in
the root `pyproject.toml`), and `services/scheduler/tests/conftest.py` has its own
`_reset_engine_pool_between_tests`.

One further consequence, learned the hard way in both suites: an **async** test must not drive a
FastAPI app through `TestClient`, which runs request handling on a loop of its own and collides
with the engine the test has already bound. Async tests go through
`httpx.AsyncClient(transport=ASGITransport(app=app))` instead, which keeps the app and the test on
one loop — `services/chat/tests/test_chats_api.py`'s `_api()` and
`services/scheduler/tests/conftest.py`'s `admin_api()` are the two helpers that wrap this. Sync
tests may keep using `TestClient` freely.

## Commands

```bash
make test              # = make test-unit + make test-frontend
make test-unit          # uv run pytest (scoped to the four per-package tests/ dirs via testpaths)
make test-frontend      # vitest, in services/frontend
make test-integration   # uv run pytest tests/integration
make test-e2e           # uv run pytest tests/e2e
```

### Running them while you work

The tiers are not the same size, and treating them as if they were is what makes a change feel
slow. Rough shape on a developer machine: `test-frontend` ~15s, `test-integration` ~45s, the
scheduler and package suites ~2m together, and **`services/chat/tests` alone is 5-9 minutes** — it
holds most of the coverage and every test in it talks to real Postgres and real Qdrant.

- **Iterate with a scoped run**, not the whole tier: `uv run pytest services/chat/tests/test_turn_api.py`,
  or `-k` a name. Seconds instead of minutes, and the failure you are chasing is the only thing on
  screen.
- **Run the full tier in the background and collect it once**, rather than watching it — start it,
  do something else, read the result when it lands. Polling a nine-minute suite costs the nine
  minutes *and* your attention.
- **Run it once more before you call the work done.** A scoped run proves the thing you changed; it
  says nothing about what you changed underneath something else.
- **`pytest-xdist` is not the shortcut here.** The chat suite shares one module-level async engine
  bound to a session-scoped event loop, and the scheduler suite truncates every table between
  tests — parallel workers would collide on both. Speed has to come from scoping, not from workers.

`test-unit`, `test-frontend`, and `test-integration` all run in CI (`.github/workflows/ci.yml`).
`test-e2e` stays manual until that tier has real tests.
