---
name: review-loop
description: Run repeated deep code review at xhigh effort and fix what it finds, committing between rounds, until a round returns fewer than 12 findings or three rounds are done — then restore the normal effort level and report findings, review time and fix time per round. Use when the user asks to "review and fix until clean", "run the review loop", "keep reviewing until it stops finding things", or otherwise wants review-and-fix iterated automatically rather than one review pass. Invoking it is the go-ahead for the whole loop, including its commits; do not pause between rounds for approval.
---

# Review loop

Iterate `/code-review` and its fixes at raised effort, committing each round, until the review
stops finding much or three rounds are spent. Report what it cost.

## The one thing that must not go wrong

`effortLevel` lives in **`~/.claude/settings.json`** — user-level, not project-level. Raising it
affects every project and every session, and it is silently expensive. **The restore in step 6 is
not cleanup; it is part of the operation.** Run it on every exit path: a clean finish, a review that
errors, a rebase conflict, a hook rejection, a user interrupt. If you are ending the turn for any
reason and the level is still raised, restore it first and say so.

Never assume the baseline is `high`. Read the actual value in step 1 and restore *that*, so a user
who normally runs at `medium` is not silently moved up.

## Steps

### 1. Record the baseline and raise the effort

```bash
python3 .claude/skills/review-loop/set-effort.py
```

Keep that value exactly as printed — it is what step 6 passes back (`unset` when the key is
absent). Then set `effortLevel` to `xhigh`:

```bash
python3 .claude/skills/review-loop/set-effort.py xhigh
```

It prints the value read back; check it says `xhigh`. It also prints a `warning:` line on stderr
for each setting it finds that outranks that key — a per-model `modelSettings.<model>.effortLevel`
(where `/effort` saves a pick), `ultracode`, `CLAUDE_CODE_EFFORT_LEVEL`, or an effort setting in
the project's settings files. **Any such warning means the session is not running at the printed
level**; say so in your first message. The script cannot see an `--effort` flag or a level picked
in-session, so no warning is not proof either.

Run both from the repo root and in exactly this form. On this machine `.claude/settings.local.json`
allows those exact command lines (no argument, each level, `unset`), so a heredoc or `python3 -c`
edit of the settings file is not needed and is refused as self-modification. That file is
untracked, so a checkout without it has no such rule: each call then needs approval, and if the
restore in step 6 is refused, stop and give the user the exact command to run. The rule approves
the command line, not the script's content — the script accepts only a known level and changes no
other key's value, but an edit to it, including one that arrives through a round's
`git pull --rebase`, runs under the same approval.

**State plainly in your first message that the global effort level is now raised and will be
restored at the end.** The user is paying for it and cannot see the setting change.

If the change does not appear to take effect for this session, say so rather than reporting the loop
as having run at xhigh when it may not have. A skill that quietly runs at the old level, and reports
otherwise, is worse than one that admits the uncertainty.

### 2. Commit anything uncommitted

Run the `commit-changes` skill. If the working tree is already clean it will say so and stop, which
is fine — the point is that each round's diff is the round's own work, not last round's mixed in.

A rebase conflict or a hook rejection there stops the loop: **restore the effort level (step 6),
report where it stopped, and hand it back.** Do not attempt to resolve a conflict inside this loop.

### 3. Review

Note the wall-clock start, run:

```
/code-review xhigh --fix
```

then note the end. `--fix` applies the findings to the working tree in the same pass, so one
invocation covers both "review" and "fix" — and both run at the raised effort, main agent and
subagents alike, because subagents inherit it.

Record from the result:

- **the number of findings** — the count the review reports, not your impression of it
- **review duration** and **fix duration** if the output distinguishes them; otherwise record the
  single combined duration and say in the report that it is combined. Do not invent a split.

If the review returns zero findings, the loop is done — go to step 5.

### 4. Commit the fixes and decide whether to go again

Run `commit-changes` again for this round's fixes.

Then stop if **either** holds:

- the round returned **fewer than 12 findings**, or
- **three rounds** are complete.

Otherwise go back to step 3. Never run a fourth round.

### 5. Verify before reporting

The review's own fixes are still changes to the code, and nothing has run the suite over them. Run
the project's checks — for this repo, `make precommit` plus the unit tier — and report the result.
**A loop that fixed 40 findings and left the tests red has not improved anything**, and the report
must not read as though it had.

If the checks fail, say so prominently and do not describe the loop as successful.

### 6. Restore the effort level

Put back the value recorded in step 1:

```bash
python3 .claude/skills/review-loop/set-effort.py <baseline>
```

Run it from the repo root, as in step 1: the Bash working directory persists between calls, and the
rounds may have left it somewhere else, where the relative path does not resolve. Pass the baseline
exactly as step 1 printed it; `unset` removes the key rather than inventing a value. The script
prints the value it read back after writing; confirm it matches the baseline rather than assuming
the write landed, and report that confirmed value.

### 7. Report

```
Round 1: <n> findings · review <t>, fix <t>
Round 2: <n> findings · review <t>, fix <t>
Round 3: <n> findings · review <t>, fix <t>
-------
Stopped because: <fewer than 12 findings | three rounds complete | error>
Total: <n> findings across <r> rounds, <t> elapsed
Commits: <short hashes>
Checks:  <make precommit + unit tier result>
Effort:  restored to <value>, confirmed
```

Omit rounds that did not run. If a round errored, say which and why in place of its numbers.

## Notes

- **Findings counts are not a score.** A round returning 3 findings after a round of 40 may mean the
  code improved, or may mean the review sampled a different area — the second and third rounds of a
  loop like this often surface things the first walked past rather than things newly broken. Report
  the numbers; do not narrate them as a trend the data does not support.
- **Do not fix beyond the findings.** The loop applies what the review found. Refactoring the review
  did not ask for makes the next round's diff unreadable and the report meaningless.
- **Three rounds is a ceiling, not a target.** If round one returns 4 findings, stop at one round.
- The `<12` threshold is a stopping rule, not a quality bar. Say what the last round actually
  returned; do not imply the code is now under some standard.
