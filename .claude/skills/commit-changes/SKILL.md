---
name: commit-changes
description: Commit the repo's currently unstaged/untracked changes as a single conventional commit, prefixed with feat:/fix:/docs:/ci:/dev: and, when the change belongs to a spec-kit feature under specs/<number>-<name>/, that feature number (e.g. "feat: 012 - add patient auth endpoint") — then sync with the remote: git pull --rebase followed by git push. Use whenever the user asks to commit, save, check in, or "commit and push" the current changes in this repo — "commit this", "commit my changes", "save my work", "check this in", "push my changes" — even if they don't specify a message or prefix themselves. Infer the prefix and feature number from the diff and branch rather than asking for them, and run the whole sequence (commit + pull + push) without pausing for approval — invoking the skill *is* the approval; report what happened afterwards.
---

# Commit changes with this repo's convention

This repo prefixes every commit with a type, and — when the change belongs to a spec-kit
feature — the feature number, e.g. `feat: 012 - add patient auth endpoint` (see `git log` for
existing examples). This skill produces one commit covering everything currently
unstaged/untracked, with that prefix and number worked out automatically instead of asked for,
then syncs it with the remote (pull --rebase, then push) so the branch doesn't just sit committed
locally.

**Run it end to end without asking.** The user invoking this skill is the go-ahead for all three
steps, including the push — don't draft a plan and wait, don't ask which prefix to use, and don't
ask again before pushing. The report at the end is how the user reviews what happened. The only
things that stop the run are the genuine blockers called out in steps 1 and 8.

## Steps

1. **Check there's something to commit.** Run `git status --porcelain` (and a plain `git status`
   for your own reading). If it's empty, tell the user there's nothing unstaged and stop here.

2. **Screen for anything that shouldn't be committed.** Look for files that smell like secrets —
   `.env`, `credentials.json`, `*.pem`, `*.key`, and similar. If any appear, read enough to confirm,
   then leave them out of staging and name them in the final report. Excluding them is the default
   and needs no confirmation; the user can commit them deliberately by saying so.

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
   - If the changes touch **two or more different** feature numbers, don't guess and don't stop:
     use the number the current branch encodes, or omit the number entirely if the branch encodes
     none. Say which numbers were implicated in the report, so the user can amend or split the
     commit if that wasn't what they wanted.

5. **Draft one commit message**: `<prefix>: [<number> - ]<short, imperative summary>`. Match the
   tone of this repo's existing history (`git log`) rather than inventing a new style.

6. **Commit**: stage the files by name (never `git add -A`/`git add .`), commit with the message
   via a heredoc, and follow the same git safety rules as any other commit here — no `--no-verify`,
   no amending, always a new commit.

7. **Sync with the remote**: run `git pull --rebase` to replay the new commit on top of whatever
   landed upstream since you last synced, keeping history linear. Then `git push`.

8. **Blockers that stop the run** — everything else proceeds on its own:
   - **A rebase conflict**: stop immediately. Don't attempt to resolve it yourself — run
     `git status` to show the user exactly which files conflict, leave the rebase paused (don't
     `git rebase --abort` unless they ask), and hand it back for them to resolve (either by hand or
     by asking you to help interactively from there).
   - **A push rejected for another reason** (e.g. something landed on the remote between your pull
     and your push): don't retry blindly or force-push — re-run `git pull --rebase` once more and
     try again; if it fails a second time, stop and explain what happened.
   - **A pre-commit hook rejection**: fix the underlying issue and commit again as a new commit —
     never bypass the hook. If the fix isn't obvious, stop and report the hook's output.
   - Never `--force`/`--force-with-lease` push, and never push to a branch other than the current
     one's actual upstream.

9. **Report back**, once the push has landed. Lead with the commit message, quoted, on its own
   line — that's the one thing the user needs to register at a glance — then a `-------` line, then
   the supporting details. Use this exact shape:

   ```
   "<the exact commit message>"
   -------
   Commit: <short hash>
   Staged: <files committed, or "all of the above">
   Excluded: <files left out and why, or "nothing">
   Pushed: <branch> -> <upstream>, or where it stopped and why
   ```

## Notes

- This repo's commits don't carry a `Co-Authored-By` trailer (see e.g. the existing
  `dev: Refactor Spek-Kit Constitution` commit) — don't add one unless the user asks for it.
- Never invent a feature number. It comes from an actual `specs/<number>-...` path or branch name,
  never from guessing at what a change "should" belong to.
- If the user's own message asks for something narrower than "everything unstaged" (a subset of
  files, a message they wrote themselves, commit-without-push), follow that instead — the steps
  above are the default, not an override of what they actually asked for.
