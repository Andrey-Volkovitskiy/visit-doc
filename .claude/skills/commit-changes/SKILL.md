---
name: commit-changes
description: Commit the repo's currently unstaged/untracked changes as a single conventional commit, prefixed with feat:/fix:/docs:/ci:/dev: and, when the change belongs to a spec-kit feature under specs/<number>-<name>/, that feature number (e.g. "feat: 012 - add patient auth endpoint") — then sync with the remote: git pull --rebase followed by git push. Use whenever the user asks to commit, save, check in, or "commit and push" the current changes in this repo — "commit this", "commit my changes", "save my work", "check this in", "push my changes" — even if they don't specify a message or prefix themselves; infer the prefix and feature number from the diff and branch rather than asking for them, but always show the full plan (commit + pull + push) and wait for one approval before running any of it.
---

# Commit changes with this repo's convention

This repo prefixes every commit with a type, and — when the change belongs to a spec-kit
feature — the feature number, e.g. `feat: 012 - add patient auth endpoint` (see `git log` for
existing examples). This skill produces one commit covering everything currently
unstaged/untracked, with that prefix and number worked out automatically instead of asked for,
then syncs it with the remote (pull --rebase, then push) so the branch doesn't just sit committed
locally.

## Steps

1. **Check there's something to commit.** Run `git status --porcelain` (and a plain `git status`
   for your own reading). If it's empty, tell the user there's nothing unstaged and stop here.

2. **Screen for anything that shouldn't be committed.** Look for files that smell like secrets —
   `.env`, `credentials.json`, `*.pem`, `*.key`, and similar. If any appear, read enough to confirm,
   then flag them to the user by name and exclude them from staging unless they explicitly say to
   include them anyway.

3. **Classify the change** by reading `git diff` (unstaged) plus the untracked files in the status
   output. Pick the single prefix that best matches the *dominant* concern — don't try to enumerate
   every touched file in the message:
   - `feat:` — new capability or functionality
   - `fix:` — bug fix
   - `docs:` — documentation-only changes (README, `docs/**`, comment-only edits)
   - `ci:` — CI pipeline config (`.github/workflows/**`)
   - `dev:` — development-process/tooling changes that aren't CI: a new Claude Code skill or hook,
     pre-commit config, lint/type-check config, `Makefile` changes, editor config, etc.

   If a change genuinely spans two concerns (e.g. a feature plus its docs), still pick one prefix —
   whichever a reviewer would say the commit is *about*.

4. **Work out the feature number**, if any:
   - Check whether any changed path lives under `specs/<number>-<feature-name>/`.
   - Check whether the current branch name encodes a `<number>-<feature-name>` (spec-kit's own
     branch convention).
   - If exactly one feature number is implicated, put it right after the prefix:
     `feat: 012 - <summary>`.
   - If no feature folder or branch is implicated, omit the number: `feat: <summary>`.
   - If the changes clearly touch **two or more different** feature numbers, don't guess — stop and
     ask the user which one to use, or whether this should be split into separate commits instead.

5. **Draft one commit message**: `<prefix>: [<number> - ]<short, imperative summary>`. Match the
   tone of this repo's existing history (`git log`) rather than inventing a new style.

6. **Show the whole plan before touching git.** Lead with the commit message, quoted, on its own
   line — that's the one thing the user needs to register at a glance — then a `-------` line, then
   the supporting details below it. This way approving is a one-second skim, but the details are
   right there for anyone who wants them. Always use this exact shape:

   ```
   "<the exact draft commit message>"
   -------
   Staging: <files to be staged, or "all of the above">
   Excluding: <files left out and why, or "nothing">
   After commit: git pull --rebase then git push (upstream: <branch>'s tracked remote branch)
   ```

   Wait for one explicit approval that covers all of it — this repo's convention is to only touch
   git on a clear go-ahead, and pushing reaches beyond your own machine, so it's worth being just as
   explicit about that step as about the commit itself.

7. **On approval, commit**: stage the approved files by name (never `git add -A`/`git add .`),
   commit with the message via a heredoc, and follow the same git safety rules as any other commit
   here — no `--no-verify`, no amending, always a new commit.

8. **Sync with the remote**: run `git pull --rebase` to replay the new commit on top of whatever
   landed upstream since you last synced, keeping history linear. Then `git push`.
   - **If the rebase hits a conflict**: stop immediately. Don't attempt to resolve it yourself —
     run `git status` to show the user exactly which files conflict, leave the rebase paused
     (don't `git rebase --abort` unless they ask), and hand it back for them to resolve (either
     by hand or by asking you to help interactively from there).
   - **If the push is rejected for any other reason** (e.g. something landed on the remote between
     your pull and your push), don't retry blindly or force-push — re-run `git pull --rebase` once
     more and try again; if it fails a second time, stop and explain what happened.
   - Never `--force`/`--force-with-lease` push, and never push to a branch other than the current
     one's actual upstream.

9. **Report back**: the resulting commit hash and message, and confirm the push succeeded (or
   explain exactly where it stopped, if it did).

## Notes

- This repo's commits don't carry a `Co-Authored-By` trailer (see e.g. the existing
  `dev: Refactor Spek-Kit Constitution` commit) — don't add one unless the user asks for it.
- Never invent a feature number. It comes from an actual `specs/<number>-...` path or branch name,
  never from guessing at what a change "should" belong to.
- If a pre-commit hook rejects the commit, fix the underlying issue and commit again as a new
  commit — don't bypass the hook.
- The approval in step 6 authorizes this one run end-to-end (commit, pull, push). It doesn't carry
  over to a future invocation — ask again next time the skill runs.
