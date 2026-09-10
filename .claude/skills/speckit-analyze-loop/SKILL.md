---
name: speckit-analyze-loop
description: Run speckit-analyze repeatedly and fix what it finds, round after round, until a round returns fewer than 3 findings or three rounds are done — then report findings fixed, rejected and deferred per round, plus the decisions left for the user. Runs speckit-tasks first only if this session has not already produced a tasks.md. Use when the user asks to "analyze and fix until clean", "run the analyze loop", "keep analyzing the spec until it stops finding things", or otherwise wants the cross-artifact analysis iterated automatically rather than one read-only pass. Invoking it is the go-ahead for the whole loop and for editing spec.md, plan.md and tasks.md; do not pause between rounds for approval.
---

# Analyze loop

Iterate `speckit-analyze` over the current feature's `spec.md`, `plan.md` and `tasks.md`, fixing
what it finds between rounds, until it stops finding much or three rounds are spent. Report what was
fixed, what was rejected, and — the part that matters most — what only the user can decide.

## The two things that must not go wrong

**1. `speckit-analyze` never fixes anything.** It is declared STRICTLY READ-ONLY and its last step
merely *offers* to "suggest concrete remediation edits". The fixing in step 4 below is **this
skill's own work**. Never report a finding as fixed because the analysis recommended a fix — read
the artifact back and confirm the edit is in it.

**2. Never regenerate a `tasks.md` that carries progress.** `speckit-tasks` rewrites the file
wholesale. If the existing one has `[X]` markers, those are the record of what has already been
built, and regenerating destroys it. Step 1's rule is not a formality.

A third, quieter one: **a finding is not automatically right.** The analysis maps tasks to
requirements "by keyword / explicit reference patterns", so it reports zero coverage for a
requirement that is genuinely covered by a task that does not happen to name its ID. Rejecting such
a finding is correct — and must be justified in the report, not dropped silently.

## Steps

### 1. Resolve the feature, and settle whether `speckit-tasks` needs to run

```bash
.specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks
```

Parse `FEATURE_DIR` and `AVAILABLE_DOCS`. Then decide, in this order — stop at the first that
applies:

| Situation | Action |
|---|---|
| This session already ran `speckit-tasks` for this feature | Proceed to step 2. Do not re-run it. |
| `tasks.md` exists and contains any `- [X]` marker | **Proceed to step 2**, and say in the report that tasks.md was kept because it carries implementation progress. Regenerating would erase it. |
| `tasks.md` exists, unmarked, and no newer than `spec.md`/`plan.md` (compare mtimes) | Proceed to step 2. It is current; regenerating buys nothing and costs a rewrite. |
| `tasks.md` is missing, or older than `spec.md`/`plan.md` | Run the `speckit-tasks` skill, then proceed to step 2. |
| `spec.md` or `plan.md` is missing | Stop. Tell the user to run `/speckit-specify` or `/speckit-plan` first — there is nothing to analyze against. |

State which row applied in your first message. "I regenerated tasks.md" and "I kept the existing
one" are different enough that the user should not have to guess which happened.

### 2. Run the analysis

Run the `speckit-analyze` skill.

Capture from its report, per finding: **ID, category, severity, location, summary, recommendation**.
Also capture the Metrics block and whether the findings table hit its 50-row cap.

Two things about running it inside this loop:

- Its final step offers to suggest remediation and asks the user. **Treat that offer as already
  answered** — this loop does the fixing. Do not pause, and do not ask the user the question.
- If it reports zero findings, the loop is done. Go to step 6.

### 3. Triage every finding into exactly one of three buckets

This is the step that decides whether the loop is useful or destructive.

**FIX** — the artifacts are inconsistent and the correction needs no new decision:

- terminology drift, a stale cross-reference, a wrong file path, a broken link
- a requirement or task that contradicts a decision already recorded elsewhere in the same artifacts
- a vague adjective where the artifact elsewhere states the measurable form
- a missing task for a requirement whose implementation is already described in `plan.md`
- a task ordering contradiction the dependency section already implies
- an ambiguity whose answer is unambiguous from the other two artifacts

**ASK** — fixing it would decide something the user owns. Do not touch these:

