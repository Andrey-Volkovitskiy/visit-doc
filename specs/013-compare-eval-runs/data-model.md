# Phase 1 — Data model

The entities the comparison produces. Every one is a pydantic model with
`ConfigDict(extra="forbid", frozen=True)`, matching the run record and the report, so a stored
comparison is validated on the way back in and nothing mutates one after it is built.

Types named from existing code are imported, never redefined: `ExclusionReason`, `RunConditions`,
`CorpusRecord` and `Selection` from `golden_harness.record`/`cases`, `Metric` and `Exclusions` from
`golden_harness.scoring.metric`, `FaqVerdict` and `IntentLabel` from `chat`.

---

## `RunSide`

One of the two runs under comparison, as the comparison saw it.

| Field | Type | Why it is here |
|---|---|---|
| `run_id` | `str` | identifies the run the numbers came from |
| `location` | `str` | the directory as given, so a baseline under `specs/` is traceable to where it lives (FR-026) |
| `started_at` | `datetime` | which of the two is older, without inferring it from a ULID |
| `conditions` | `RunConditions` | the eleven fields the service stated; the delta is computed from the pair |
| `corpus` | `CorpusRecord` | the pin, live hash and whether they matched |
| `selection` | `Selection` | what the run set out to drive |
| `recorded_cases` | `list[str]` | what it actually recorded — the difference from `selection` is what makes a run incomplete |
| `complete` | `bool` | `recorded_cases` covers `selection.case_ids`; false is reported, never refused (FR-012) |

**Rule**: `complete` is derived at construction and stored rather than recomputed by readers, because
a stored comparison must be re-renderable without the runs (FR-025).

---

## `ConditionDelta` and `ConditionChange`

`ConditionChange`: one field that differs.

| Field | Type |
|---|---|
| `field` | `str` — the `RunConditions` field name, or `corpus_sha256` |
| `base` | `str` — rendered as text, so a float threshold and a model id read alike |
| `new` | `str` |

`ConditionDelta`: `changes: list[ConditionChange]`, ordered as `RunConditions` declares its fields,
with the corpus hash first when it moved.

**Rules**:
- An empty `changes` list means the two runs were measured under identical settings. It is reported
  as such rather than omitted, so "the conditions matched" is a statement the report makes rather
  than one a reader infers from silence.
- A corpus hash difference is a `ConditionChange` like any other and additionally sets
  `corpus_moved: bool` on the comparison, because FR-010 requires it to be as prominent as a
  threshold change and a reader scanning a list can miss one row.

---

## `MetricMovement`

One metric, on both sides.

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | the canonical metric name, from `METRICS_BY_FAMILY` |
| `family` | `MetricFamily` | classification / retrieval / serving / booking |
| `base` | `Metric \| None` | None exactly when the metric was not computed on that side |
| `new` | `Metric \| None` | same |
| `direction` | `MovementDirection` | see below |
| `denominator_moved` | `bool` | FR-016: a numerator holding while the denominator moves is a different fact |
| `band` | `BandVerdict \| None` | None when no band was supplied, or the band has no observation for this metric (FR-034) |

`MovementDirection` (StrEnum): `improved`, `degraded`, `unchanged`, `directionless`,
`not_comparable`.

**Rules**:
- `not_comparable` is the value when one side is `None` or either side's `Metric.value` is
  `not_measured`. FR-015 forbids turning "not computed" into a numeric delta, and 2b already
  distinguishes *not computed* (`Report.not_computed`, no scorer) from *not measured*
  (`Metric.value == "not_measured"`, empty denominator). The comparison preserves both rather than
  collapsing them into zero.
- `improved` and `degraded` are assigned from the metric's own polarity, declared once in a table.
  The table has **three** values, not two: the two zero-target shares improve downward, the six
  `verdict_distribution` rows have no polarity at all, and every other published metric improves
  upward. A distribution row counts what the run produced rather than what it should have produced —
  more answers is an improvement only if answering was right, which is exactly what the two
  zero-target shares measure — so calling one an improvement would be the forced direction FR-019
  forbids for cases. Such a row moves `directionless`. The polarity table is data, not a heuristic
  on the metric's name.
- `unchanged` means both sides produced the same value. It is still published (FR-014), and it may
  still carry case movements beneath it (FR-018).

---

## `BandVerdict`

How a movement sits against the measured noise.

