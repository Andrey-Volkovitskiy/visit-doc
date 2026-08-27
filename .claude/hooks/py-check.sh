#!/usr/bin/env bash
# PostToolUse hook (Write|Edit): formats, lints, and type-checks the edited Python file.
# Reads the tool-call JSON on stdin; on lint/type errors, emits {"decision":"block","reason":...}
# so the errors are fed back to Claude. Silent (no output) when the file is clean or non-Python.
#
# Formatting is applied rather than reported: `ruff format` is already the project's one
# formatter (`make format`), so a hook that only complained would just add a round trip.
#
# mypy is deliberately skipped under tests/. The project excludes tests from type checking
# (pyproject.toml's `exclude = ["(^|/)tests/"]`), but that exclusion is bypassed the moment a
# path is passed on the command line - so checking a test file here reports errors that
# `make typecheck` does not, and that nobody is expected to fix.
set -euo pipefail

input="$(cat)"
file="$(jq -r '.tool_input.file_path // .tool_response.filePath // empty' <<<"$input")"

[[ "$file" == *.py ]] || exit 0
[[ -f "$file" ]] || exit 0

repo_root="$(git -C "$(dirname "$file")" rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

output=""

# Format first, so the checks below see the formatted text and mechanical layout
# violations (E501 on a reflowable line, import order) never reach Claude at all.
if ! format_out="$(uv run ruff format -q "$file" 2>&1)"; then
    output+="$format_out"$'\n'
fi

if ! ruff_out="$(uv run ruff check "$file" 2>&1)"; then
    output+="$ruff_out"$'\n'
fi

if [[ "$file" != */tests/* ]]; then
    if ! mypy_out="$(uv run mypy "$file" 2>&1)"; then
        output+="$mypy_out"$'\n'
    fi
fi

if [[ -n "$output" ]]; then
    jq -n --arg reason "$output" '{decision: "block", reason: $reason}'
fi
exit 0
