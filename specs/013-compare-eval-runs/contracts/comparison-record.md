# Contract: what a comparison stores and prints

A comparison is a read over two runs, and its own output is an artifact like theirs: machine-readable
first, rendered from that (FR-025). The shape is a contract because FR-007 requires a re-run to
produce the same record, and because a stored comparison must re-render without its inputs.

## Where it goes

```text
.run/evals/comparisons/<base run id>__<new run id>/
├── comparison.json
└── comparison.md
```

Never inside either input run's directory (research R8): the new run may be the committed baseline
under `specs/`, or a run someone else will use as a baseline later, and a read must not add files to
its input. The comparisons directory is under the harness's own artifacts root, which `.gitignore`
already covers.

Re-comparing the same pair overwrites in place. The record carries `compared_at`, so the file says
which run of the comparison it is.

## `comparison.json`

The serialized `Comparison` of [`data-model.md`](../data-model.md), written with the same
`to_json` treatment the report uses — `model_dump(mode="json")`, indented, keys sorted — so a diff
between two comparison files is readable.

**Reproducibility**: re-running `compare` over the same two stored runs produces a byte-identical
file apart from its two timing fields, `compared_at` and `compare_seconds` (FR-007, SC-003).
Everything else is a pure
function of the inputs, including every list's order, which is fixed: metrics in report order, cases
by case id then position then group.

## `comparison.md` — the order the summary is read in

The order is part of the contract, because it is what stops a reader drawing the wrong conclusion
from a real number.

1. **The two runs.** Ids, locations, when each was taken, and whether either is incomplete (FR-012).
2. **The condition delta** (FR-008). Every differing field with both values, the corpus hash first
   when it moved (FR-010). When nothing differs, the line says so explicitly.
3. **The coverage restriction** (FR-023), when the runs cover different case sets: the count of
   common cases, and the cases on one side only (FR-022).
4. **The band**, when one was supplied: its id, that it is five observations and not a confidence
   interval (FR-032), and — when its conditions differ from the runs' — that it is not being applied
   (FR-033).
5. **Alignment and exclusions** on both sides. A moved exclusion count explains a moved denominator
   before any metric does.
6. **The metrics**, by family, every one of them (FR-013, FR-014), each with both values, both
   numerators and denominators, the direction, a mark when the denominator moved (FR-016), and the
   band verdict where one applies.
7. **The case movements**, grouped by `MovementGroup` (FR-020), each with both states, the question
   where there is one, the metrics it changed the contribution to (FR-017, `CaseMovement.affects`),
   and a mark for a case the band saw vary on its own (FR-035). Each metric in section 6 names the
   movements that affect it, so the two readings — by metric and by kind of change — are the same
   list seen from two sides rather than two lists that can disagree.
8. **Cases that moved under an unchanged metric** (FR-018), called out as their own section — the
   churn a delta of zero hides.

## Language rules

These are output rules, not prose preferences, because FR-036 is about what the report may claim.

- Without a band, a movement is described as **moved**, **up** or **down**. The words *regression*,
  *improvement of the system*, *better* and *worse* do not appear about a metric.
- With a band, a movement outside it is **outside the observed range of five runs**; inside it is
  **within the observed range**. Neither is called statistically significant, because it is not.
- A case movement's `improved` / `degraded` is about **that case against its label** — this request
  is now answered where the label says it is answerable — and the renderer says it in those terms,
  so a reader never reads a per-case judgement as a verdict on the build.
- A movement on a case a run **set aside** says so, naming the side and the reason: *the case was
  set aside in the new run (missing_log_slice)*. A request's verdict is a fact about the reply the
  patient got, so it keeps the direction its label gives it there; the clause is what stops that
  direction being read as a metric that moved, since the case counted towards less — or nothing —
  on that side. The clause makes no claim about which metrics the exclusion cost, because that
  varies by reason: the `affects` list printed beside it names the ones that changed. The exclusion
  group is silent, its own two states being those two reasons.
- A metric with no scorer on one side is **not computed**; a metric with an empty denominator is
  **not measured**. Both already exist in 2b with those exact names, and neither becomes a delta
  (FR-015).

## Worked shape

```text
Base: 01M2JZZ… (specs/012-golden-set-metrics/evaluation/), taken 2026-09-15, complete
New:  01M3ABC… (.run/evals/01M3ABC…), taken 2026-09-16, complete

Conditions: rerank_floor 0.58 -> 0.52. Corpus unchanged. 1 field differs.
Coverage: 135 cases in both.
Band: none supplied — movements are reported, not judged.

Serving
| metric | base | new | direction | band |
| unserved_answerable_share | 0.069 (6/87) | 0.034 (3/87) | down | - |
| wrong_abstention_share | 0.190 (4/21) | 0.056 (1/18) | down, denominator moved | - |

Case movements — verdict (3)
- G016 [0] abstained_rerank_floor -> answered   (improved; labelled answerable) "Is parking free?"
- G096 [1] abstained_rerank_floor -> answered   (improved; labelled answerable) "is parking free?"
- G024 [0] answered -> answered                 (no movement; not listed)
```

The last line is the point of the example: an unchanged case does not appear. What appears under a
group is what moved.