- two requirements genuinely conflict, and picking one changes what gets built
- a constitution MUST conflict where complying changes the feature's scope
- a coverage gap that can only be closed by **adding scope** — new work, not a new sentence
- anything whose fix would edit a `## Clarifications` bullet. Those are the user's recorded answers;
  a loop must never overwrite one. If a clarification now looks wrong, that is an ASK.
- a success criterion that cannot be made measurable without choosing a target

**REJECT** — the finding is wrong, and the artifact is right. Record the reason.

Write the triage down before editing anything — a scratch file under the session scratchpad is
fine. You will need it verbatim for the report, and across three rounds it is not memorable.

### 4. Apply the FIX bucket

Rules, all of which exist because a doc loop can quietly wreck a spec:

- **Edit the artifact that is wrong**, not the one that is easier to edit. If `tasks.md` names a
  file `plan.md` does not, decide which is mistaken and fix that one.
- **Never renumber `FR-`, `SC-` or `T` identifiers.** Other artifacts, commit messages and code
  comments reference them. Add a suffixed id (`FR-012a`) rather than shifting a range.
- **Match the artifact's own voice.** These documents argue for their decisions; a fix that reads as
  a lint correction is a fix someone will revert.
- **Do not fix beyond the findings.** Improvements the analysis did not ask for make the next
  round's report meaningless.
- If `tasks.md` changed: re-verify task IDs are sequential, every task still carries a file path,
  and story labels appear only in story phases.
- If any artifact's requirement text changed: check whether `checklists/requirements.md` still
  passes, and update the checkbox only if its state genuinely changed.

### 5. Decide whether to go again

Stop if **any** of these holds:

- the round returned **fewer than 3 findings**, or
- **three rounds** are complete, or
- **every finding in the round is one already deferred to ASK in an earlier round.** Those reappear
  every time — the analysis is read-only and nothing fixed them — and re-running cannot make
  progress. This is a stop, not a failure.

Otherwise go back to step 2. Never run a fourth round.

### 6. Verify before reporting

Cheap and worth it, since the loop just edited the documents implementation will be driven from:

- every relative link in the changed artifacts resolves to a file that exists
- task IDs sequential, no reference to a task ID that does not exist
- `FR-`/`SC-` ids unique and unrenumbered
- `git diff --stat` on the feature directory matches the fixes you claim to have made

If the artifacts do not hang together, say so prominently rather than reporting the loop as clean.

### 7. Report

```
Round 1: <n> found · <f> fixed · <a> deferred to you · <r> rejected
Round 2: <n> found · <f> fixed · <a> deferred to you · <r> rejected
Round 3: <n> found · <f> fixed · <a> deferred to you · <r> rejected
-------
Stopped because: <fewer than 3 findings | three rounds complete | only deferred findings left | zero findings>
tasks.md:  <regenerated | kept (carries progress) | kept (current)>
Files changed: <paths, with +/- from git diff --stat>
Checks: <links, ids, diff-matches-claims>

Decisions for you (nothing was changed for these):
  1. <the question, in one line>
     Where: <artifact:section>  ·  Severity: <as reported>
     Options: <A — what it changes> / <B — what it changes>
  2. …

Rejected findings (and why):
  1. <finding id> — <one line: what the analysis missed>
```

Omit rounds that did not run. Omit a section that is empty rather than writing "none" under a
heading. If a round errored, say which and why in place of its numbers.

The **Decisions for you** block is the skill's real output. A loop that fixed 20 wordings and buried
one genuine requirement conflict at the bottom has failed at the only thing it was for — lead that
block with the CRITICAL and HIGH items, in that order.

## Notes

- **Finding counts are not a score.** Round two of an analysis often surfaces what round one walked
  past rather than anything newly broken. Report the numbers; do not narrate a trend.
- **If a round hits the 50-finding cap**, its count is a floor, not a total. Say so — otherwise
  "50 findings" reads as a measurement when it is a truncation.
- **Deferred findings reappearing is expected**, not a regression. The report should say a finding
  is unchanged since round 1, not count it as new each time.
- **No commits.** This loop edits documents only, and the user did not ask for commits; leaving one
  reviewable diff across all rounds is more useful than three fragmentary ones. `/commit-changes` is
  one command away when they want it.
- **Three rounds is a ceiling, not a target.** If round one returns 2 findings, stop at one round.
- The `<3` threshold is a stopping rule, not a quality bar. Say what the last round actually
  returned; do not imply the artifacts are now correct.