| Field | Type |
|---|---|
| `low` | `float` — the lowest value observed across the band's runs |
| `high` | `float` — the highest |
| `inside` | `bool` — the new run's value lies within `[low, high]` |

**Rules**: a `BandVerdict` is attached only when the band's conditions match both runs' (FR-033) and
the band observed that metric (FR-034). Its presence is what licenses the word "outside the observed
range" anywhere in the output; without it the renderer may only say a value moved (FR-036).

---

## `CaseMovement`

One case's — or one request's — change between the runs. This is the entity that answers "what
actually happened", and the one a reader acts on.

| Field | Type | Notes |
|---|---|---|
| `case_id` | `str` | |
| `position` | `int \| None` | the request position where the group is per request; None where the group is per turn |
| `group` | `MovementGroup` | see below |
| `base` | `str` | the state on the baseline side, rendered for reading |
| `new` | `str` | the state on the new side |
| `direction` | `MovementDirection` | `improved`, `degraded` or `directionless` — never `unchanged`, since an unchanged case is not a movement |
| `question` | `str \| None` | the request's question where one exists, so a finding arrives with the text attached |
| `affects` | `list[str]` | the metric names this movement changed the contribution to, so FR-017's "for every metric, the cases behind it" is answerable in both directions — a movement knows its metrics, and a metric's movements are those naming it. Empty when the movement changes no published metric, which an exclusion movement often does not |
| `labelled` | `Literal["answerable", "a gap"] \| None` | what the label asks of this request in the reader's words, and the only thing licensing the renderer's "; labelled …" clause. Carried rather than re-derived, since a stored comparison is re-rendered without its labels, and checked against its derivation like every other stored derivation here: it is set only on a `verdict` movement, and a directed `verdict` movement always has it, since the label is what directed it |
| `base_excluded` / `new_excluded` | `ExclusionReason \| None` | the turn-level reason each run recorded on the case, None where that run scored it. A verdict keeps the direction its label gives it even on a case a run set aside — the patient did get a different reply, whatever a scorer counted — and this pair is what lets the line say so, rather than leaving a reader to take that direction for a metric that moved. It claims nothing about which metrics the exclusion cost; `affects` beside it names those. On an `exclusion` movement the pair is checked against the two states, which *are* those two reasons |
| `varies_on_its_own` | `bool` | the band observed this case varying on an unchanged build (FR-035) |

`MovementGroup` (StrEnum): `verdict`, `retrieval_rank`, `segmentation`, `tool_selection`,
`database_state`, `exclusion`.

**Rules** — one per group, each derived as research R4 sets out:

| Group | Both states read from | Direction rule |
|---|---|---|
| `verdict` | `CaseRun.assistant_message.request_outcomes[position].verdict` | against the label's `answerable`: answer→abstention on an answerable request is `degraded`, the reverse `improved`; abstention→abstention at a different gate is `directionless` |
| `retrieval_rank` | `RetrievalRequest.similarity` / `.rerank` | a lower rank is `improved`; scored↔excluded is `directionless` |
| `segmentation` | produced segmentation vs the label's intents | moving to agreement is `improved`, away `degraded`, one disagreement to another `directionless`; a case a case-scoped reason set aside on either side is `directionless` too — the classification scorers measured nothing for it, so "not classified" is an absent measurement rather than a disagreement |
| `tool_selection` | `BookingScores.tool_selection_misses` | losing a miss is `improved`, gaining one `degraded`; a case the booking scorers never scored — not a booking case, or set aside — reads as `not scored for booking` and is `directionless`, since the absence of a published miss is not the clean state |
| `database_state` | `BookingScores.task_failures` | losing a failure is `improved`, gaining one `degraded`; `not scored for booking` on either side is `directionless`, as above |
| `exclusion` | `CaseRun.excluded` | always `directionless` (FR-021) — the case left or entered a denominator, which is neither better nor worse |

---

## `AlignmentMovement`

The alignment totals on both sides — the first place a reader looks when a denominator moved.

| Field | Type |
|---|---|
| `base` | `AlignmentTotals` — aligned, unaligned, excluded, labelled, as `scoring.alignment` publishes them |
| `new` | `AlignmentTotals` |
| `moved` | `bool` — any of the four differs |

**Rule**: the totals are reported whether or not they moved, because "alignment held" is what
licenses reading a metric movement as behaviour rather than as a population change.

