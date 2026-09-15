#!/usr/bin/env python3
"""Read or set `effortLevel` in ~/.claude/settings.json, and nothing else.

    set-effort.py            print the current value ("<unset>" if absent)
    set-effort.py <level>    set low|medium|high|xhigh, print the value read back
    set-effort.py unset      remove the key (restores a baseline that was "<unset>")

Exists so the review loop's permission rule can allow this one script rather
than arbitrary Python: the level is checked against a closed set and no other
key is touched.
"""

import json
import pathlib
import sys

LEVELS = ("low", "medium", "high", "xhigh")
SETTINGS = pathlib.Path.home() / ".claude" / "settings.json"


def current() -> str:
    """Return the persisted effort level, or "<unset>" when the key is absent."""
    return str(json.loads(SETTINGS.read_text()).get("effortLevel", "<unset>"))


def main(argv: list[str]) -> int:
    """Print the level, or set (or remove) it and print the value read back.

    Returns: the process exit code (2 for an unknown level or extra arguments)
    """
    if len(argv) == 0:
        print(current())
        return 0
    if len(argv) != 1 or argv[0] not in (*LEVELS, "unset"):
        print(f"usage: set-effort.py [{'|'.join(LEVELS)}|unset]", file=sys.stderr)
        return 2
    settings = json.loads(SETTINGS.read_text())
    if argv[0] == "unset":
        settings.pop("effortLevel", None)
    else:
        settings["effortLevel"] = argv[0]
    SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
    print(current())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
