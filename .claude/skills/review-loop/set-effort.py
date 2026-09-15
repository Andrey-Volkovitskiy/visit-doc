#!/usr/bin/env python3
"""Read or set the top-level `effortLevel` in Claude Code's user settings.

    set-effort.py            print the current value ("unset" if absent)
    set-effort.py <level>    set low|medium|high|xhigh, print the value read back
    set-effort.py unset      remove the key

What it prints for an absent key is an argument it accepts, so a baseline read
before raising the level is restored by passing that output back verbatim.

The file is `$CLAUDE_CONFIG_DIR/settings.json`, or `~/.claude/settings.json`
when that variable is unset. A missing file reads as "unset". A write
re-serializes the file with two-space indentation and changes no other key's
value; it goes to a temporary file that is then renamed over the original, so an
interrupted write leaves the previous file whole.

Exists so the review loop's permission rule can allow this one script rather
than arbitrary Python. The rule approves the command line, not this file's
content: the level is checked against a closed set only while this file says so.

The key written here is not all that decides a session's effort. In Claude Code
2.1.272 each of these outranks it: a `modelSettings.<model>.effortLevel` in the
same file (where `/effort` saves a pick), `ultracode: true`,
`CLAUDE_CODE_EFFORT_LEVEL`, and an effort setting in the project's
`.claude/settings.json` or `.claude/settings.local.json` (looked for under the
current directory). Every run prints a `warning:` line on stderr for each of
those it finds. It cannot see an `--effort` flag, a level picked in-session, or
managed settings, so the absence of a warning does not prove the printed level
is the one in effect.
"""

import contextlib
import json
import os
import pathlib
import stat
import sys
import tempfile

LEVELS = ("low", "medium", "high", "xhigh")
UNSET = "unset"
PROJECT_FILES = (".claude/settings.json", ".claude/settings.local.json")


def settings_path() -> pathlib.Path:
    """Return the user settings file, honouring CLAUDE_CONFIG_DIR."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    home = pathlib.Path(configured) if configured else pathlib.Path.home() / ".claude"
    return home / "settings.json"


def load(path: pathlib.Path) -> dict[str, object]:
    """Return the parsed settings, or an empty mapping when the file is absent."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    settings: dict[str, object] = json.loads(text)
    return settings


def write(path: pathlib.Path, settings: dict[str, object]) -> None:
    """Replace the file's content in one rename, keeping its mode and symlink."""
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=target.parent, prefix=".settings.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def level(settings: dict[str, object]) -> str:
    """Return the top-level effort level, or UNSET when the key is absent."""
    return str(settings.get("effortLevel", UNSET))


def overrides(user_file: pathlib.Path, user_settings: dict[str, object]) -> list[str]:
    """Describe each setting found that outranks the top-level key."""
    found = []
    variable = os.environ.get("CLAUDE_CODE_EFFORT_LEVEL")
    if variable:
        found.append(f"CLAUDE_CODE_EFFORT_LEVEL={variable} outranks it")
    sources: list[tuple[str, dict[str, object]]] = [(str(user_file), user_settings)]
    for name in PROJECT_FILES:
        project_file = pathlib.Path(name).resolve()
        if not project_file.exists() or project_file == user_file.resolve():
            continue
        try:
            content = load(project_file)
        except (OSError, ValueError) as error:
            found.append(f"{project_file} could not be checked ({error})")
            continue
        if "effortLevel" in content:
            found.append(
                f"{project_file}: effortLevel={content['effortLevel']} outranks it"
            )
        sources.append((str(project_file), content))
    for label, content in sources:
        if content.get("ultracode") is True:
            found.append(f"{label}: ultracode=true outranks it")
        model_settings = content.get("modelSettings")
        if not isinstance(model_settings, dict):
            continue
        for model, entry in model_settings.items():
            if isinstance(entry, dict) and "effortLevel" in entry:
                value = entry["effortLevel"]
                found.append(
                    f"{label}: modelSettings.{model}.effortLevel={value} outranks it"
                )
    return found


def main(argv: list[str]) -> int:
    """Print the level, or set (or remove) it and print the value read back.

    Returns: the process exit code (2 for an unknown level or extra arguments)
    """
    if len(argv) > 1 or (argv and argv[0] not in (*LEVELS, UNSET)):
        print(f"usage: set-effort.py [{'|'.join(LEVELS)}|{UNSET}]", file=sys.stderr)
        return 2
    path = settings_path()
    if argv:
        settings = load(path)
        if argv[0] == UNSET:
            settings.pop("effortLevel", None)
        else:
            settings["effortLevel"] = argv[0]
        write(path, settings)
    settings = load(path)
    print(level(settings))
    for override in overrides(path, settings):
        print(f"warning: {override}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
