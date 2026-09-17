"""Alignment: pairing each labelled request with a produced one, or saying why not.

Every labelled request of a recorded case lands in exactly one of three states. A case
set aside by a case-scoped reason is `excluded` and never compared. Otherwise its
produced requests are grouped onto its labelled ones in order (`aligned`): each labelled
request takes one produced request, except that a labelled `faq_question` may take a run
of several consecutive produced `faq_question`s. A question the classifier split in two
is still answered in one reply that carries both halves, so splitting it is not a
failure - but a question split into a `faq_question` and anything else is, since the
other half never reaches the FAQ answer. When no grouping fits, or more than one does,
every labelled request of the case is `unaligned` - scored for nothing, and never
dropped. Two fitting groupings are refused rather than chosen between, because which
labelled question a produced half belongs to would then be a guess.

The conservation rule - aligned, unaligned and excluded requests together are exactly
the labelled requests, each once - is asserted here about the scorer itself rather than
left for a reader of the report to check.
"""

from collections import Counter
from collections.abc import Sequence
from enum import StrEnum

from chat.domain.schemas import IntentLabel
from pydantic import BaseModel, ConfigDict, Field, model_validator

from golden_harness.cases import Case, LabelledRequest
from golden_harness.record import CaseRun, ExclusionReason, ProducedSegment
from golden_harness.scoring.metric import CASE_SCOPED_REASONS


class ConservationError(AssertionError):
    """A labelled request is in no alignment state, or in more than one."""


class AlignmentState(StrEnum):
    """Where a case's labelled requests stand for per-request scoring."""

    ALIGNED = "aligned"
    UNALIGNED = "unaligned"
    EXCLUDED = "excluded"


class AlignedPair(BaseModel):
    """A labelled request and the produced requests grouped onto it.

    `position` is the labelled request's position. `produced` is never empty, holds
    consecutive produced requests in order, and holds more than one only when the
    labelled request and every produced one are `faq_question`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    position: int
    labelled: LabelledRequest
    produced: list[ProducedSegment] = Field(min_length=1)

    @model_validator(mode="after")
    def _only_a_faq_question_takes_several(self) -> "AlignedPair":
        """Refuse a group of several unless it is FAQ questions throughout."""
        faq = IntentLabel.FAQ_QUESTION
        several = len(self.produced) > 1
        if several and (
            self.labelled.intent is not faq
            or any(segment.intent is not faq for segment in self.produced)
        ):
            raise ValueError("only a faq_question groups several produced requests")
        positions = [segment.position for segment in self.produced]
        if positions != list(range(positions[0], positions[0] + len(positions))):
            raise ValueError(f"grouped positions are not consecutive: {positions}")
        return self


class Alignment(BaseModel):
    """One case's alignment.

    The labelled positions are split across `pairs` (aligned), `unaligned_positions`
    and `excluded_positions` according to `state`. `produced_count` is None for an
    excluded case, which is never compared. `turn_exclusion` is the reason stored on
    the case: a case-scoped one for an excluded case, and `handed_off_turn` for a case
    that still aligns but is set aside by the retrieval and serving scorers.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    state: AlignmentState
    labelled_count: int
    produced_count: int | None
    pairs: list[AlignedPair]
    unaligned_positions: list[int]
    excluded_positions: list[int]
    turn_exclusion: ExclusionReason | None


class AlignmentTotals(BaseModel):
    """Labelled requests per alignment state over a run, beside their total."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    aligned: int
    unaligned: int
    excluded: int
    labelled: int

    @classmethod
    def of(
        cls, alignments: Sequence[Alignment], cases: Sequence[Case]
    ) -> "AlignmentTotals":
        """Total a run's alignments, asserting each labelled request is in one state.

        Args:
            cases: the labels of the recorded cases; ids with no alignment are ignored.

        Raises: ConservationError when an alignment accounts for a labelled position
            twice or not at all, names a position its case does not have, or has no
            label, or when a case is aligned twice.
        """
        by_id = {case.id: case for case in cases}
        repeated = sorted(
            case_id
            for case_id, n in Counter(a.case_id for a in alignments).items()
            if n > 1
        )
        if repeated:
            raise ConservationError(f"cases aligned more than once: {repeated}")

        labelled = 0
        for alignment in alignments:
            case = by_id.get(alignment.case_id)
            if case is None:
                raise ConservationError(f"{alignment.case_id} has no label")
            accounted = sorted(
                [pair.position for pair in alignment.pairs]
                + alignment.unaligned_positions
                + alignment.excluded_positions
            )
            if accounted != list(range(len(case.requests))):
                expected = list(range(len(case.requests)))
                raise ConservationError(
                    f"{alignment.case_id}: labelled positions {expected} "
                    f"are accounted for as {accounted}"
                )
            labelled += len(case.requests)

        totals = cls(
            aligned=sum(len(a.pairs) for a in alignments),
            unaligned=sum(len(a.unaligned_positions) for a in alignments),
            excluded=sum(len(a.excluded_positions) for a in alignments),
            labelled=labelled,
        )
        if totals.aligned + totals.unaligned + totals.excluded != totals.labelled:
            raise ConservationError(f"alignment states do not sum: {totals}")
        return totals


class RunAlignment(BaseModel):
    """Every recorded case's alignment, in record order, and the run's totals."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    alignments: list[Alignment]
    totals: AlignmentTotals


