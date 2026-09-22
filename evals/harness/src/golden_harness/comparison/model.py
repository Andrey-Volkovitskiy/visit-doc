"""What a comparison of two stored runs holds: movements, and what they are read under.

Every model here is frozen and refuses an undeclared field, as the run record and the
report are, so nothing mutates a comparison after it is built and a stored one is
validated on the way back in. Types the run record and the scorers already define - the
conditions, the selection, a metric, the exclusion reasons, the alignment totals - are
imported rather than restated, so a comparison can never describe a metric in terms the
report does not use.

Two rules run through the whole file. A value that is derived is stored *and* checked
against what it was derived from, because a stored comparison must re-render without
its runs (FR-025) and a reader of the file cannot recompute it. And no movement is
forced into better or worse: `directionless` and `not_comparable` are values, not
omissions, since a metric one run did not compute and an abstention that changed gate
are both real answers to "what moved" (FR-015, FR-019).
"""

from datetime import datetime
from enum import StrEnum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from golden_harness.cases import Selection
from golden_harness.record import (
    TURN_LEVEL_REASONS,
    CorpusRecord,
    ExclusionReason,
    RunConditions,
    RunTracing,
)
from golden_harness.report import MetricFamily
from golden_harness.scoring.alignment import AlignmentTotals
from golden_harness.scoring.metric import NOT_MEASURED, Metric


class MovementDirection(StrEnum):
    """Which way something moved, or why that question has no answer.

    `unchanged` is a metric whose two values are equal; `directionless` a change that
    is neither better nor worse - an abstention that moved between gates, a case that
    left a denominator; `not_comparable` a metric one of the two runs did not compute
    or did not measure.
    """

    IMPROVED = "improved"
    DEGRADED = "degraded"
    UNCHANGED = "unchanged"
    DIRECTIONLESS = "directionless"
    NOT_COMPARABLE = "not_comparable"


# What a label asks of one request, in the words the report prints. A closed pair
# rather than free text: it is a stored derivation of the label's `answerable`, and a
# third word would be a claim no label makes.
LabelledExpectation = Literal["answerable", "a gap"]


# How an exclusion movement renders a run that set nothing aside. It lives here, beside
# the validator that checks an exclusion movement's states against its reasons, so the
# word and the check cannot drift apart.
SCORED: Final = "scored"


class MovementGroup(StrEnum):
    """What changed about a case - one group per question a reader would ask.

    A verdict that moved while its rank held is a gate's doing; a rank that moved
    without the verdict is retrieval churn that has not cost anything yet (FR-020).
    """

    VERDICT = "verdict"
    RETRIEVAL_RANK = "retrieval_rank"
    SEGMENTATION = "segmentation"
    TOOL_SELECTION = "tool_selection"
    DATABASE_STATE = "database_state"
    EXCLUSION = "exclusion"


class RunSide(BaseModel):
    """One of the two runs under comparison, as the comparison saw it.

    `location` is the directory as it was given, so a baseline under `specs/` is
    traceable to where it lives (FR-026). `complete` is derived from `recorded_cases`
    against `selection` and stored, since a stored comparison is re-rendered without
    the runs; an incomplete run is reported, never refused (FR-012). `tracing` is the
    run's own, shown beside its id and never compared: a comparison stored before runs
    recorded it reads as a run the service could not trace.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    location: str
    started_at: datetime
    conditions: RunConditions
    corpus: CorpusRecord
    selection: Selection
    recorded_cases: list[str]
    complete: bool
    tracing: RunTracing = RunTracing.UNTRACED_SERVICE_OFF

    @model_validator(mode="after")
    def _completeness_follows_the_recorded_cases(self) -> "RunSide":
        """Refuse a `complete` that disagrees with what the run recorded."""
        covered = set(self.selection.case_ids) <= set(self.recorded_cases)
        if self.complete != covered:
            raise ValueError(
                f"complete={self.complete} disagrees with {len(self.recorded_cases)} "
                f"cases recorded of {len(self.selection.case_ids)} selected"
            )
        return self


class ConditionChange(BaseModel):
    """One condition field that differs, with both values rendered as text.

    Rendered rather than typed, so a float threshold and a model id read alike in one
    list and neither needs a reader to know which is which.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    base: str
    new: str


class ConditionDelta(BaseModel):
    """Every condition field that differs: corpus, clock, then declaration order.

    The corpus hash and the run clock lead because neither is a `RunConditions` field -
    the run records both beside them - and a reader scanning eleven rows can miss one.
    The rest follow in `RunConditions` declaration order.

    An empty `changes` is reported as "the conditions matched" rather than omitted, so
    that is a statement the report makes rather than one a reader infers from silence.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    changes: list[ConditionChange]

    @property
    def identical(self) -> bool:
        """Whether the two runs were measured under identical settings."""
        return not self.changes


class BandVerdict(BaseModel):
    """How one metric's new value sits against the band's observed range.

    Its presence is what licenses the words "outside the observed range" anywhere in
    the output; without it the renderer may only say a value moved (FR-036).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    low: float
    high: float
    inside: bool

    @model_validator(mode="after")
    def _the_range_runs_low_to_high(self) -> "BandVerdict":
        """Refuse a range whose low exceeds its high."""
        if self.low > self.high:
            raise ValueError(f"a band's low {self.low} exceeds its high {self.high}")
        return self


