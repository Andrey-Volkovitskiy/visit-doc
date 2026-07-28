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

## Commands

```bash
make test              # = make test-unit
make test-unit          # uv run pytest (scoped to the four per-package tests/ dirs via testpaths)
make test-integration   # uv run pytest tests/integration
make test-e2e           # uv run pytest tests/e2e
```

Only `test-unit` runs in CI so far (a `test` job in `.github/workflows/ci.yml`, alongside the
`pre-commit` job). `test-integration`/`test-e2e` stay manual until those tiers have real tests.
