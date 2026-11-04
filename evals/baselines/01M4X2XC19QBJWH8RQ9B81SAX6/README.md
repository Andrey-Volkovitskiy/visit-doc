# `01M4X2XC19QBJWH8RQ9B81SAX6` — dense retrieval with BM25's top added

The whole golden set at 146 cases, driven and scored on 2026-10-14, on the build that searches a
BM25 branch beside the dense one and hands the reranker the dense top 5 with BM25's top 3 added,
never in place of a dense hit (`cd84c89`). Everything else is `01M3WD842TTD1Q9FX8TDRTMFAB`'s
build. It supersedes that run as the baseline. Committed as the harness wrote it.

```bash
make eval-compare BASE=evals/baselines/01M4X2XC19QBJWH8RQ9B81SAX6 NEW=<run_id>
```

No band applies yet: `bands/01M3W93XYN42QWH1TCYXN3E5FC.json` was measured under dense retrieval,
so a comparison against this run names `retrieval` and `lexical_cap` as conditions it does not
share and marks nothing against it.

## Conditions

| | |
|---|---|
| Run id | `01M4X2XC19QBJWH8RQ9B81SAX6`, 2026-10-14 11:31:40Z – 11:41:16Z (591 s of driving) |
| Code | `cd84c89` on `018-hybrid-retrieval-bm25`; service source clean |
| Selection | all 146 cases, 168 labelled requests |
| Run clock | `2026-03-02T08:00:00` (a Monday) |
| Corpus | `3af64fc8…28a236d0f`, matching `evals/golden/corpus.json`'s pin; 62 chunks |
| Classification / small-talk model | `claude-haiku-4-5-20251001` |
| Generation model | `claude-sonnet-5` |
| Embedding / rerank | `voyage-4-lite` / `rerank-3` |
| Retrieval | `dense_plus_bm25`: pool 25 per branch; dense top 5 + BM25 top 3 (`lexical_cap`) |
| Floors / caps | similarity none; unreranked fallback 0.25; rerank 0.58 / 3 |
| Segment cap | 3 |
| Model spend | $0.94 (`make eval-cost`, Claude calls only); 680 chunks reranked against 545 |

## What it measured

Identical to `01M3WD842TTD1Q9FX8TDRTMFAB` on every metric, and no case moved:

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

The harness scores retrieval by entry, so the run was also read chunk by chunk
(`specs/018-hybrid-retrieval/evaluation/probe/diff_verdicts.py`): no verdict changed in 109 FAQ
requests and the top reranked chunk is the previous baseline's in every one. Six requests carry a
different second or third chunk into the answer - `G-l-02`, `G-m-02` [1], `G-o-01` [1], `G-o-05`,
`G-q-02`, `G-q-03` - exactly the six the offline probe predicted. Two of those answers read
differently: "Which insurance plans do you accept?" (`G-m-02` [1], `G-q-03`) now lists the dental
plans, and `G-m-02` [1] no longer gives the Cigna network detail the displaced chunk held. The one
unserved request is still `G-w-02`, routed to `small_talk`.
