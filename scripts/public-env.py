"""Create or check `.env.public`: the public cluster's own secrets.

    uv run python scripts/public-env.py create  # once; never overwrites
    uv run python scripts/public-env.py check   # before every public deploy

The public cluster takes its API keys from `.env`, as the local one does, and two values
from this file instead, which must not be the development ones:

- POSTGRES_PASSWORD. Postgres stores it when its volume is first initialised and never
  reads it again, so changing it here later locks the services out of the existing
  database: it is created once and kept.
- ADMIN_SECRET, which guards `/admin` (reached through `make k8s-admin`).

Both are random hex, so neither can contain the `@`, `:` or `/` a database URL cannot
hold unescaped. The file is gitignored and dockerignored, like `.env`.
"""

import secrets
import sys
from pathlib import Path

from dotenv import dotenv_values

PUBLIC_ENV = Path(".env.public")
_KEYS = ("POSTGRES_PASSWORD", "ADMIN_SECRET")
# The local cluster's default (Makefile's POSTGRES_PASSWORD), refused here by name.
_DEFAULT_PASSWORD = "visitdoc"


def create() -> None:
    """Write `.env.public` with a fresh random value for each key, unless it exists."""
    if PUBLIC_ENV.exists():
        sys.exit(
            f"{PUBLIC_ENV} exists - not overwritten (its password is in the database)"
        )
    lines = [
        "# The public cluster's own secrets - see scripts/public-env.py. Never commit.",
        *(f"{key}={secrets.token_hex(24)}" for key in _KEYS),
    ]
    PUBLIC_ENV.write_text("\n".join(lines) + "\n")
    PUBLIC_ENV.chmod(0o600)
    print(f"wrote {PUBLIC_ENV} ({', '.join(_KEYS)}; values not shown)")


def _problems() -> list[str]:
    """Every reason `.env.public` is not fit to deploy with; empty when it is."""
    if not PUBLIC_ENV.is_file():
        return ["missing: run `make k8s-public-env` once"]
    public = dotenv_values(PUBLIC_ENV)
    local = dotenv_values(".env") if Path(".env").is_file() else {}
    problems = [f"{key} is not set" for key in _KEYS if not public.get(key)]
    password = public.get("POSTGRES_PASSWORD") or ""
    if password == _DEFAULT_PASSWORD:
        problems.append("POSTGRES_PASSWORD is the local default")
    if any(char in password for char in "@:/"):
        problems.append(
            "POSTGRES_PASSWORD holds @, : or /, which a database URL cannot"
        )
    admin = public.get("ADMIN_SECRET")
    if admin and admin == local.get("ADMIN_SECRET"):
        problems.append("ADMIN_SECRET is the same as .env's")
    return problems


def check() -> None:
    """Exit non-zero, naming each problem, unless `.env.public` is fit to deploy."""
    problems = _problems()
    if problems:
        sys.exit("\n".join(f"{PUBLIC_ENV}: {problem}" for problem in problems))


if __name__ == "__main__":
    commands = {"create": create, "check": check}
    command = commands.get(sys.argv[1] if len(sys.argv) > 1 else "")
    if command is None:
        sys.exit(f"usage: {sys.argv[0]} {{{' | '.join(commands)}}}")
    command()