`ExclusionCount`, used by `Comparison.exclusions`, is the same shape for one exclusion reason:
`base: int`, `new: int`, `moved: bool`.

---

## `CaseCoverage`

What the two runs had in common, and what they did not.

| Field | Type |
|---|---|
| `common` | `list[str]` — scored on both sides; every metric is computed over exactly these (FR-023) |
| `base_only` | `list[str]` — recorded by the baseline alone (FR-022) |
| `new_only` | `list[str]` |
| `restricted` | `bool` — either exclusive list is non-empty |

**Rule**: `restricted` being true is what the renderer prints before any metric, with the count, so a
12-case comparison is never read as a statement about the set.

---

## `Comparison`

The stored record (FR-025, FR-026).

| Field | Type |
|---|---|
| `base` / `new` | `RunSide` |
| `conditions` | `ConditionDelta` |
| `corpus_moved` | `bool` |
| `coverage` | `CaseCoverage` |
| `metrics` | `list[MetricMovement]` — every metric either run published, in report order |
| `cases` | `list[CaseMovement]` — every movement, ordered by case id then position |
| `alignment` | `AlignmentMovement` — aligned / unaligned / excluded on both sides |
| `exclusions` | `dict[ExclusionReason, ExclusionCount]` — each reason's count on both sides. A named pair rather than a two-element tuple: a positional pair in a stored record leaves every reader to guess which end is the baseline |
| `band_id` | `str \| None` — the band the movements were marked against |
| `band_applicable` | `bool` — false when a band was given but its conditions differ (FR-033) |
| `compared_at` | `datetime` |
| `compare_seconds` | `float` — the only field a re-run may differ in (FR-007, SC-003) |

---

## `NoiseBand`

| Field | Type |
|---|---|
| `band_id` | `str` — a ULID minted when the band is built, as a run id is, so two bands over the same five runs are still distinguishable and a band sorts by when it was measured |
| `run_ids` | `list[str]` — the five runs, in the order taken |
| `conditions` | `RunConditions` | shared by all five; a difference is refused (FR-030) |
| `corpus_sha256` | `str` |
| `clock` | `datetime` — the local time all five were driven on; a difference is refused, and a comparison whose runs used another clock is not marked against this band |
| `case_ids` | `list[str]` — the case set all five ran |
| `metrics` | `dict[str, MetricObservation]` |
| `varying_cases` | `list[CaseVariation]` |
| `measured_at` | `datetime` |

`MetricObservation`: `name: str`, `values: list[float]` (one per run, in run order), `low: float`,
`high: float`. Storing every value, not just the range, is what lets a later reader see whether four
runs agreed and one did not — and costs five floats.

`CaseVariation`: `case_id: str`, `position: int | None`, `group: MovementGroup`,
`outcomes: dict[str, int]` — each observed state and how many of the five runs produced it. This is
the frequency count the move from three runs to five bought (research R7).

**Rules**:
- A band is built only from runs whose `RunConditions`, corpus hash, clock and label digests all
  match (FR-030); any difference is refused, naming the field and the run.
- The five runs are five *different* runs. One run named five times agrees with itself in every
  field above and produces a zero-wide range and an empty variation list — one observation wearing
  five — so it is refused, naming the run.
- `run_ids`, every `MetricObservation.values` and every `CaseVariation.outcomes` each count five,
  on the way in as well as on the way out: a stored band whose counts say otherwise renders
  sentences ("the spread of five observations", "in 4 of 5") that its own numbers contradict.
- A band carries no verdict, no threshold and no target. Nothing in it says what a run *should*
  score (FR-038).
- The band's own record must state that it is five observations (FR-032). That sentence lives with
  the rendered output rather than as a data field, because it is a statement about the band as a
  whole, not a property of any row.

---

## What is deliberately not modelled

- **No score.** There is no overall "better/worse" number for a comparison. A run can improve one
  metric and degrade another, and a single figure summarising both is the "one value, two meanings"
  defect this project keeps removing — the same reasoning that deleted 1h's `summarize_verdict`.
- **No regression flag without a band.** There is no boolean called `regression` anywhere. Movement
  is what the model carries; whether a movement is outside the measured noise is the `BandVerdict`,
  and it exists only when a band does.
- **No stored copy of either run's report.** The comparison holds movements, not a snapshot of two
  reports. Anyone wanting the full numbers has both run directories, which is what `RunSide.location`
  records.
