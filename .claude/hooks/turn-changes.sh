#!/usr/bin/env bash
# Prints the files a turn changed, grouped created / updated / deleted.
#
# Two halves of one hook, in one script - they only make sense as a pair:
#   snapshot  (UserPromptSubmit) - records the file list plus a timestamp marker
#   report    (Stop)             - diffs the tree against that snapshot, prints, re-snapshots
#
# Git decides *which* files count: tracked-plus-untracked minus everything .gitignore covers,
# so a `uv sync` rewriting .venv or an npm install under node_modules never reaches the output,
# plus the handful of ignored paths named in watch_ignored below. Detection deliberately does
# not key off the Write/Edit tools - plenty of edits here go through Bash (sed, heredocs) or
# are made by py-check.sh's `ruff format`, and none of those are a tool call naming a file.
#
# created/deleted fall out of the set difference. `updated` is the intersection filtered to
# "mtime at or after the marker" rather than filtered by `git status`, because status only
# reports a difference from HEAD: a file edited twice in one turn, or edited back to its
# committed content, is invisible to it but not to mtime. The flip side is that mtime counts
# a rewrite that changed nothing - a `touch`, or ruff reformatting an already-formatted file.
#
# Reporting rolls the snapshot forward instead of clearing it, so a turn that stops more than
# once (a background task waking Claude back up) reports each stop's own delta, not a repeat.
#
# Output goes to /dev/tty: a Stop hook's stdout only surfaces in transcript mode (ctrl-R),
# which is not "the same terminal Claude Code prints to" in any useful sense. systemMessage
# is the fallback for a session with no controlling terminal (it cannot carry color).
set -euo pipefail

mode="${1:?usage: turn-changes.sh snapshot|report}"

input="$(cat)"
session="$(jq -r '.session_id // "default"' <<<"$input")"
cwd="$(jq -r '.cwd // empty' <<<"$input")"
[[ -n "$cwd" ]] || cwd="$PWD"

repo_root="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$repo_root" ]] || exit 0
cd "$repo_root"

state_dir="${TMPDIR:-/tmp}/claude-turn-changes/$session"
marker="$state_dir/marker"
before="$state_dir/before"
after="$state_dir/after"

# Gitignored paths that are still worth reporting: files edited by hand that happen to be
# ignored. `--exclude-standard` alone hides these, which is right for .venv and node_modules
# but wrong for .env - a turn that edited only .env reported nothing at all. Add a pathspec
# here to un-hide another ignored file; anything not listed stays excluded.
watch_ignored=('.env' '.env.*')

# `--cached` keeps listing a path that was deleted from the worktree, so filter to what is
# actually on disk - otherwise a deletion never leaves the "before" set.
list_files() {
    {
        git ls-files --cached --others --exclude-standard
        git ls-files --others --ignored --exclude-standard -- "${watch_ignored[@]}"
    } |
        while IFS= read -r f; do
            if [[ -e "$f" ]]; then printf '%s\n' "$f"; fi
        done | sort -u
}

# List first, marker second: a file written in the gap is then missed rather than reported as
# updated when nothing touched it.
take_snapshot() {
    mkdir -p "$state_dir"
    list_files > "$before"
    : > "$marker"
}

if [[ "$mode" == snapshot ]]; then
    take_snapshot
    exit 0
fi

# A Stop with no snapshot (hook added mid-session, or `claude -p`) has nothing to diff
# against. Bootstrap one and stay quiet rather than reporting the whole tree as created.
if [[ ! -f "$before" || ! -f "$marker" ]]; then
    take_snapshot
    exit 0
fi

list_files > "$after"

mapfile -t created < <(comm -13 "$before" "$after")
mapfile -t deleted < <(comm -23 "$before" "$after")
# `find -newer` is deliberately not used here. It tests strictly-greater-than, and ext4 stamps
# writes from a tick-granular clock, so a file written in the same jiffy as the marker gets a
# byte-identical mtime and ties - silently dropping a real edit. `>=` reports the tie instead;
# the worst case is naming a file that was written at snapshot time rather than after it.
# Both sides of the comparison are filesystem mtimes so they come from one clock - reading the
# threshold from date(1) instead would compare a fine-grained clock against a coarse one.
marker_time="$(stat -c '%.9Y' "$marker")"
mapfile -t updated < <(comm -12 "$before" "$after" |
    xargs -d '\n' -r stat -c '%.9Y %n' -- 2>/dev/null |
    awk -v m="$marker_time" '$1 >= m { sub(/^[^ ]+ /, ""); print }')

take_snapshot

if (( ${#created[@]} + ${#updated[@]} + ${#deleted[@]} == 0 )); then
    exit 0
fi

# Braces around the probe, not `: > /dev/tty 2>/dev/null`: bash applies redirections left to
# right and reports the failed one on its own stderr before 2>/dev/null is in effect, so the
# unbraced form prints "No such device or address" on every session that has no terminal.
if { : > /dev/tty; } 2>/dev/null; then
    green=$'\033[92m'; blue=$'\033[94m'; yellow=$'\033[93m'
    dim=$'\033[2m'; reset=$'\033[0m'
else
    green=""; blue=""; yellow=""; dim=""; reset=""
fi

# Paths come out of git repo-relative, which is already what we want in the common case of a
# prompt whose cwd is the repo root; the realpath-per-path cost is only paid below that.
relativize() {
    if [[ "$cwd" == "$repo_root" ]]; then
        cat
    else
        while IFS= read -r f; do realpath -ms --relative-to="$cwd" "$repo_root/$f"; done
    fi
}

body="${dim}Files changed this turn${reset}"$'\n'
append_group() {
    local color="$1" label="$2"; shift 2
    (( $# )) || return 0
    body+="  ${color}${label} (${#})${reset}"$'\n'
    local f
    while IFS= read -r f; do
        body+="    ${color}${f}${reset}"$'\n'
    done < <(printf '%s\n' "$@" | relativize)
}

append_group "$green" created "${created[@]}"
append_group "$blue" updated "${updated[@]}"
append_group "$yellow" deleted "${deleted[@]}"

if [[ -n "$reset" ]]; then
    printf '%s' "$body" > /dev/tty
else
    jq -n --arg msg "$body" '{systemMessage: $msg}'
fi
