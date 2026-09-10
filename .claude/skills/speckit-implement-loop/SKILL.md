---
name: speckit-implement-loop
description: Run speckit-implement, then iterate speckit-converge and speckit-implement — implementing each round's appended tasks — until a converge round appends nothing or three rounds are done, then run the project's gates and report tasks added and implemented per round plus the decisions left for the user. Use when the user asks to "implement until converged", "run the implement loop", "keep converging until nothing is left", or otherwise wants implement-and-converge iterated automatically rather than one pass. Invoking it is the go-ahead for the whole loop and for the code changes it makes; do not pause between rounds for approval.
---

# Implement loop

Drive the feature to convergence: implement the task list, ask `speckit-converge` what the code
still does not satisfy, implement that, and repeat until converge appends nothing or three rounds
are spent. Then run the gates and report honestly what is built, what is not, and what only the user
can decide.

## The three things that must not go wrong

**1. `speckit-converge` is append-only, so an unfinished task comes back as a duplicate.** It
appends a new `## Phase N: Convergence` section and *never* touches an existing task — including one
from a previous convergence phase that is still `- [ ]`. So a gap that round 2 failed to close is
re-appended by round 3's converge under a fresh ID, and the task list grows while the code does not.
**Track which convergence tasks are still open across rounds** (step 4), and treat a round whose
appended tasks are all re-appearances as a stop, not as progress.

**2. `speckit-implement` stops and asks the user if any checklist is incomplete.** It counts
`FEATURE_DIR/checklists/*.md` and, on any incomplete item, halts with "Do you want to proceed with
implementation anyway?". **Do not answer that for the user.** An incomplete checklist is the spec's
own quality gate failing, and overriding it is exactly the kind of decision this loop must hand
back. Stop the loop, and report it as the first entry in **Decisions for you**.

**3. A green report over a red suite is worse than no report.** `implement` marks tasks `[X]` as it
goes; nothing in it runs the project's gates over the accumulated result. Step 6 is not cleanup —
a loop that closed 30 tasks and left `make test-unit` failing has not converged on anything.

A fourth, quieter one: **never edit a test to make a task pass.** The constitution puts tests before
implementation for this feature; a round that adjusts an assertion instead of the code has inverted
the thing the ordering exists to protect. If a test's expectation is genuinely wrong, that is a
**Decision for you**, not a fix.

## Steps

### 1. Resolve the feature, and settle whether `speckit-implement` needs a first run

```bash
.specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks
```

Parse `FEATURE_DIR`. Then, in order — stop at the first that applies:

| Situation | Action |
|---|---|
| This session already ran `speckit-implement` for this feature | Proceed to step 2. Do not re-run it. |
| Any `FEATURE_DIR/checklists/*.md` has an incomplete item | **Stop the loop before it starts.** Report the checklist table and the incomplete items as a decision for the user — `implement` would halt on them anyway, and answering for them is not this loop's call. |
| `tasks.md` has no `- [ ]` task left | Skip the first implement and go straight to step 2's converge. There is nothing to implement yet; converge is what finds whether that is true of the *code*. |
| Otherwise | Run the `speckit-implement` skill, then proceed to step 2. |

Record before running anything: the count of `- [ ]` and `- [X]` tasks, and the maximum task ID.
Those three numbers are what every round below is measured against.

Two things to watch in that first implement run:

- Its step 4 wants to create or verify ignore files. This repo already has them. Let it verify;
  if it appends anything to `.gitignore`, say so in the report rather than letting a loop quietly
  edit repository config.
- It halts on a failed non-parallel task. A halt is a stop for this loop too — go to step 6.

### 2. Converge

Run the `speckit-converge` skill.

Before it runs, record `tasks.md`'s max task ID and the ids of every convergence task still `- [ ]`.
After it returns, read the file back and establish, **from the file rather than from the report**:

- how many tasks were appended, and under which `## Phase N: Convergence` header
- their ids, gap types (`missing` / `partial` / `contradicts` / `unrequested`) and source-refs

If converge reports `converged` and `tasks.md` is byte-for-byte unchanged, the loop is done — go to
step 6. That is the loop's real stopping signal, and it is a file fact, not a claim.

### 3. Triage the appended tasks before implementing them

Most appended tasks are ordinary work. Three kinds are not, and must be pulled out of the round:

- **`unrequested` gaps.** Converge surfaces code the spec did not call for and appends a task to
  "review/justify or remove". Deleting code on a loop's own judgment is not this loop's call —
  route every `unrequested` task to **Decisions for you** and leave it `- [ ]`.
- **A task that contradicts a recorded decision.** If closing it would reverse something the spec's
  `## Clarifications` section settled, or a decision `plan.md`/`research.md` argues for, stop and
  ask. Converge reads intent from the artifacts and can misread which of two of them governs.
- **A re-appearance.** A task whose source-ref matches one still open from an earlier round is a
  gap the loop already failed to close. Do not implement it a second time blind; report it, and
  count it as a re-appearance rather than as new work.

Write the triage down — a scratch file under the session scratchpad. Across three rounds the ids
and gap types are not memorable, and the report needs them exactly.

### 4. Implement the round

Run the `speckit-implement` skill.

Then read `tasks.md` back and record, per round:

- **appended**: how many tasks converge added
- **implemented**: how many of *those* ids are now `- [X]`
- **still open**: the appended ids left `- [ ]`, and why (deferred by step 3, failed, or skipped)

Never report a task as implemented because implement said so — a task is implemented when its
checkbox is `[X]` **and** the file the task names actually changed. Check both.

### 5. Decide whether to go again

Stop if **any** of these holds:

- converge appended **no** tasks, or
- **three rounds** are complete, or
- **every task appended this round is a re-appearance** (step 3) — converge is append-only, so this
  means the loop is copying gaps rather than closing them, and a fourth round would copy them again,
  or
- `implement` **halted** on a failed task, or a checklist became incomplete.

Otherwise go back to step 2. Never run a fourth round.

### 6. Run the gates

The loop just wrote code across up to three rounds and nothing has checked it as a whole:

```bash
make lint && make typecheck && make test-unit
make test-frontend      # only if the rounds touched services/frontend
```

Report the actual result. If the gates fail, say so **before** any per-round numbers — the numbers
describe activity, and the gates describe whether it worked. Do not describe the loop as converged
over a failing suite.

### 7. Report

```
Round 1: <n> appended · <i> implemented · <o> still open · <r> re-appearances
Round 2: <n> appended · <i> implemented · <o> still open · <r> re-appearances
Round 3: <n> appended · <i> implemented · <o> still open · <r> re-appearances
-------
Stopped because: <converge appended nothing | three rounds complete | only re-appearances | implement halted | checklist incomplete>
Tasks: <x>/<y> complete in tasks.md (was <a>/<b> at the start)
Gates: <make lint / typecheck / test-unit / test-frontend results>
Uncommitted: <paths changed, from git diff --stat>

Decisions for you (nothing was changed for these):
  1. <the question, in one line>
     Where: <task id · source-ref · gap type>
     Options: <A — what it changes> / <B — what it changes>
  2. …

Still open (implemented by nothing, and why):
  1. <task id> — <failed | deferred | skipped> — <one line>
```

Omit rounds that did not run, and omit a section that is empty rather than writing "none" under a
heading. If a round errored, say which and why in place of its numbers.

The **Decisions for you** block is what the user actually needs from this skill; lead it with
anything CRITICAL (a constitution violation converge found) and anything that blocks a P1 story.

## Notes

- **"Appended" is not "remaining".** Converge appends what it *found*, bounded by the scope the
  artifacts define. A round that appends 12 tasks has not measured the whole gap, and the report
  must not imply it has.
- **No commits.** This loop changes code, and the user did not ask for commits — a single reviewable
  diff across rounds beats three fragmentary ones, and `commit-changes` also *pushes*, which is not
  a side effect a loop should have. Report what is uncommitted; `/commit-changes` is one command
  away. If the user wants a commit per round, they will say so.
- **Three rounds is a ceiling, not a target.** If round one appends nothing, the feature was already
  converged and the loop is one converge call long.
- **Converge is not a diff tool.** It assesses the present state of the code against the artifacts,
  with no git and no history. So it cannot tell work this loop did from work that was already there,
  and neither can the report — say what `tasks.md` and the gates say, not what the loop "achieved".
- A `converged` result means the code satisfies the **specified scope**, which is not the same as
  the feature being finished. If the spec's own manual steps (a quickstart walk, an evaluation
  procedure) are still `- [ ]`, list them under **Still open** rather than reporting convergence
  without qualification.
