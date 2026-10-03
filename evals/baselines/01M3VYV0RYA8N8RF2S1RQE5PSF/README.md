# `01M3VYV0RYA8N8RF2S1RQE5PSF` — the extended corpus, before any retriever change

The whole golden set at 146 cases, driven and scored on 2026-10-01, against the starter corpus as
Phase 4b extended it: the original 9 entries plus 10 longer clinic documents, 19 entries and 36
chunks. It is the run the Phase 4b ablation compares against. Committed as the harness wrote it:
`run.json` (conditions, label digests, corpus pin, entry-id map), `cases/*.json` (one per driven
case, what every number was computed from), and `report.json`/`report.md` (the report the run
printed).

```bash
make eval-compare BASE=evals/baselines/01M3VYV0RYA8N8RF2S1RQE5PSF NEW=<run_id>
```

## Conditions

| | |
|---|---|
| Run id | `01M3VYV0RYA8N8RF2S1RQE5PSF`, 2026-10-01 14:45:33Z – 14:54:10Z (560 s of driving) |
| Code | `e28479a` on `018-hybrid-retrieval`, clean tree |
| Selection | all 146 cases, 168 labelled requests |
| Run clock | `2026-03-02T08:00:00` (a Monday) |
| Corpus | `3af64fc8…28a236d0f`, matching `evals/golden/corpus.json`'s pin |
| Classification / small-talk model | `claude-haiku-4-5-20251001` |
| Generation model | `claude-sonnet-5` |
| Embedding / rerank | `voyage-4-lite` / `rerank-3` |
| Pool / floors / caps | pool 25; similarity 0.25 / 5, rerank 0.58 / 3 |
| Segment cap | 3 |
| Model spend | $0.93 (`make eval-cost`) |

## What it measured

| | |
|---|---|
| request count, intent, exact segmentation | 1.000 (145/145), 0.976 (163/167), 0.972 (141/145) |
| similarity hit@1 / @3 / @5, MRR | 0.928 (77/83), 0.976 (81/83), 1.000 (83/83), 0.956 |
| rerank hit@1 / @3 / @5, MRR | 1.000 (80/80) |
| similarity gate survival | 0.964 (80/83) |
| unserved answerable share | 0.138 (12/87) |
| wrong abstention share | 0.267 (8/30) |
| answers on labelled gaps | none |
| tool selection, end to end | 1.000 (24/24) |

The corpus extension did what it was for: the similarity stage is no longer saturated, where the v2
baseline read 1.000 on almost everything. The reranker still puts the cited entry first whenever
it is handed it.

The twelve unserved answerable requests fall into three causes, and none of them is dense ranking:

- **The similarity floor dropped a chunk ranked first (3):** G-s-05 ("PR-4"), G-u-04 ("braces"),
  G-w-08 ("HC-9") - short questions turning on one token, whose right chunk scored 0.22-0.24
  against a floor of 0.25. This is what Phase 4b's "dense, no floor" row measures.
- **The answerer declined with the right chunk in front of it (5):** G-s-06 (Spikevax), G-s-11
  (Delta Dental PPO), G-u-06 (DOT physical), G-w-05 (Cigna DHMO), and G-m-02's second request
  ("which insurance plans do you accept?"), the one case of the original set that regressed. On
  replay G-s-11 declined 5 times in 5 and G-m-02 4 in 5, with the original entry answering it and
  the insurance guide's chunks beside it; G-u-06 declined 2 in 5. Most are a stated no, which the
  answer prompt's "not even a no" reads against.
- **The classifier routed it elsewhere (4):** G-s-10, G-t-01 and G-w-02 to `small_talk`, G-t-04 to
  `booking`.

## A dependency blip during the run

Voyage was briefly unreachable, and two cases met it, both labelled gaps:

- **G-k-04** - the query embedding failed, so the turn errored and the case is excluded as
  `run_error`. Every metric above is computed without it.
- **G-k-20** - the rerank call failed, so it took the floor-only fallback (`answered_unreranked`
  at the gates); generation then declined, and it abstained as a gap should.

Promoted as it is, by decision rather than re-run: one excluded gap case does not change what the
baseline says, a comparison is computed over the cases both runs recorded, and a dependency blip
is part of the run-to-run noise a band is measured from.
