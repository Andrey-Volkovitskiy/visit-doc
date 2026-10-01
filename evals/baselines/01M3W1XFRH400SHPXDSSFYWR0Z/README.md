# `01M3W1XFRH400SHPXDSSFYWR0Z` — no similarity floor in front of the reranker

The whole golden set at 146 cases, driven and scored on 2026-10-01, on the build that dropped the
similarity floor (`b0aa7a1`): the shortlist is the dense search's top 5 by rank, and the rerank
floor alone decides an abstention. It supersedes `01M3VYV0RYA8N8RF2S1RQE5PSF`, the same set and
corpus under a 0.25 floor, and is the run the answer-prompt change and the hybrid ablation compare
against. Committed as the harness wrote it.

```bash
make eval-compare BASE=evals/baselines/01M3W1XFRH400SHPXDSSFYWR0Z NEW=<run_id>
```

## Conditions

| | |
|---|---|
| Run id | `01M3W1XFRH400SHPXDSSFYWR0Z`, 2026-10-01 15:39:19Z – 15:47:30Z (531 s of driving) |
| Code | `b0aa7a1` on `018-hybrid-retrieval`, clean tree |
| Selection | all 146 cases, 168 labelled requests |
| Run clock | `2026-03-02T08:00:00` (a Monday) |
| Corpus | `3af64fc8…28a236d0f`, matching `evals/golden/corpus.json`'s pin |
| Classification / small-talk model | `claude-haiku-4-5-20251001` |
| Generation model | `claude-sonnet-5` |
| Embedding / rerank | `voyage-4-lite` / `rerank-3` |
| Pool / floors / caps | pool 25; similarity -1.0 (none) / 5; unreranked fallback 0.25; rerank 0.58 / 3 |
| Segment cap | 3 |
| Model spend | $0.92 (`make eval-cost`) |

## What it measured

| | |
|---|---|
| request count, intent, exact segmentation | 1.000 (146/146), 0.976 (164/168), 0.973 (142/146) |
| similarity hit@1 / @3 / @5, MRR | 0.928 (77/83), 0.976 (81/83), 1.000 (83/83), 0.956 |
| rerank hit@1 / @3 / @5, MRR | 1.000 (83/83) |
| similarity gate survival | 1.000 (83/83) |
| unserved answerable share | 0.115 (10/87) |
| wrong abstention share | 0.207 (6/29) |
| answers on labelled gaps | none |
| tool selection, end to end | 1.000 (24/24) |
| exclusions other than handed-off turns | none |

Retrieval lost nothing: every routed answerable request reached the reranker with its cited entry,
and the reranker ranked it first every time. The ten unserved requests have two causes:

- **The answerer declined with the right chunk in front of it (6):** G-m-02's second request,
  G-s-06, G-s-11, G-u-04, G-u-06, G-w-05. G-u-04 ("Do you do braces?") is the floor's old miss,
  now reaching generation and declined there; replayed five times under this build's prompt it was
  answered twice.
- **The classifier routed it elsewhere (4):** G-s-10, G-t-01 and G-w-02 to `small_talk`, G-t-04 to
  `booking`.

Run-to-run noise is not measured for this build, so the movements against
`01M3VYV0RYA8N8RF2S1RQE5PSF` are samples: the three floor rescues are structural (their chunks now
reach the reranker), the rest is not evidence. See
`specs/018-hybrid-retrieval/evaluation/findings.md`.
