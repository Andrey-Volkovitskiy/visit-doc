"""Print the named keys of `.env` as KEY=value lines, for a Kubernetes Secret.

    uv run python scripts/env-subset.py ANTHROPIC_API_KEY VOYAGE_API_KEY | \
        kubectl create secret generic chat --from-env-file=/dev/stdin ...

Parsed with python-dotenv - what pydantic-settings reads `.env` with - because kubectl's
own `--from-env-file` keeps quotes as part of the value: `KEY="abc"` would reach the
cluster as `"abc"`, a key no API accepts. Only the keys named are printed, so the
addresses in `.env` that point at localhost never reach the cluster. A key that is
absent or blank is left out rather than sent empty, so the service's own default
applies, as it would locally.
"""

import sys

from dotenv import dotenv_values


def main(keys: list[str]) -> None:
    """Print each of `keys` that `.env` gives a non-blank value."""
    values = dotenv_values(".env")
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
