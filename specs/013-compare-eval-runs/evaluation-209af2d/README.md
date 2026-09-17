# The noise band, re-measured after the post-013 fixes

This directory re-takes [`../evaluation/`](../evaluation/)'s measurement — five full runs of one
unchanged build and the band built from them — on the build that followed it. Between the two
builds, five fixes landed, aimed at what that band found varying:

| commit | fix |
|---|---|
| `a9237df` | Answer a lone request in the patient's own words |
| `04fd410` | Act on a chosen booking change without re-confirming it |
| `e55dd78` | Say what an appointment listing leaves out |
| `11be02f` | Block paid API calls in the integration tier |
| `209af2d` | Steady the classifier on its label boundaries (temperature 0, three stated boundaries) |

- `runs/<run_id>/report.json`, `report.md` — the five runs' reports, as the harness wrote them
- `band.json` — the band built from all five by `make eval-band`
- `band-013-rescored.json` — 013's five runs, re-banded under **this** build's scorer (see below)
- `comparison/comparison.json`, `comparison.md` — 013's run 5 against this record's run 5, with
  this band applied

The same caveats as 013's record hold, and are not repeated at length: a band is an observed range of
five values, not a confidence interval and not a threshold, and nothing here was adjudicated.

## Conditions the five were measured under

| | |
|---|---|
| Runs, in the order taken | `01M2QRMSWMRPE05S35S7P4R0K0`, `01M2QS7Z9FCAFZKZFJ4Y67J4XM`, `01M2QSVP667YS4A7QEMGWDARHM`, `01M2QTG3V38A6WP6P2Y6NY8F8H`, `01M2QV47S2PM18W25RGYQM7Q8Z` |
| Taken | 2026-09-17 13:24Z – 14:18Z, back to back, 598–637 s of driving each |
| Code | `209af2d` on `main`, clean tree; the stack restarted on it before the first run |
| Cases | all 135, recorded by every run |
| Everything else | identical to 013's record: clock, corpus pin, models, pool, floors, caps, segments, context turns |

Because every condition matches, 013's band and this one describe the same measurement on two
builds, and `make eval-compare` accepts a run from each.

## Was it the assistant or the scorer?

`209af2d` changed the scorer as well as the classifier (alignment, classification, retrieval and
serving scoring). A narrower band could therefore be a scoring change rather than a steadier
assistant. `band-013-rescored.json` answers that: 013's five stored runs re-banded with this build's
scorer keep essentially 013's widths — the booking metrics at 0.167, `wrong_abstention_share` at
0.084, 18 varying cases. **The narrowing below is the assistant's.**

## What the five runs found

### Widths, 013's build against this one

Both columns are scored by this build's scorer.

| metric | 013's build (rescored) | this build |
|---|---|---|
| `tool_selection_correctness` | 0.500 – 0.667 (0.167) | 0.722 – 0.778 (0.056) |
| `end_to_end_task_success` | 0.722 – 0.889 (0.167) | 0.944 – 1.000 (0.056) |
| `wrong_abstention_share` | 0.143 – 0.227 (0.084) | 0.174 (0) |
| `rerank_hit_at_1` | 0.977 – 1.000 (0.023) | 1.000 (0) |
| `exact_segmentation_match` | 0.926 – 0.941 (0.015) | 0.948 (0) |
| `unserved_answerable_share` | 0.057 – 0.069 (0.011) | 0.046 (0) |
| `intent_accuracy` | 0.962 – 0.973 (0.011) | 0.973 (0) |
| `request_count_accuracy` | 0.970 – 0.978 (0.007) | 0.978 (0) |

20 of 22 metrics did not move across the five runs. The two that did are the booking metrics, each
by one booking half of 18. The booking ranges now sit wholly above 013's; `wrong_abstention_share`
settled inside its old range rather than below it.

The runs are not copies of one another: each opened its own session, and their stored replies
differ — generation still samples. The likeliest source of the steadiness is `209af2d` classifying at
temperature 0, though these runs do not isolate it from the other four fixes.

### The cases that varied

2 of 135, down from 18:

- **G102** — missed `book_appointment` in 1 of 5, and the appointment was absent afterwards. It was on
  013's list too, and is the one booking case that still fails the task on some runs.
- **G089** — missed `list_practitioners` in 1 of 5, with the task still completed. New to the list.

Every other case 013 saw vary is steady here, including G062 and G090, which missed their tool in 4
of 013's 5 runs, and all six FAQ cases that flipped at segmentation.

### What is steady and still wrong

A zero-width metric is not a correct one. With the noise gone, these are what hold the numbers where
they are — each is identical in all five runs, and each is a finding for adjudication:

- **Four booking halves miss their labelled tool every time**, while the task still succeeds:
  - G048 "can I move my Thursday appointment?" — lists appointments, never calls `reschedule_appointment`
  - G049 "which cardiologists do you have?" — calls nothing
  - G053 "Can I see a cardiologist next week?" — calls nothing
  - G092 "What should I bring, and what does Dr. Vesalius specialize in?" — calls nothing

  Whether the assistant is wrong or the label asks more than one turn should do is the open question.
- **Four answerable requests abstain every time**: G016 and G096 ("Is parking free?", rerank floor),
  G081 ("What cards do you take?", similarity floor) and G100 ("What happens if you do not take
  Medicare?", rerank floor). G081 answered in 3 of 013's 5 runs and abstains in all 5 of these: whatever
  steadied it, it settled on the wrong side.

## The worked comparison

`comparison/` compares 013's run 5 (`01M2NMC5ATQ0G5G6246PW0VP3E`) with this record's run 5
(`01M2QV47S2PM18W25RGYQM7Q8Z`) — two builds, not one, so unlike 013's worked comparison the answer is
not known in advance. Both runs are re-scored by this build's scorer. Of 22 metrics, 12 moved towards
their target, none away, 3 in no direction (the verdict shares) and 7 not at all. The booking pair
moved most, up 0.167 each, carried by G062, G095 and G102 now calling `book_appointment`.

The band marks every metric of the new run as inside it, which says run 5 is typical of *this* build.
It does not say the change between builds exceeds noise; the widths table above, where the two
builds' ranges do not overlap, is the better reading of that.

## What is deliberately not here

As in 013's record, the runs' `cases/` directories are not committed; the per-case variation above
cannot be traced to its turns from this directory.