def align_case(case: Case, case_run: CaseRun) -> Alignment:
    """Align one recorded case against its label.

    Raises: ValueError when the record is of another case, or when a case no
        case-scoped reason excludes carries no produced segmentation.
    """
    if case.id != case_run.case_id:
        raise ValueError(f"label {case.id} cannot align record {case_run.case_id}")

    positions = list(range(len(case.requests)))
    if case_run.excluded in CASE_SCOPED_REASONS:
        return Alignment(
            case_id=case.id,
            state=AlignmentState.EXCLUDED,
            labelled_count=len(case.requests),
            produced_count=None,
            pairs=[],
            unaligned_positions=[],
            excluded_positions=positions,
            turn_exclusion=case_run.excluded,
        )

    if case_run.segments is None:
        raise ValueError(
            f"{case.id} has no produced segmentation and no case-scoped exclusion"
        )
    produced = case_run.segments.segments
    groupings = _groupings(case.requests, produced)
    if len(groupings) != 1:
        return Alignment(
            case_id=case.id,
            state=AlignmentState.UNALIGNED,
            labelled_count=len(case.requests),
            produced_count=len(produced),
            pairs=[],
            unaligned_positions=positions,
            excluded_positions=[],
            turn_exclusion=case_run.excluded,
        )
    (sizes,) = groupings
    pairs: list[AlignedPair] = []
    start = 0
    for position, (labelled, size) in enumerate(zip(case.requests, sizes, strict=True)):
        pairs.append(
            AlignedPair(
                position=position,
                labelled=labelled,
                produced=list(produced[start : start + size]),
            )
        )
        start += size
    return Alignment(
        case_id=case.id,
        state=AlignmentState.ALIGNED,
        labelled_count=len(case.requests),
        produced_count=len(produced),
        pairs=pairs,
        unaligned_positions=[],
        excluded_positions=[],
        turn_exclusion=case_run.excluded,
    )


def _groupings(
    labelled: Sequence[LabelledRequest], produced: Sequence[ProducedSegment]
) -> list[tuple[int, ...]]:
    """Return every way to group `produced`, in order, onto `labelled`.

    Returns: one tuple per fitting grouping, holding how many produced requests each
        labelled request takes, in labelled order.

    A labelled request takes exactly one produced request, whatever its intent - a
    wrong intent is scored as a wrong intent, not as a failure to align. A labelled
    `faq_question` may instead take two or more consecutive produced requests, all of
    them `faq_question`.
    """
    faq = IntentLabel.FAQ_QUESTION

    def fits(request: int, start: int) -> list[tuple[int, ...]]:
        if request == len(labelled):
            return [()] if start == len(produced) else []
        found: list[tuple[int, ...]] = []
        remaining = len(labelled) - request - 1
        largest = len(produced) - start - remaining
        for size in range(1, largest + 1):
            group = produced[start : start + size]
            if size > 1 and (
                labelled[request].intent is not faq
                or any(segment.intent is not faq for segment in group)
            ):
                break
            found.extend((size, *rest) for rest in fits(request + 1, start + size))
        return found

    return fits(0, 0)


def align_run(cases: Sequence[Case], case_runs: Sequence[CaseRun]) -> RunAlignment:
    """Align every recorded case of a run and total the result.

    Args:
        cases: the labels; only those of recorded cases enter the totals.

    Raises: ValueError when a recorded case has no label or is recorded twice;
        ConservationError when the totals do not account for every labelled request.
    """
    by_id = {case.id: case for case in cases}
    repeated = sorted(
        case_id
        for case_id, n in Counter(run.case_id for run in case_runs).items()
        if n > 1
    )
    if repeated:
        raise ValueError(f"cases recorded more than once: {', '.join(repeated)}")
    unlabelled = sorted(run.case_id for run in case_runs if run.case_id not in by_id)
    if unlabelled:
        raise ValueError(f"recorded cases with no label: {', '.join(unlabelled)}")

    alignments = [align_case(by_id[run.case_id], run) for run in case_runs]
    recorded = [by_id[run.case_id] for run in case_runs]
    return RunAlignment(
        alignments=alignments, totals=AlignmentTotals.of(alignments, recorded)
    )