class MetricMovement(BaseModel):
    """One metric on both sides, with what it did and what it could not say.

    `base` or `new` is None exactly when that run did not compute the metric, and
    `delta` is then None: "not computed" never becomes a numeric difference (FR-015).
    `denominator_moved` is carried beside the direction because a numerator holding
    while the denominator moves is a different fact about the run (FR-016).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    family: MetricFamily
    base: Metric | None
    new: Metric | None
    direction: MovementDirection
    denominator_moved: bool
    band: BandVerdict | None = None

    @field_validator("base", "new", mode="before")
    @classmethod
    def _accept_a_serialized_metric_back(cls, value: object) -> object:
        """Drop the values pydantic computes when re-reading a stored comparison.

        `Metric.value` and `Exclusions.total` are computed fields: they are written
        into the JSON and refused on the way back in, since both models forbid an
        undeclared field. Dropping them here is what makes a stored comparison
        re-readable - and they are recomputed from the numerator, denominator and
        counts beside them, so nothing is lost.
        """
        if not isinstance(value, dict):
            return value
        metric = {name: field for name, field in value.items() if name != "value"}
        excluded = metric.get("excluded")
        if isinstance(excluded, dict):
            metric["excluded"] = {
                name: field for name, field in excluded.items() if name != "total"
            }
        return metric

    @model_validator(mode="after")
    def _a_missing_side_is_not_comparable(self) -> "MetricMovement":
        """Refuse any direction but `not_comparable` when a side did not compute."""
        missing = self.base is None or self.new is None
        if missing and self.direction is not MovementDirection.NOT_COMPARABLE:
            raise ValueError(
                "a metric one run did not compute is not_comparable, "
                f"not {self.direction.value}"
            )
        return self

    @property
    def delta(self) -> float | None:
        """Return `new` minus `base`, or None where no difference can be taken.

        None covers both situations FR-015 keeps apart from a zero: a metric one run
        did not compute, and one whose denominator was empty on either side.
        """
        if self.base is None or self.new is None:
            return None
        base, new = self.base.value, self.new.value
        if base == NOT_MEASURED or new == NOT_MEASURED:
            return None
        return float(new) - float(base)


class CaseMovement(BaseModel):
    """One case's - or one request's - change between the runs.

    `position` is the request position where the group is per request, and None where
    it is per turn. `affects` names the metrics this movement changed the contribution
    to, so FR-017 is answerable in both directions: a movement knows its metrics, and a
    metric's movements are those naming it. It is empty when the movement changes no
    published metric, which an exclusion movement often does not.

    `base_excluded` and `new_excluded` are the turn-level reason each run recorded on
    the case, None where that run scored it. They are carried so a line can say a
    movement sits on a case a run set aside: a verdict keeps the direction its label
    gives it there - the patient really did get a different reply - and the reason is
    what stops a reader taking that direction for a metric that moved. What each
    exclusion cost is not claimed here; `affects` beside it names the metrics that
    actually changed.

    `labelled` is what the label asks of this request in the reader's words -
    `answerable` or `a gap` - and is what licenses the renderer's "; labelled ..."
    clause. It is carried rather than re-derived because a stored comparison is
    re-rendered without its labels, and it is None wherever the label asks nothing
    directed, which is every group but `verdict`. Being derived and stored, it is
    checked against what it was derived from, as every other derived field here is: the
    two words are the type, and the two rules below are the derivation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    position: int | None
    group: MovementGroup
    base: str
    new: str
    direction: Literal[
        MovementDirection.IMPROVED,
        MovementDirection.DEGRADED,
        MovementDirection.DIRECTIONLESS,
    ]
    question: str | None
    affects: list[str]
    labelled: LabelledExpectation | None = None
    base_excluded: ExclusionReason | None = None
    new_excluded: ExclusionReason | None = None
    varies_on_its_own: bool = False

    @model_validator(mode="after")
    def _a_movement_moved(self) -> "CaseMovement":
        """Refuse a movement whose two states are the same."""
        if self.base == self.new:
            raise ValueError(
                f"{self.case_id}: a movement's two states differ, not {self.base!r} "
                "twice"
            )
        return self

    @model_validator(mode="after")
    def _an_exclusion_is_one_a_case_can_carry(self) -> "CaseMovement":
        """Refuse a reason no case record could have stored.

        The record admits only turn-level reasons on a case (`CaseRun.excluded`); a
        request-level one is derived per request at scoring time and describes a
        request, never the whole case. Restating the rule here is what keeps a
        hand-edited stored comparison from printing one.
        """
        for side, reason in (("base", self.base_excluded), ("new", self.new_excluded)):
            if reason is not None and reason not in TURN_LEVEL_REASONS:
                raise ValueError(
                    f"{self.case_id}: {reason.value} is decided per request, so it is "
                    f"not an exclusion the {side} run recorded on the case"
                )
        return self

    @model_validator(mode="after")
    def _an_exclusion_movement_agrees_with_its_own_states(self) -> "CaseMovement":
        """Refuse an exclusion movement whose states are not the reasons beside it.

        This group's two states *are* the two exclusions rendered, so the pair is
        checked against them - the rule this file follows for every derived value it
        stores. The other groups have nothing here to check against, and carry the
        reasons as context rather than as their subject.
        """
        if self.group is not MovementGroup.EXCLUSION:
            return self
        for side, state, reason in (
            ("base", self.base, self.base_excluded),
            ("new", self.new, self.new_excluded),
        ):
            rendered = reason.value if reason is not None else SCORED
            if state != rendered:
                raise ValueError(
                    f"{self.case_id}: the {side} state {state!r} disagrees with the "
                    f"exclusion beside it, {rendered!r}"
                )
        return self

    @model_validator(mode="after")
    def _only_a_verdict_names_what_the_label_asks(self) -> "CaseMovement":
        """Refuse a `labelled` no label produced, and a directed verdict without one.

        Only a request's verdict is judged against `answerable`; a segmentation, a
        booking or an exclusion is judged against something else or against nothing, and
        a "; labelled answerable" printed beside one would attribute the judgement to a
        label that never made it. The converse is what the renderer relies on: a verdict
        movement is directed *because* the label asked something of that request, so a
        directed one always knows which of the two words it was.
        """
        if self.labelled is not None and self.group is not MovementGroup.VERDICT:
            raise ValueError(
                f"{self.case_id}: only a verdict movement names what the label asks, "
                f"not a {self.group.value} one"
            )
        directed = self.direction is not MovementDirection.DIRECTIONLESS
        if self.group is MovementGroup.VERDICT and directed and self.labelled is None:
            raise ValueError(
                f"{self.case_id}: a verdict movement directed {self.direction.value} "
                "was directed by a label, and names which of the two it asked"
            )
        return self


