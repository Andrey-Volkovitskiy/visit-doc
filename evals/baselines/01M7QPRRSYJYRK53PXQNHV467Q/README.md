# `01M7QPRRSYJYRK53PXQNHV467Q` — medication advice handed to staff

The whole golden set at 160 cases, driven and scored on 2026-11-18, on the build that gives a
request for medication advice a label, a cause and a notice of its own and never answers one from
the corpus (`ba5919e`, with round-one review fixes in `092c9dd` and G-r-01's history replanted in
`81a643d`). Retrieval, the answerer and the booking loop are `01M4X2XC19QBJWH8RQ9B81SAX6`'s build;
only the classifier prompt and the escalation causes differ. It supersedes that run as the
baseline: the set moved under it - family `y` took `G-s-07` and `G-s-12` and G-r-01's history
changed - so the old run no longer re-scores. Committed as the harness wrote it.

```bash
make eval-compare BASE=evals/baselines/01M7QPRRSYJYRK53PXQNHV467Q NEW=<run_id>
```

No band applies: `bands/01M3W93XYN42QWH1TCYXN3E5FC.json` was measured under dense retrieval on the
146-case set.

## Conditions

| | |
|---|---|
| Run id | `01M7QPRRSYJYRK53PXQNHV467Q`, 2026-11-18 16:10:20Z – 16:18:34Z (542 s of driving), untraced |
| Code | `81a643d` on `main`; service source clean |
| Selection | all 160 cases, 184 labelled requests |
| Run clock | `2026-03-02T08:00:00` (a Monday) |
| Corpus | `3af64fc8…28a236d0f`, matching `evals/golden/corpus.json`'s pin |
| Classification / small-talk model | `claude-haiku-4-5-20251001` |
| Generation model | `claude-sonnet-5` |
| Embedding / rerank | `voyage-4-lite` / `rerank-3` |
| Retrieval | `dense_plus_bm25`: pool 25 per branch; dense top 5 + BM25 top 3 (`lexical_cap`) |
| Floors / caps | similarity none; unreranked fallback 0.25; rerank 0.58 / 3 |
| Segment cap | 3 |
| Model spend | $1.02 (`make eval-cost`, Claude calls only) |

## What it measured

| | |
|---|---|
| request count, intent, exact segmentation | 1.000 (160/160), 0.995 (183/184), 0.994 (159/160) |
| rerank hit@1 / @3 / @5, MRR | 1.000 (87/87) |
| unserved answerable share | 0.034 (3/88) |
| wrong abstention share | 0.080 (2/25) |
| answers on labelled gaps | none |
| tool selection, end to end | 1.000 (24/24) |
| exclusions other than handed-off turns | none |

Every family `y` case took its label, and so did every boundary beside it: the test-results
question stays `not_authorized`, fasting before spirometry and "does the dentist need to know?"
stay `faq_question`. The one segmentation miss is still `G-w-02`, a bare billing code read as
`small_talk`.

Three answerable requests went unserved, against `01M4X2XC…`'s one:

- `G-w-02` - the misclassification above, unchanged.
- `G-w-05` - abstained at generation on the same shortlist and rerank scores it is answered from
  in other runs; the answerer's own variance, which the dense band already records for this case
  (declined 3 in 5).
- `G-o-06` [1] - restated as *"What happens if you don't take my plan?"*, which falls under the
  rerank floor; restated with *"insurance plan"*, it is answered. Replayed against this prompt the
  classifier wrote *"insurance plan"* 5 in 5, and a targeted run of the case served it 3 in 3, so
  the restatement still varies live. Read a movement here as noise until a band of this build
  says otherwise.
