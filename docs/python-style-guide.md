# Python code style guide

## Commands

```bash
uv run ruff check .          # lint
uv run ruff check --fix .    # lint, applying auto-fixes
uv run ruff format .         # format
uv run mypy .                # type-check (strict mode)
```

Rules are configured once in the root `pyproject.toml` (`[tool.ruff]`/`[tool.ruff.lint]`,
`[tool.mypy]`) and apply to every Python workspace member.

`Settings`-style classes (`pydantic_settings.BaseSettings` subclasses with required, env-populated
fields) need the `pydantic.mypy` plugin already enabled in `[tool.mypy]` (`plugins =
["pydantic.mypy"]`) — without it, strict mypy misreads env-sourced fields as missing constructor
arguments and raises false `call-arg` errors on `Settings()`.

## Style

- Type-annotate every function/method, including the return type:
  `def foo(a: str, b: list[int]) -> dict[str, tuple[int, bool]]: ...`
- Give every function/method a short docstring describing what it does. Only add `Args`/`Returns`
  sections when they aren't already obvious from the argument names and type annotations.
- Document exceptions the function can raise — whether raised directly in its body or propagated
  from another function/method in this codebase that it calls — with a `Raises:` line, e.g.
  `Raises: ValueError`. Exceptions from third-party/standard-library calls don't need to be
  documented.
- Prefer a "happy path" structure (early returns) over nested `if`/`else`:
  ```python
  def foo(a: int | None) -> int:
      if a is None:
          return 0
      if a <= 0:
          return 0
      return bar(a)  # all checks passed, proceed with main logic
  ```
- Omit unnecessary `else` blocks:
  ```python
  def foo(a: int) -> int:
      if a < 0:
          return 0
      return a
  ```

## Specific cases
- Insead of 
```python
  @contextmanager
  def foo() -> Iterator[...]
```
Follow the modern recomendation using:
```python
  @contextmanager
  def foo() -> Generator[...]
```

- Insead of 
```python
  @asynccontextmanager
  def foo() -> AsyncGenerator[...]
```
Follow the modern recomendation using:
```python
  @asynccontextmanager
  def foo() -> AsyncGenerator[...]
```

## Logging

Every service logs via `structlog`, never stdlib `logging` directly. `services/chat/src/chat/core/logging.py`
and `services/chat/src/chat/core/correlation.py` are the reference implementation — mirror their
shape in any other service that needs logging (e.g. `services/scheduler/src/scheduler/core/`),
rather than inventing a different approach per service.

- **One processor chain per service**, configured once via a `configure_logging(settings)` call at
  app startup: merge correlation-id context → add log level → add timestamp → truncate long
  strings → redact secrets → render. Every log call flows through this single chain, so changing
  how logs are rendered later (e.g. a JSON/Langfuse-ready renderer) is a one-line change in that
  one module, not a rewrite of every call site.
- **Call sites never call `structlog.get_logger()` directly.** Use the service's own `get_logger()`
  helper instead, which wraps the structlog logger so a failure anywhere in the processor chain
  never raises out to the caller — servicing the request always takes priority over a log entry
  being delivered. A dropped entry is best-effort noted (e.g. to stderr) but never retried or
  surfaced to the caller.
- **Correlation IDs** (a request/turn/operation ID) are bound via `structlog.contextvars`
  (`bound_contextvars`/`bind_contextvars`/`clear_contextvars`), not threaded through every function
  as an explicit parameter — `structlog.contextvars` is scoped to the current `asyncio` task, so
  concurrent requests never leak each other's bound values, and business-logic signatures stay free
  of observability-only parameters.
- **Never log a secret** — see "Secrets in logs" below.

### Secrets in logs

Whenever a new secret, credential, token, or password-bearing field is added to a service's
`Settings` — including a URL that embeds one (e.g. a database or message-broker connection string
with a password in it) — it MUST be added to that service's redaction processor's known-secret list
in the *same change* that introduces it, so it can never appear in the clear, even if it later
surfaces inside an unrelated exception message.

In `services/chat/src/chat/core/logging.py`, that means adding the new `Settings` field name to
`_SECRET_SETTINGS_FIELDS` (a plain secret value) or `_SECRET_URL_SETTINGS_FIELDS` (a URL whose
embedded password is the secret) — both declared as constants at the top of the module specifically
so this is a one-line addition, not a change buried inside `_known_secret_values`'s body. Mirror the
same two-constants pattern in any other service's logging module.

This is on top of, not instead of, key-name-based redaction (any field named `password`, `token`,
`secret`, `api_key`, `credential`, `authorization`, etc. is redacted regardless of its value) —
neither check alone is sufficient: a secret logged under an unexpected key name is only caught by
the known-value list, and a secret introduced under a recognizable key name is caught even before
it's added to the known-value list.