class CaseCoverage(BaseModel):
    """What the two runs had in common, and what only one of them recorded.

    Every metric is computed over `common` alone (FR-023). `restricted` is what the
    renderer prints before any metric, with the count, so a twelve-case comparison is
    never read as a statement about the set.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    common: list[str]
    base_only: list[str]
    new_only: list[str]
    restricted: bool

    @model_validator(mode="after")
    def _restriction_follows_the_exclusive_lists(self) -> "CaseCoverage":
        """Refuse a `restricted` that disagrees with the two exclusive lists."""
        exclusive = bool(self.base_only or self.new_only)
        if self.restricted != exclusive:
            raise ValueError(
                f"restricted={self.restricted} disagrees with {len(self.base_only)} "
                f"baseline-only and {len(self.new_only)} new-only cases"
            )
        return self


class AlignmentMovement(BaseModel):
    """The alignment totals on both sides, and whether any of the four differ.

    Reported whether or not they moved: "alignment held" is what licenses reading a
    metric movement as behaviour rather than as a population change.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    base: AlignmentTotals
    new: AlignmentTotals
    moved: bool

    @model_validator(mode="after")
    def _moved_follows_the_totals(self) -> "AlignmentMovement":
        """Refuse a `moved` that disagrees with the two sets of totals."""
        if self.moved != (self.base != self.new):
            raise ValueError(
                f"moved={self.moved} disagrees with the totals it is taken from"
            )
        return self


class ExclusionCount(BaseModel):
    """One exclusion reason's count on both sides, and whether it moved.

    A named pair rather than a two-element tuple: a positional pair in a stored record
    leaves every reader to guess which end is the baseline.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    base: int
    new: int
    moved: bool

    @model_validator(mode="after")
    def _moved_follows_the_counts(self) -> "ExclusionCount":
        """Refuse a `moved` that disagrees with the two counts."""
        if self.moved != (self.base != self.new):
            raise ValueError(
                f"moved={self.moved} disagrees with counts {self.base} and {self.new}"
            )
        return self


class Comparison(BaseModel):
    """The stored record of one comparison (FR-025, FR-026).

    There is deliberately no overall score and no `regression` flag: a run can improve
    one metric and degrade another, and a single figure summarising both is the "one
    value, two meanings" defect this project keeps removing. `band_applicable` is false
    when a band was given whose conditions differ from the runs' (FR-033), which is why
    it is not merely `band_id is not None`. `compare_seconds` and `compared_at` are the
    only two fields a re-comparison of the same pair may differ in (FR-007).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    base: RunSide
    new: RunSide
    conditions: ConditionDelta
    corpus_moved: bool
    coverage: CaseCoverage
    metrics: list[MetricMovement]
    cases: list[CaseMovement]
    alignment: AlignmentMovement
    exclusions: dict[ExclusionReason, ExclusionCount]
    band_id: str | None
    band_applicable: bool
    compared_at: datetime
    compare_seconds: float

    @model_validator(mode="after")
    def _a_band_applies_only_when_one_was_given(self) -> "Comparison":
        """Refuse an applicable band nobody supplied."""
        if self.band_applicable and self.band_id is None:
            raise ValueError("no band was given, so none can be applicable")
        return self
