# The noise band — how much these numbers move on their own

This directory is phase 2c's record (FR-037): five full runs of **one unchanged build**, the band
measured from them, and one worked comparison between two of the five.

- `runs/<run_id>/report.json`, `report.md` — the five runs' reports, as the harness wrote them
- `band.json` — the band built from all five by `make eval-band`
- `comparison/comparison.json`, `comparison.md` — the T046 comparison of run 1 against run 5, with
  the band applied

**It is evidence of self-movement, not a threshold** (FR-038). Nothing here is a value a later run
has to beat, and nothing here says what a run *should* score. The band answers one question — how
far these numbers drift when nothing changes — so that a later comparison can be read without
mistaking drift for an effect. It is an observed range of five values: not a confidence interval,
not a significance test, and not a gate. No build in this repository fails on any number in this
directory.

Nothing was adjudicated because of these runs. No label was changed, no threshold was moved, and
nothing the assistant does was touched (spec, Out of Scope). Everything below is a finding for a
person to decide about later.

## Conditions the five were measured under

| | |
|---|---|
| Runs, in the order taken | `01M2NHQFK02X5TJNJ05BM8BNX1`, `01M2NJDNMDARRKHDGJRT5PC07N`, `01M2NK2XD8NNJKXVAQZV81BVWW`, `01M2NKPXX05H24TYV51CKWGXEN`, `01M2NMC5ATQ0G5G6246PW0VP3E` |
| Taken | 2026-09-16 16:45Z – 17:42Z, back to back, 619–685 s of driving each |
| Code | `115ba24` on `main`, clean tree |
| Cases | all 135, recorded by every run |
| Run clock | `2026-03-02T08:00:00` (a Monday) |
| Corpus | `ea83b6c4…a48b20a0`, matching the pin in all five |
| Classification model | `claude-haiku-4-5-20251001` |
| Generation model | `claude-sonnet-5` |
| Embedding / rerank | `voyage-4-lite` / `rerank-3` |
| Retrieval pool | 25 |
| Similarity floor / cap | 0.3 / 5 |
| Rerank floor / cap | 0.58 / 3 |
| Max segments / context turns | 3 / 5 |
| Stack | local, `LOG_FORMAT=json make services-up`, chat + scheduler + Postgres + Qdrant |

These are the same conditions the committed 2b run was taken under, and `band.json` records them as
a run does (FR-029). **A band says nothing about runs taken under other values** — `make eval-compare`
given this band and a run with a different floor reports that and marks nothing (FR-033).

## What the five runs found

### The metrics that move most

| metric | observed range | width | what the width is in whole units |
|---|---|---|---|
| `tool_selection_correctness` | 0.500 – 0.667 | 0.167 | 3 booking halves of 18 |
| `end_to_end_task_success` | 0.722 – 0.889 | 0.167 | 3 booking halves of 18 |
| `wrong_abstention_share` | 0.143 – 0.227 | 0.084 | 2 requests of ~22 |
| `rerank_hit_at_1` | 0.976 – 1.000 | 0.024 | 2 requests of ~85 |
| `unserved_answerable_share` | 0.057 – 0.080 | 0.023 | 2 requests of 87 |
| `exact_segmentation_match` | 0.911 – 0.933 | 0.022 | 3 cases of 135 |

The two booking metrics are by far the widest, and the fourth column is why: they are scored over
**18 booking halves**, so a single case flipping moves them 0.056 and three cases span the whole
observed range. A booking number that moves a tenth between two runs has not necessarily seen
anything change — that is within what five runs of one build produced.

Five metrics did not move at all across the five runs: `similarity_hit_at_5`, `rerank_hit_at_5`,
`verdict_distribution.answered_unreranked`, `verdict_distribution.abstained_empty_corpus` and
`verdict_distribution.abstained_empty_pool`. A zero-width range is still only five observations —
it means these did not move here, not that they cannot.

### The cases that varied

18 of 135 cases produced a different outcome in at least one of the five runs, across five kinds of
outcome: retrieval rank (13 entries), segmentation (9), verdict (7), database state (6) and tool
selection (6). `band.json`'s `varying_cases` carries every one with its per-outcome counts; the
shape worth knowing:

**Booking cases that flip between calling the labelled tool and missing it** — G042, G062, G088,
G090, G093, G102. Each varies in tool selection *and* in the database state afterwards, together:
the appointment is missing because the tool was never called. Two of these are nearly one-sided
rather than evenly split — G062 misses `book_appointment` in **4 of 5** runs and G090 misses
`cancel_appointment` in **4 of 5** — which makes them look less like noise than like a defect that
occasionally does not reproduce. That is a finding for adjudication, not something this phase acts
on.

**FAQ cases that flip at segmentation, and drag what follows with them** — G002, G003, G023, G097,
G108, G130. When the classifier splits a message into two requests in one run and one in the next,
the second request's verdict appears and disappears with it. In G002, G003, G097 and G108 the
request's retrieval rank goes with it too, which is why those four show up under three groups at
once; G023 and G130 vary in segmentation and verdict only. G003 is the sharpest: `booking` in 4 of
5 runs and `faq_question` in 1.

**Cases that vary only in where the right chunk ranks** — G099 and G100, which move `hit_at_1` and
`mrr` and nothing else. G099 sits at rank 1 in 3 of 5 runs and rank 2 in 2, on both similarity and
rerank. G100 is at rank 1 in 4 of 5 on both; its fifth run drops to similarity rank 2 and never
reaches the reranker at all.

The remaining three — G033, G077, G111 — vary in segmentation alone. G077 and G111 reorder their two
segments without changing either label, which `exact_segmentation_match` counts as a miss.

### What this means for reading a comparison

- **Check the band before believing a movement.** A case that flipped in 2 of these 5 runs has not
  been changed by an edit. `make eval-compare … BAND=` marks each metric inside or outside the
  observed range and tags every case the band saw vary, so this check does not have to be done by
  hand.
- **Booking numbers need more than one run.** Their range is a sixth of the scale. A change aimed at
  booking cannot be judged from a single pair of runs.
- **Inside the band is not proof of nothing.** The range is five observations. A real effect smaller
  than the drift hides inside it, and a sixth run could widen it.
- **The band expires when the conditions do.** Move a floor or a model and this band no longer
  applies — the tool will refuse to mark against it, and a new band costs five more full runs.

The worked comparison in `comparison/` is the demonstration: run 1 against run 5, two runs of the
same build with no edit between them. **All 22 of its metrics are marked inside the observed range
and none outside** (SC-009) — the 15 that moved as well as the 7 that did not. Of those 15, 7 moved
away from their target and 5 towards it. It is worth reading precisely because the answer is known
in advance: `wrong_abstention_share` up 0.075 and `end_to_end_task_success` up 0.111 across two
identical builds is the clearest statement of what this set does on its own.

## What is deliberately not here

The five runs' `cases/` directories are **not committed** (SC-011). Five of them are some 12 MB of
JSON against the 2b run's 2.5 MB, and the band's value is the shape of the spread rather than the
turn behind each observation.

The cost is stated rather than discovered: **the band's per-case variation cannot be traced to its
turns from this directory.** `varying_cases` says G062 missed `book_appointment` in 4 of 5 runs; it
cannot say what the assistant did instead, and the local runs under `.run/evals/` are the only place
that could have answered. A question that needs the turn behind a number needs five fresh runs.

Every number in `band.json` and `comparison/` does trace to the five `report.json` files here
without re-running anything, which is the guarantee SC-011 actually makes.
