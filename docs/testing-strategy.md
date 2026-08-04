# Testing strategy

## Layout

- **Unit tests are colocated per workspace member**: `services/chat/tests/`,
  `services/scheduler/tests/`, `packages/shared-models/tests/`, `packages/shared-proto/tests/`.
  Each member already owns its own `pyproject.toml`/`src/` — its `tests/` dir is part of that same
  self-contained unit, not a separate tree. A package's `tests/` dir *is* the unit tier; there's no
  extra `tests/unit/` nesting.
- **Integration and e2e tests are centralized at the repo root**: `tests/integration/`,
  `tests/e2e/`. These inherently cross service boundaries (e.g. `chat` calling `scheduler` over
  gRPC, or a full frontend+chat+scheduler flow), so they can't belong to a single package. Both are
  placeholders today — no integration surface or frontend exists yet (see `docs/ROADMAP.md`);
  populate them once there's something to test.

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

## Test databases (chat)

`chat`'s unit tests (`services/chat/tests/`) are unit-scoped by location, but hit **real**
Postgres and Qdrant — only the paid third-party APIs (Voyage embeddings, Claude) are faked (see
`conftest.py`'s `fake_embed_texts`/`FakeAnthropicStream`). To keep that real I/O from colliding
with manual/dev use of the same local containers, tests run against **isolated database and
collection names**, never the ones `.env` points a locally-running `chat` service at:

- **Postgres**: `conftest.py` reads the base `DATABASE_URL` from `Settings()`, then overrides the
  `DATABASE_URL` env var to a `<db>_test`-suffixed database (`visitdoc` → `visitdoc_test`) *before*
  any `chat.*` module is imported by a test file. Because env vars take priority over `.env` file
  values in `pydantic-settings`, every later `Settings()` call — including Alembic's `env.py`,
  which reads `DATABASE_URL` directly — consistently resolves to the isolated database. A
  session-scoped, autouse fixture (`_apply_migrations_to_test_database`) runs `alembic upgrade
  head` against it once before any test, so the isolated database's schema is always current
  regardless of test order or whether it's ever been migrated before.
- **Qdrant**: the same suffixing helper (`_with_test_suffix`) is applied to
  `QDRANT_COLLECTION_NAME` (default `faq_chunks`, overridden to `faq_chunks_test`), which
  `chat.repositories.qdrant_repository.COLLECTION_NAME` reads at import time. Unlike Postgres, no
  pre-provisioning is needed — a Qdrant collection is just an API-created resource, and the
  existing idempotent `ensure_collection` creates it on demand the first time a test touches it.

Locally, `docker-compose.yml` creates the `visitdoc_test` Postgres database automatically via a
`/docker-entrypoint-initdb.d` init script (`docker/postgres-init/01-create-test-db.sql`) — but only
on a *fresh* `postgres_data` volume; a pre-existing volume needs it created once by hand (`docker
exec visitdoc-postgres psql -U visitdoc -c "CREATE DATABASE visitdoc_test;"`). In CI
(`.github/workflows/ci.yml`), the `test` job's Postgres service container is provisioned with
`POSTGRES_DB: visitdoc_test` directly, and Qdrant needs no such step at all (ephemeral either way,
collection created on demand) — so CI required no extra setup for the Qdrant side of this scheme.

## Async engine and event loops (chat)

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

Any future service with async SQLAlchemy tests (`scheduler` will need this once it has its own
Postgres-backed test suite) needs both pieces, not just one.

## Commands

```bash
make test              # = make test-unit
make test-unit          # uv run pytest (scoped to the four per-package tests/ dirs via testpaths)
make test-integration   # uv run pytest tests/integration
make test-e2e           # uv run pytest tests/e2e
```

Only `test-unit` runs in CI so far (a `test` job in `.github/workflows/ci.yml`, alongside the
`pre-commit` job). `test-integration`/`test-e2e` stay manual until those tiers have real tests.
