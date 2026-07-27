#!/usr/bin/env bash
# PostToolUse hook (Write|Edit): runs ruff+mypy on the edited file if it's a .py file.
# Reads the tool-call JSON on stdin; on lint/type errors, emits {"decision":"block","reason":...}
# so the errors are fed back to Claude. Silent (no output) when the file is clean or non-Python.
set -euo pipefail

input="$(cat)"
file="$(jq -r '.tool_input.file_path // .tool_response.filePath // empty' <<<"$input")"

[[ "$file" == *.py ]] || exit 0
[[ -f "$file" ]] || exit 0

repo_root="$(git -C "$(dirname "$file")" rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

output=""
if ! ruff_out="$(uv run ruff check "$file" 2>&1)"; then
    output+="$ruff_out"$'\n'
fi
if ! mypy_out="$(uv run mypy "$file" 2>&1)"; then
    output+="$mypy_out"$'\n'
fi

if [[ -n "$output" ]]; then
    jq -n --arg reason "$output" '{decision: "block", reason: $reason}'
fi
exit 0
