"""Alignment: pairing each labelled request with a produced one, or saying why not.

Every labelled request of a recorded case lands in exactly one of three states. A case
set aside by a case-scoped reason is `excluded` and never compared. Otherwise its
produced requests pair with its labelled ones by position when the counts are equal
(`aligned`), and when they differ every labelled request of the case is `unaligned` -
scored for nothing, and never dropped.

The conservation rule - aligned, unaligned and excluded requests together are exactly
the labelled requests, each once - is asserted here about the scorer itself rather than
left for a reader of the report to check.
"""

from collections import Counter
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

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
    """A labelled request and the produced request at the same position."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    position: int
    labelled: LabelledRequest
    produced: ProducedSegment


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
    if len(produced) != len(case.requests):
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
    return Alignment(
        case_id=case.id,
        state=AlignmentState.ALIGNED,
        labelled_count=len(case.requests),
        produced_count=len(produced),
        pairs=[
            AlignedPair(position=position, labelled=labelled, produced=segment)
            for position, (labelled, segment) in enumerate(
                zip(case.requests, produced, strict=True)
            )
        ],
        unaligned_positions=[],
        excluded_positions=[],
        turn_exclusion=case_run.excluded,
    )


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
