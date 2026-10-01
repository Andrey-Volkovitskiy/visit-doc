# `01M3W4D3ZPHGWSV5DPQJB5RZC3` — heading-aware chunking

The whole golden set at 146 cases, driven and scored on 2026-10-01, on the build that chunks an
entry with headings by section and prefixes every chunk with its heading path (`a171078`).
Everything else is `01M3W2MA7Y1BBTG2Y9F5W1NCKN`'s build: the extended corpus, no similarity floor
in front of the reranker, and the answer prompt that gives a no the information states. It
supersedes that run as the baseline, and is the one the hybrid ablation compares against.
Committed as the harness wrote it.

```bash
make eval-compare BASE=evals/baselines/01M3W4D3ZPHGWSV5DPQJB5RZC3 NEW=<run_id>
```

## Conditions

| | |
|---|---|
| Run id | `01M3W4D3ZPHGWSV5DPQJB5RZC3`, 2026-10-01 16:22:49Z – 16:31:37Z (558 s of driving) |
| Code | `a171078` on `018-hybrid-retrieval`, clean tree |
| Selection | all 146 cases, 168 labelled requests |
| Run clock | `2026-03-02T08:00:00` (a Monday) |
| Corpus | `3af64fc8…28a236d0f`, matching `evals/golden/corpus.json`'s pin; 62 chunks |
| Classification / small-talk model | `claude-haiku-4-5-20251001` |
| Generation model | `claude-sonnet-5` |
| Embedding / rerank | `voyage-4-lite` / `rerank-3` |
| Pool / floors / caps | pool 25; similarity -1.0 (none) / 5; unreranked fallback 0.25; rerank 0.58 / 3 |
| Segment cap | 3 |
| Model spend | $0.90 (`make eval-cost`) |

The chunker is not a run condition - `service.configured` states no chunking setting - so a
comparison against an earlier run shows identical conditions. The code commit above is what
distinguishes them.

## What it measured

| | |
|---|---|
| request count, intent, exact segmentation | 1.000 (146/146), 0.976 (164/168), 0.973 (142/146) |
| similarity hit@1 / @3 / @5, MRR | 0.952 (79/83), 0.976 (81/83), 1.000 (83/83), 0.967 |
| rerank hit@1 / @3 / @5, MRR | 1.000 (83/83) |
| unserved answerable share | 0.057 (5/87) |
| wrong abstention share | 0.042 (1/24) |
| answers on labelled gaps | none |
| tool selection, end to end | 1.000 (24/24) |
| exclusions other than handed-off turns | none |

Of the five unserved requests, four are the classifier routing elsewhere (G-s-10, G-t-01, G-w-02
to small talk, G-t-04 to booking) and one is the answerer declining G-w-05 ("Do you accept Cigna
DHMO?"), which on replay it answers 5 times in 10.

Three gaps that the rerank floor stopped under the old chunks now clear it and are declined at
generation instead - G-k-07 (fasting before a blood test), G-k-14 (a follow-up's price) and the
MRI half of G-n-10 - and G-k-21 moved the other way. The heading prefix makes a chunk read as more
relevant to the cross-encoder, so the answerer now carries more of the abstention. All declined;
G-k-07 and G-k-14 declined 10 times in 10 on replay.
