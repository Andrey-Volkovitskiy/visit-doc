#!/usr/bin/env bash
# PostToolUse hook (Write|Edit): type-checks the frontend when a .ts/.tsx file changes.
# Reads the tool-call JSON on stdin; on type errors, emits {"decision":"block","reason":...}
# so they are fed back to Claude. Silent when the file is clean or is not frontend TypeScript.
#
# The counterpart to py-check.sh, and needed for the same reason: nothing else catches a
# TypeScript error close to the edit. `npm test` runs vitest, which transpiles via esbuild and
# never type-checks, so a type error is invisible to the whole test suite.
#
# Checking goes through `npm run typecheck` (`tsc -b`) rather than a direct `tsc` call, so this
# hook and CI run the identical command. `tsc --noEmit` is NOT usable here: tsconfig.json is a
# solution file (`"files": []` plus `references`), so an invocation that does not follow those
# references checks zero files and exits 0 - passing while seeing nothing.
#
# `tsc -b` is project-wide but incremental (it writes *.tsbuildinfo, already gitignored), so the
# first run costs a few seconds and later ones are fast. Project-wide is also the right scope:
# editing one module can break a different file that consumes it.
set -euo pipefail

input="$(cat)"
file="$(jq -r '.tool_input.file_path // .tool_response.filePath // empty' <<<"$input")"

[[ "$file" == *.ts || "$file" == *.tsx ]] || exit 0
[[ -f "$file" ]] || exit 0

repo_root="$(git -C "$(dirname "$file")" rev-parse --show-toplevel 2>/dev/null || pwd)"
frontend="$repo_root/services/frontend"

# The only TypeScript project in the repo. Anything else is left alone rather than checked
# against a config that was never meant for it.
case "$(realpath "$file")" in
    "$frontend"/*) ;;
    *) exit 0 ;;
esac

# An environment that never ran `npm ci` is not a type error to report.
[[ -d "$frontend/node_modules" ]] || exit 0

cd "$frontend"
if ! out="$(npm run --silent typecheck 2>&1)"; then
    jq -n --arg reason "$out" '{decision: "block", reason: $reason}'
fi
exit 0
