# `01M321DWRXSVSY7GW9RY3CR9YW` — the first scoreable v2 run

The whole golden set at 97 cases, driven and scored on 2026-09-21. Committed as the harness wrote
it: `run.json` (conditions, label digests, corpus pin, entry-id map), `cases/*.json` (one per
driven case, what every number was computed from), and `report.json`/`report.md` (the report the
run printed).

```bash
make eval-compare BASE=evals/baselines/01M321DWRXSVSY7GW9RY3CR9YW NEW=<run_id>
```

A comparison re-scores both runs from their case records and never reads a stored `report.json`,
so a later phase may add a field without making this directory unparseable.

## Conditions

| | |
|---|---|
| Run id | `01M321DWRXSVSY7GW9RY3CR9YW`, 2026-09-21 13:10:33Z – 13:18:30Z (498 s of driving) |
| Code | `12341e1` on `new-golden-set`, clean tree |
| Selection | all 97 cases, 117 labelled requests |
| Run clock | `2026-03-02T08:00:00` (a Monday) |
| Corpus | `3d94d021…6c9ec7b`, matching `evals/golden/corpus.json`'s pin |
| Classification model | `claude-haiku-4-5-20251001` |
| Generation model | `claude-sonnet-5` |
| Embedding / rerank | `voyage-4-lite` / `rerank-3` |
| Floors / caps | similarity 0.25 / 5, rerank 0.58 / 3 |
| Segment cap | 3 |

## What it measured

Every metric is 1.000 except one, and that one is not a miss:

| | |
|---|---|
| request count, intent, exact segmentation | 1.000 (97/97, 117/117, 97/97) |
| rerank hit@1/@3/@5, MRR | 1.000 (43/43) |
| similarity hit@1 | 0.977 (42/43) |
| similarity gate survival | 1.000 (43/43) |
| unserved answerable share | 0.000 (0/43) |
| wrong abstention share | 0.000 (0/17) |
| answers on labelled gaps | none |
| tool selection, end to end | 1.000 (23/23) |
| retries, assistant_failed, broken streams | none |

The single `similarity_hit_at_1` miss is G-o-06's second request, "what if you don't take my
insurance plan?". Two insurance entries sat 0.004 apart at the embedding stage and the wrong one
led; the cross-encoder scored the labelled entry 0.715 and dropped the other below the floor, so
the request was answered from the right chunk. It is the two-stage design doing what the two
metrics exist to tell apart.

All 17 abstentions fell on labelled gaps and every labelled-answerable request was answered — 14
stopped at the rerank floor, 1 at the similarity floor, and 2 at generation, which is the
`abstained_generation` verdict added in `12341e1` doing its work on G-k-15 and G-k-16.

## What it does not measure

A question answerable only by combining two corpus entries. `answer_faq`'s module comment names
one — "what should I do before my visit?", which the arrival and what-to-bring entries answer
together — and no case here covers it. Measured by hand against this build it returns the
no-answer sentinel every time, so a perfect score above says nothing about it.
