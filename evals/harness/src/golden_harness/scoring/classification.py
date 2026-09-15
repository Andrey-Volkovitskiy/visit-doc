"""Classification metrics: did the turn find the labelled requests, in order, by intent.

Three numbers, because the strict one alone cannot say what failed: request-count
accuracy (the count was right), intent accuracy over aligned requests (the intents were
right where the count was), and exact segmentation match (both, at every position).

Only intents are compared. A segment's produced text and a label's gist are both
restatements, and neither is read here.
"""

from collections.abc import Sequence

from chat.domain.schemas import IntentLabel
from pydantic import BaseModel, ConfigDict

from golden_harness.cases import Case
from golden_harness.record import CaseRun, ExclusionReason
from golden_harness.scoring.alignment import AlignmentState, RunAlignment
from golden_harness.scoring.metric import Exclusions, Metric

REQUEST_COUNT_ACCURACY = "request_count_accuracy"
INTENT_ACCURACY = "intent_accuracy"
EXACT_SEGMENTATION_MATCH = "exact_segmentation_match"
CLASSIFICATION_METRICS = (
    REQUEST_COUNT_ACCURACY,
    INTENT_ACCURACY,
    EXACT_SEGMENTATION_MATCH,
)


class SegmentationDisagreement(BaseModel):
    """A case whose produced segmentation is not its labelled one, as intent lists."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    labelled: list[IntentLabel]
    produced: list[IntentLabel]


class ClassificationScores(BaseModel):
    """The classification metrics over a run, and the cases behind them.

    `unaligned_requests` is published beside intent accuracy, whose denominator leaves
    them out. `cap_bound_cases` lists the cases whose classifier said it combined
    requests to stay within the segment cap.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_count_accuracy: Metric
    intent_accuracy: Metric
    unaligned_requests: int
    exact_segmentation_match: Metric
    disagreements: list[SegmentationDisagreement]
    cap_bound_cases: list[str]


def score_classification(
    cases: Sequence[Case], alignment: RunAlignment, case_runs: Sequence[CaseRun]
) -> ClassificationScores:
    """Score a run's classification from its alignment.

    Args:
        cases: the labels `alignment` was taken against.
        case_runs: the recorded cases `alignment` was taken over.

    Raises: ValueError when an alignment has no recorded case or no label, or when a
        case no exclusion set aside produced `classification_failed` - which the
        record should have stored as a run error.
    """
    labels = {case.id: case for case in cases}
    runs = {run.case_id: run for run in case_runs}
    case_exclusions: list[ExclusionReason] = []
    request_exclusions: list[ExclusionReason] = []
    count_matches = exact_matches = scored_cases = 0
    aligned_requests = matching_intents = unaligned_requests = 0
    disagreements: list[SegmentationDisagreement] = []
    cap_bound: list[str] = []

    for case_alignment in alignment.alignments:
        case_run = runs.get(case_alignment.case_id)
        label = labels.get(case_alignment.case_id)
        if case_run is None or label is None:
            raise ValueError(
                f"{case_alignment.case_id} is aligned but not recorded and labelled"
            )
        if case_alignment.state is AlignmentState.EXCLUDED:
            reason = case_alignment.turn_exclusion
            assert reason is not None
            case_exclusions.append(reason)
            request_exclusions.extend([reason] * len(case_alignment.excluded_positions))
            continue

        segmentation = case_run.segments
        assert segmentation is not None
        produced = [segment.intent for segment in segmentation.segments]
        if IntentLabel.CLASSIFICATION_FAILED in produced:
            raise ValueError(
                f"{case_run.case_id} produced classification_failed but is not "
                "recorded as a run error"
            )
        scored_cases += 1
        if segmentation.cap_bound:
            cap_bound.append(case_run.case_id)

        if case_alignment.state is AlignmentState.UNALIGNED:
            unaligned_requests += len(case_alignment.unaligned_positions)
            disagreements.append(
                SegmentationDisagreement(
                    case_id=case_run.case_id,
                    labelled=[request.intent for request in label.requests],
                    produced=produced,
                )
            )
            continue

        count_matches += 1
        pairs = case_alignment.pairs
        matching = sum(1 for p in pairs if p.labelled.intent is p.produced.intent)
        aligned_requests += len(pairs)
        matching_intents += matching
        if matching == len(pairs):
            exact_matches += 1
            continue
        disagreements.append(
            SegmentationDisagreement(
                case_id=case_run.case_id,
                labelled=[p.labelled.intent for p in pairs],
                produced=produced,
            )
        )

    by_case = Exclusions.tally(case_exclusions)
    return ClassificationScores(
        request_count_accuracy=Metric(
            name=REQUEST_COUNT_ACCURACY,
            numerator=count_matches,
            denominator=scored_cases,
            excluded=by_case,
        ),
        intent_accuracy=Metric(
            name=INTENT_ACCURACY,
            numerator=matching_intents,
            denominator=aligned_requests,
            excluded=Exclusions.tally(request_exclusions),
        ),
        unaligned_requests=unaligned_requests,
        exact_segmentation_match=Metric(
            name=EXACT_SEGMENTATION_MATCH,
            numerator=exact_matches,
            denominator=scored_cases,
            excluded=by_case,
        ),
        disagreements=disagreements,
        cap_bound_cases=cap_bound,
    )
