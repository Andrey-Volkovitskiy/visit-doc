# `01M3WD842TTD1Q9FX8TDRTMFAB` — classifier fix

The whole golden set at 146 cases, driven and scored on 2026-10-01, on the build whose classifier
reads a question about what the clinic sent, billed or treated as `faq_question`, and arriving
late as a term of an appointment rather than a change to it (`009d4ff`). Everything else is
`01M3W4D3ZPHGWSV5DPQJB5RZC3`'s build: the extended corpus, no similarity floor in front of the
reranker, the stated-no answer prompt and heading-aware chunking. It supersedes that run as the
baseline and closes Phase 4b. Committed as the harness wrote it.

```bash
make eval-compare BASE=evals/baselines/01M3WD842TTD1Q9FX8TDRTMFAB NEW=<run_id> \
  BAND=evals/baselines/bands/01M3W93XYN42QWH1TCYXN3E5FC.json
```

## Conditions

| | |
|---|---|
| Run id | `01M3WD842TTD1Q9FX8TDRTMFAB`, 2026-10-01 18:57:22Z – 19:06:57Z (620 s of driving) |
| Code | `009d4ff` on `018-hybrid-retrieval`; service source clean (docs-only edits made during the run) |
| Selection | all 146 cases, 168 labelled requests |
| Run clock | `2026-03-02T08:00:00` (a Monday) |
| Corpus | `3af64fc8…28a236d0f`, matching `evals/golden/corpus.json`'s pin; 62 chunks |
| Classification / small-talk model | `claude-haiku-4-5-20251001` |
| Generation model | `claude-sonnet-5` |
| Embedding / rerank | `voyage-4-lite` / `rerank-3` |
| Pool / floors / caps | pool 25; similarity -1.0 (none) / 5; unreranked fallback 0.25; rerank 0.58 / 3 |
| Segment cap | 3 |
| Model spend | $0.94 (`make eval-cost`) |

A prompt change states no new condition, so the comparison against `01M3W4D3…` showed identical
conditions and applied the band automatically. The band was measured on the previous build
(`a171078`); it still describes this one's answerer, which the classifier change does not touch.

## What it measured

| | |
|---|---|
| request count, intent, exact segmentation | 1.000 (146/146), 0.994 (167/168), 0.993 (145/146) |
| similarity hit@1 / @3 / @5, MRR | 0.953 (82/86), 0.977 (84/86), 1.000 (86/86), 0.968 |
| rerank hit@1 / @3 / @5, MRR | 1.000 (86/86) |
| unserved answerable share | 0.011 (1/87) |
| wrong abstention share | 0.000 (0/23) |
| answers on labelled gaps | none |
| tool selection, end to end | 1.000 (24/24) |
| exclusions other than handed-off turns | none |

Against `01M3W4D3…`, intent and segmentation moved outside the band (which never moved them):
`G-s-10` and `G-t-01` from `small_talk` and `G-t-04` from `booking` to `faq_question`, each then
ranked first by both stages and answered. `G-w-05` was answered this time - the comparison marks
it as a case that varied on an unchanged build. The one unserved request is `G-w-02` ("How much is
93000 if I'm paying myself?"), still routed to `small_talk`.
