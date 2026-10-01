# `01M3W2MA7Y1BBTG2Y9F5W1NCKN` — the answerer gives a no the information states

The whole golden set at 146 cases, driven and scored on 2026-10-01, on the build that changed the
FAQ answer prompt (`957f240`): "Say nothing it does not say - not even a no" became "... - but a
no it does say is an answer, so give it." Everything else is `01M3W1XFRH400SHPXDSSFYWR0Z`'s
build: the extended corpus and no similarity floor in front of the reranker. It supersedes that run
as the baseline, and is the one heading-aware chunking and the hybrid ablation compare against.
Committed as the harness wrote it.

```bash
make eval-compare BASE=evals/baselines/01M3W2MA7Y1BBTG2Y9F5W1NCKN NEW=<run_id>
```

## Conditions

| | |
|---|---|
| Run id | `01M3W2MA7Y1BBTG2Y9F5W1NCKN`, 2026-10-01 15:51:47Z – 16:00:40Z (575 s of driving) |
| Code | `957f240` on `018-hybrid-retrieval`, clean tree |
| Selection | all 146 cases, 168 labelled requests |
| Run clock | `2026-03-02T08:00:00` (a Monday) |
| Corpus | `3af64fc8…28a236d0f`, matching `evals/golden/corpus.json`'s pin |
| Classification / small-talk model | `claude-haiku-4-5-20251001` |
| Generation model | `claude-sonnet-5` |
| Embedding / rerank | `voyage-4-lite` / `rerank-3` |
| Pool / floors / caps | pool 25; similarity -1.0 (none) / 5; unreranked fallback 0.25; rerank 0.58 / 3 |
| Segment cap | 3 |
| Model spend | $0.94 (`make eval-cost`) |

## What it measured

| | |
|---|---|
| request count, intent, exact segmentation | 1.000 (145/145), 0.976 (163/167), 0.972 (141/145) |
| similarity hit@1 / @3 / @5, MRR | 0.927 (76/82), 0.976 (80/82), 1.000 (82/82), 0.955 |
| rerank hit@1 / @3 / @5, MRR | 1.000 (82/82) |
| similarity gate survival | 1.000 (82/82) |
| unserved answerable share | 0.070 (6/86) |
| wrong abstention share | 0.080 (2/25) |
| answers on labelled gaps | none |
| tool selection, end to end | 1.000 (24/24) |

Against `01M3W1XFRH400SHPXDSSFYWR0Z`, with identical conditions: G-m-02's second request, G-s-06,
G-u-06 and G-w-05 moved from declined to answered, and G-t-08 ("Can I put moisturiser on before
the heart tracing?") from answered to declined - on replay the new prompt declines it 3 times in
10 and the old one never did. The six unserved requests left are two the answerer declines
(G-s-11, G-t-08) and four the classifier routes elsewhere (G-s-10, G-t-01, G-w-02 to small talk,
G-t-04 to booking).

## A dependency blip during the run

**G-u-04** ("Do you do braces?") is excluded as `run_error`: its query embedding failed on a
Voyage connection error. In the targeted run on the same build just before this one it was
answered. Promoted as it is, by decision: comparisons are computed over the cases both runs
recorded, so the one excluded case only narrows them.
