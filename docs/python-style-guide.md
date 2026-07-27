# Python code style guide

## Commands

```bash
uv run ruff check .          # lint
uv run ruff check --fix .    # lint, applying auto-fixes
uv run ruff format .         # format
```

Rules are configured once in the root `pyproject.toml` (`[tool.ruff]`/`[tool.ruff.lint]`) and apply
to every Python workspace member.

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
