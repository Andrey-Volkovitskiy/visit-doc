"""Print the named keys of `.env` as KEY=value lines, for a Kubernetes Secret.

    uv run python scripts/env-subset.py ANTHROPIC_API_KEY VOYAGE_API_KEY | \
        kubectl create secret generic chat --from-env-file=/dev/stdin ...

    uv run python scripts/env-subset.py --from .env --from .env.public ADMIN_SECRET ...

Parsed with python-dotenv - what pydantic-settings reads `.env` with - because kubectl's
own `--from-env-file` keeps quotes as part of the value: `KEY="abc"` would reach the
cluster as `"abc"`, a key no API accepts. Only the keys named are printed, so the
addresses in `.env` that point at localhost never reach the cluster. A key that is
absent or blank is left out rather than sent empty, so the service's own default
applies, as it would locally.

`--from` names the files to read, in order, a later file's value replacing an earlier
one's - so the public cluster takes the API keys from `.env` and its own secrets from
`.env.public`. Without it, `.env` alone. A file named but missing stops the script: a
secret it was meant to replace would otherwise go to the cluster as the earlier file's.
"""

import sys
from pathlib import Path

from dotenv import dotenv_values


def _read(files: list[str]) -> dict[str, str | None]:
    """Merge `files` in order, a later file's value for a key replacing an earlier's."""
    values: dict[str, str | None] = {}
    for name in files:
        if not Path(name).is_file():
            sys.exit(f"{name}: no such file")
        values.update(dotenv_values(name))
    return values


def main(args: list[str]) -> None:
    """Print each named key the files give a non-blank value."""
    files: list[str] = []
    keys: list[str] = []
    rest = iter(args)
    for arg in rest:
        if arg == "--from":
            files.append(next(rest, ""))
            continue
        keys.append(arg)
    values = _read(files or [".env"])
    for key in keys:
        value = values.get(key)
        if value:
            if "\n" in value:
                sys.exit(
                    f"{key}: a multi-line value cannot be passed as an env-file line"
                )
            print(f"{key}={value}")


if __name__ == "__main__":
    main(sys.argv[1:])
