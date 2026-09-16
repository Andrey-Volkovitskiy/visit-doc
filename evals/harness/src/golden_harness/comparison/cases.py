"""Case movements: what actually happened, grouped by the question it answers.

Nothing here re-implements a scoring rule. Each group reads both states from what the
scorers already publish - the retrieval requests with their per-stage ranks, the
segmentation disagreements, the tool-selection misses, the task failures - or from the
case record's own `request_outcomes`, which 1h created as the authority on what a
request got (research R4). A movement and the metric it sits under therefore cannot
disagree.

`affects` is derived the same way and in one place: a metric's *contribution* is the
state of each item it was computed over, and a movement affects the metrics whose state
at its own item changed. So FR-017 is answerable in both directions from one list - a
movement knows its metrics, and a metric's movements are those naming it - rather than
from two lists that can drift apart.

Direction is attached only where the label makes one available. An abstention that
moved from one gate to another is the same outcome for the patient and the same miss
for the metric, and a case that became excluded has left a denominator rather than
behaved worse: both are `directionless`, which is a value here and not an omission
(FR-019, FR-021).
"""

from collections.abc import Mapping, Sequence
from typing import Final

from chat.domain.schemas import FaqVerdict, IntentLabel, RequestOutcome

from golden_harness.cases import Case
from golden_harness.comparison.model import (
    CaseMovement,
    LabelledExpectation,
    MovementDirection,
    MovementGroup,
)
from golden_harness.record import CaseRun
from golden_harness.report import Report
from golden_harness.scoring.metric import CASE_SCOPED_REASONS
from golden_harness.scoring.retrieval import (
    RERANK_HIT_AT_1,
    RERANK_HIT_AT_3,
    RERANK_HIT_AT_5,
    RERANK_MRR,
    SIMILARITY_GATE_SURVIVAL,
    SIMILARITY_HIT_AT_1,
    SIMILARITY_HIT_AT_3,
    SIMILARITY_HIT_AT_5,
    SIMILARITY_MRR,
    RetrievalRequest,
    Stage,
    StageResult,
)
from golden_harness.scoring.serving import (
    UNSERVED_ANSWERABLE_SHARE,
    VERDICT_DISTRIBUTION,
    WRONG_ABSTENTION_SHARE,
)

# One item of a metric's population: a case, and a request position where the metric is
# per request.
type Item = tuple[str, int | None]
# What one metric made of each item it was computed over.
type Contribution = dict[Item, str]

_SCORED: Final = "scored"
_HIT_AT: Final = {
    Stage.SIMILARITY: (
        (SIMILARITY_HIT_AT_1, 1),
        (SIMILARITY_HIT_AT_3, 3),
        (SIMILARITY_HIT_AT_5, 5),
    ),
    Stage.RERANK: ((RERANK_HIT_AT_1, 1), (RERANK_HIT_AT_3, 3), (RERANK_HIT_AT_5, 5)),
}
_MRR: Final = {Stage.SIMILARITY: SIMILARITY_MRR, Stage.RERANK: RERANK_MRR}


def case_movements(
    base: Report,
    new: Report,
    *,
    base_cases: Sequence[CaseRun],
    new_cases: Sequence[CaseRun],
    labels: Sequence[Case],
    varying: Sequence[Item] = (),
) -> list[CaseMovement]:
    """Return every movement between the two runs, ordered by case, position, group.

    Args:
        base_cases / new_cases: the case records the two reports were scored over.
        labels: the labels both runs were scored against; a direction is taken from
            the labelled request where there is one.
        varying: the items a band saw vary on an unchanged build, marked wherever this
            comparison reports them as moved (FR-035).

    An unchanged case produces nothing: what appears under a group is what moved.
    """
    by_label = {case.id: case for case in labels}
    on_base = {record.case_id: record for record in base_cases}
    on_new = {record.case_id: record for record in new_cases}
    common = [case_id for case_id in on_base if case_id in on_new]

    contributions = (
        _contributions(base, base_cases, labels),
        _contributions(new, new_cases, labels),
    )
    movements: list[CaseMovement] = []
    for case_id in common:
        label = by_label.get(case_id)
        if label is None:
            continue
        movements.extend(
            _verdict_movements(case_id, label, on_base[case_id], on_new[case_id])
        )
        movements.extend(_rank_movements(case_id, base, new))
        movements.extend(
            _segmentation_movements(case_id, label, on_base[case_id], on_new[case_id])
        )
        movements.extend(
            _tool_movements(label, on_base[case_id], on_new[case_id], base, new)
        )
        movements.extend(
            _database_movements(label, on_base[case_id], on_new[case_id], base, new)
        )
        movements.extend(
            _exclusion_movements(case_id, on_base[case_id], on_new[case_id])
        )

    varying_items = set(varying)
    return sorted(
        (
            movement.model_copy(
                update={
                    "affects": _affects(*contributions, movement),
                    "varies_on_its_own": (movement.case_id, movement.position)
                    in varying_items,
                }
            )
            for movement in movements
        ),
        key=lambda movement: (
            movement.case_id,
            movement.position or 0,
            movement.group.value,
            movement.base,
        ),
    )


def _movement(
    case_id: str,
    position: int | None,
    group: MovementGroup,
    base: str,
    new: str,
    direction: MovementDirection,
    question: str | None = None,
    labelled: LabelledExpectation | None = None,
) -> CaseMovement:
    """Build a movement with its `affects` left to be filled in by one place."""
    return CaseMovement(
        case_id=case_id,
        position=position,
        group=group,
        base=base,
        new=new,
        direction=direction,
        question=question,
        labelled=labelled,
        affects=[],
    )


def _verdict_movements(
    case_id: str, label: Case, base: CaseRun, new: CaseRun
) -> list[CaseMovement]:
    """A request whose verdict differs, directed against what the label asked for.

    A request that produced an outcome in one run and none in the other is reported
    here too, as a movement to or from `_NO_OUTCOME`. Its turn took a different shape
    rather than reaching a different verdict, so the segmentation or exclusion group
    usually says why - but it is a real difference in what the patient got, and leaving
    it to another group to happen to cover would let a request answered in one run and
    silent in the other appear in no group at all.
    """
    was, is_now = _outcomes(base), _outcomes(new)
    movements: list[CaseMovement] = []
    for position in sorted(set(was) | set(is_now)):
        before, after = was.get(position), is_now.get(position)
        if before is not None and after is not None and before.verdict is after.verdict:
            continue
        movements.append(
            _movement(
                case_id,
                position,
                MovementGroup.VERDICT,
                _verdict_state(before),
                _verdict_state(after),
                _verdict_direction(label, position, before, after),
                question=_question(after) or _question(before),
                labelled=_labelled_expectation(_labelled_answerable(label, position)),
            )
        )
    return movements


# What a request that produced nothing is reported as. Not a verdict: the FAQ half did
# not reach one, which is a different fact from any of the five it could have reached.
_NO_OUTCOME: Final = "no outcome recorded"


def _verdict_state(outcome: RequestOutcome | None) -> str:
    """Render one side of a verdict movement: the verdict, or that there was none."""
    return outcome.verdict.value if outcome is not None else _NO_OUTCOME


def _question(outcome: RequestOutcome | None) -> str | None:
    """Return a request's question, or None where that side recorded no outcome.

    The new side is asked first and the baseline second, so a request that stopped
    producing an outcome still arrives with its text attached.
    """
    return outcome.question if outcome is not None else None


def _outcomes(record: CaseRun) -> dict[int, RequestOutcome]:
    """Return the run's stored outcomes for a case, by request position."""
    reply = record.assistant_message
    outcomes = reply.request_outcomes if reply is not None else None
    return {outcome.position: outcome for outcome in outcomes or ()}


def _verdict_direction(
    label: Case,
    position: int,
    before: RequestOutcome | None,
    after: RequestOutcome | None,
) -> MovementDirection:
    """Direct a verdict movement by what the label says the request is.

    An abstention that moved between gates is the same outcome for the patient, and a
    request the label does not describe as an answerable question or a gap gives
    nothing to direct against: both are directionless. So is a request that produced no
    outcome on one side - what the label asks of an answer says nothing about a turn
    that reached no verdict at all, and the group that explains the shape it took is
    where its direction, if any, belongs.
    """
    if before is None or after is None:
        return MovementDirection.DIRECTIONLESS
    if before.verdict.answered == after.verdict.answered:
        return MovementDirection.DIRECTIONLESS
    answerable = _labelled_answerable(label, position)
    if answerable is None:
        return MovementDirection.DIRECTIONLESS
    # Answering is what the label asks for on an answerable request and what it warns
    # against on a gap, so the same move is an improvement in one and a degradation in
    # the other.
    better = after.verdict.answered if answerable else before.verdict.answered
    return MovementDirection.IMPROVED if better else MovementDirection.DEGRADED


def _labelled_answerable(label: Case, position: int) -> bool | None:
    """Whether the label asks this request to be answered, or None where it asks not.

    None covers a position the label does not have and a request the label does not
    describe as a `faq_question` the corpus can or cannot answer - neither gives
    anything to direct a verdict movement against, or to name one by.
    """
    if not 0 <= position < len(label.requests):
        return None
    request = label.requests[position]
    if request.intent is not IntentLabel.FAQ_QUESTION:
        return None
    return request.answerable


def _labelled_expectation(answerable: bool | None) -> LabelledExpectation | None:
    """Name what the label asks of a request, in the words the report prints.

    A gap says so: the renderer used to call every directed verdict movement
    "labelled answerable", which states the opposite of what a gap's label says and
    contradicts the direction the same label produced.
    """
    if answerable is None:
        return None
    return "answerable" if answerable else "a gap"


def _rank_movements(case_id: str, base: Report, new: Report) -> list[CaseMovement]:
    """A request whose cited chunk changed place at a stage."""
    was = {request.position: request for request in _requests(base, case_id)}
    is_now = {request.position: request for request in _requests(new, case_id)}
    movements: list[CaseMovement] = []
    for position in sorted(set(was) | set(is_now)):
        for stage in Stage:
            before = _stage_result(was.get(position), stage)
            after = _stage_result(is_now.get(position), stage)
            if _stage_state(stage, before) == _stage_state(stage, after):
                continue
            movements.append(
                _movement(
                    case_id,
                    position,
                    MovementGroup.RETRIEVAL_RANK,
                    _stage_state(stage, before),
                    _stage_state(stage, after),
                    _rank_direction(before, after),
                )
            )
    return movements


def _requests(report: Report, case_id: str) -> list[RetrievalRequest]:
    """Return the scored retrieval requests of one case."""
    return [
        request for request in report.retrieval.requests if request.case_id == case_id
    ]


def _stage_result(request: RetrievalRequest | None, stage: Stage) -> StageResult | None:
    """Return one stage's result for a request, or None where the run has none."""
    if request is None:
        return None
    return request.similarity if stage is Stage.SIMILARITY else request.rerank


def _stage_state(stage: Stage, result: StageResult | None) -> str:
    """Render one stage's outcome for a request as the text a reader compares."""
    if result is None:
        return f"{stage.value} not scored"
    if result.excluded is not None:
        return f"{stage.value} set aside: {result.excluded.value}"
    if result.rank is None:
        return f"{stage.value} rank none"
    return f"{stage.value} rank {result.rank}"


def _rank_direction(
    before: StageResult | None, after: StageResult | None
) -> MovementDirection:
    """Direct a rank movement: a lower rank is better, and leaving is neither."""
    if before is None or after is None:
        return MovementDirection.DIRECTIONLESS
    if (before.excluded is None) != (after.excluded is None):
        # Scored on one side and set aside on the other: a rank and an exclusion are
        # not two values of one scale.
        return MovementDirection.DIRECTIONLESS
    if before.excluded is not None:
        return MovementDirection.DIRECTIONLESS
    # A request whose cited chunk is in neither list has no rank; losing it is worse
    # than any rank, and finding it better than none.
    was = before.rank if before.rank is not None else _UNRANKED
    is_now = after.rank if after.rank is not None else _UNRANKED
    if was == is_now:
        return MovementDirection.DIRECTIONLESS
    return MovementDirection.IMPROVED if is_now < was else MovementDirection.DEGRADED


# Worse than any real rank, and only ever compared against one.
_UNRANKED: Final = float("inf")


def _segmentation_movements(
    case_id: str, label: Case, base: CaseRun, new: CaseRun
) -> list[CaseMovement]:
    """A turn segmented differently, directed by its agreement with the label.

    A case a case-scoped reason set aside on either side is reported without a
    direction: the classification scorers measured nothing for it, so "not classified"
    is an absence of a measurement rather than a disagreement with the label.
    """
    was, is_now = _intents(base), _intents(new)
    if was == is_now:
        return []
    labelled = [request.intent.value for request in label.requests]
    direction = (
        MovementDirection.DIRECTIONLESS
        if _set_aside(base) or _set_aside(new)
        else _agreement_direction(was == labelled, is_now == labelled)
    )
    return [
        _movement(
            case_id,
            None,
            MovementGroup.SEGMENTATION,
            ", ".join(was) if was else "not classified",
            ", ".join(is_now) if is_now else "not classified",
            direction,
        )
    ]


def _set_aside(record: CaseRun) -> bool:
    """Whether a case-scoped reason kept this record out of every metric."""
    return record.excluded in CASE_SCOPED_REASONS


def _intents(record: CaseRun) -> list[str]:
    """Return the intents the classifier produced for a case, in order."""
    if record.segments is None:
        return []
    return [segment.intent.value for segment in record.segments.segments]


def _agreement_direction(before: bool, after: bool) -> MovementDirection:
    """Moving to agreement with the label is better, away from it worse."""
    if before == after:
        return MovementDirection.DIRECTIONLESS
    return MovementDirection.IMPROVED if after else MovementDirection.DEGRADED


def _tool_movements(
    label: Case, base_record: CaseRun, new_record: CaseRun, base: Report, new: Report
) -> list[CaseMovement]:
    """A booking half that called a different set of the tools its label requires."""
    if not label.has_booking_request:
        # Neither side was in the booking population, so there is nothing to compare -
        # and asking the report for a miss it cannot hold would scan its list per case.
        return []
    case_id = label.id
    was = _booking_state(base_record, _tool_state(base, case_id))
    is_now = _booking_state(new_record, _tool_state(new, case_id))
    if was == is_now:
        return []
    return [
        _movement(
            case_id,
            None,
            MovementGroup.TOOL_SELECTION,
            was,
            is_now,
            _shortfall_direction(was, is_now, clean=_ALL_TOOLS_CALLED),
        )
    ]


_ALL_TOOLS_CALLED: Final = "every labelled tool called"
_BOOKING_LANDED: Final = "expected appointments found"
# What a case the booking scorers never scored is reported as. Not one of the two above:
# neither was observed, and reading the absence of a published miss as "every labelled
# tool called" would report a case that errored out as an improvement over one that
# merely missed a tool.
_NOT_SCORED_FOR_BOOKING: Final = "not scored for booking"


def _booking_state(record: CaseRun, scored: str) -> str:
    """Return a booking case's state, or that the booking scorers measured nothing.

    Only reached for a case whose label carries a booking request; the other half of
    `score_booking`'s population is `_set_aside`, which is what this reads.
    """
    if _set_aside(record):
        return _NOT_SCORED_FOR_BOOKING
    return scored


def _tool_state(report: Report, case_id: str) -> str:
    """Render what a case's booking half missed, from the published misses."""
    misses = [
        miss for miss in report.booking.tool_selection_misses if miss.case_id == case_id
    ]
    if not misses:
        return _ALL_TOOLS_CALLED
    missing = sorted(tool.value for miss in misses for tool in miss.missing)
    return f"missing {', '.join(missing)}"


def _database_movements(
    label: Case, base_record: CaseRun, new_record: CaseRun, base: Report, new: Report
) -> list[CaseMovement]:
    """A booking case whose appointments stopped - or started - matching its fixture."""
    if not label.has_booking_request:
        return []
    case_id = label.id
    was = _booking_state(base_record, _database_state(base, case_id))
    is_now = _booking_state(new_record, _database_state(new, case_id))
    if was == is_now:
        return []
    return [
        _movement(
            case_id,
            None,
            MovementGroup.DATABASE_STATE,
            was,
            is_now,
            _shortfall_direction(was, is_now, clean=_BOOKING_LANDED),
        )
    ]


def _database_state(report: Report, case_id: str) -> str:
    """Render a case's post-state outcome, from the published failures."""
    failures = [
        failure
        for failure in report.booking.task_failures
        if failure.case_id == case_id
    ]
    if not failures:
        return _BOOKING_LANDED
    reads = ", ".join(sorted(failure.read.value for failure in failures))
    return f"did not match at {reads}"


def _shortfall_direction(before: str, after: str, *, clean: str) -> MovementDirection:
    """Losing a shortfall is better, gaining one worse, swapping or leaving neither."""
    if _NOT_SCORED_FOR_BOOKING in (before, after):
        # Entering or leaving the scored population is not a shortfall gained or lost.
        return MovementDirection.DIRECTIONLESS
    if before == clean:
        return MovementDirection.DEGRADED
    if after == clean:
        return MovementDirection.IMPROVED
    return MovementDirection.DIRECTIONLESS


def _exclusion_movements(
    case_id: str, base: CaseRun, new: CaseRun
) -> list[CaseMovement]:
    """A case set aside in one run and not the other - always without a direction."""
    was = base.excluded.value if base.excluded is not None else _SCORED
    is_now = new.excluded.value if new.excluded is not None else _SCORED
    if was == is_now:
        return []
    return [
        _movement(
            case_id,
            None,
            MovementGroup.EXCLUSION,
            was,
            is_now,
            MovementDirection.DIRECTIONLESS,
        )
    ]


def _affects(
    base: dict[str, Contribution], new: dict[str, Contribution], movement: CaseMovement
) -> list[str]:
    """Name the metrics whose contribution at this movement's item changed.

    A movement with a position asks about that request alone - including nothing
    computed once per turn, which a request's verdict or rank cannot move. A movement
    about the turn - a segmentation, a booking, an exclusion - asks about every item of
    the case, since what it changed may well be a per-request contribution.

    Nothing is lost by the narrowing: every per-turn contribution that changed has a
    per-turn movement of its own to claim it, because the four turn-level groups are
    the only things a per-turn metric is computed from.
    """
    changed: list[str] = []
    for name in base.keys() | new.keys():
        on_base, on_new = base.get(name, {}), new.get(name, {})
        items = {
            item
            for item in on_base.keys() | on_new.keys()
            if item[0] == movement.case_id
            and (movement.position is None or item[1] == movement.position)
        }
        if any(on_base.get(item) != on_new.get(item) for item in items):
            changed.append(name)
    return sorted(changed)


def _contributions(
    report: Report, case_runs: Sequence[CaseRun], labels: Sequence[Case]
) -> dict[str, Contribution]:
    """Return, per metric, what the run made of each item the metric was computed over.

    Every state is read from the report's own published lists against the population
    the scorers computed over, so nothing here decides whether an item counted - it
    reads back what the scorer decided (research R4).
    """
    by_label = {case.id: case for case in labels}
    contributions: dict[str, Contribution] = {}
    contributions.update(_classification_contributions(report, case_runs, by_label))
    contributions.update(_retrieval_contributions(report))
    contributions.update(_serving_contributions(report, case_runs, by_label))
    contributions.update(_booking_contributions(report, case_runs, by_label))
    return contributions


def _scored_cases(case_runs: Sequence[CaseRun]) -> list[CaseRun]:
    """Return the records no case-scoped reason set aside."""
    return [record for record in case_runs if not _set_aside(record)]


def _classification_contributions(
    report: Report, case_runs: Sequence[CaseRun], labels: Mapping[str, Case]
) -> dict[str, Contribution]:
    """What each scored case contributed to the three classification metrics."""
    disagreements = {
        disagreement.case_id: disagreement
        for disagreement in report.classification.disagreements
    }
    counts: Contribution = {}
    exact: Contribution = {}
    intents: Contribution = {}
    for record in _scored_cases(case_runs):
        case_id = record.case_id
        label = labels.get(case_id)
        if label is None:
            continue
        produced = _intents(record)
        counts[(case_id, None)] = (
            "counted right" if len(produced) == len(label.requests) else "counted wrong"
        )
        exact[(case_id, None)] = (
            "matched" if case_id not in disagreements else "did not match"
        )
        if len(produced) != len(label.requests):
            continue
        for position, (labelled, was) in enumerate(
            zip(label.requests, produced, strict=True)
        ):
            intents[(case_id, position)] = (
                "matched" if labelled.intent.value == was else f"produced {was}"
            )
    return {
        "request_count_accuracy": counts,
        "exact_segmentation_match": exact,
        "intent_accuracy": intents,
    }


def _retrieval_contributions(report: Report) -> dict[str, Contribution]:
    """What each scored request contributed to each retrieval metric, per stage."""
    contributions: dict[str, Contribution] = {
        name: {} for name, _ in (*_HIT_AT[Stage.SIMILARITY], *_HIT_AT[Stage.RERANK])
    }
    contributions[SIMILARITY_MRR] = {}
    contributions[RERANK_MRR] = {}
    contributions[SIMILARITY_GATE_SURVIVAL] = {}
    for request in report.retrieval.requests:
        item: Item = (request.case_id, request.position)
        for stage in Stage:
            result = _stage_result(request, stage)
            assert result is not None
            for name, k in _HIT_AT[stage]:
                contributions[name][item] = _hit_state(result, k)
            contributions[_MRR[stage]][item] = _stage_state(stage, result)
        contributions[SIMILARITY_GATE_SURVIVAL][item] = (
            "survived"
            if request.survived_similarity_gate
            else "did not survive"
            if request.survived_similarity_gate is False
            else f"set aside: {request.similarity.excluded}"
        )
    return contributions


def _hit_state(result: StageResult, k: int) -> str:
    """Whether a stage put a cited chunk in the top `k`, or set the request aside."""
    if result.excluded is not None:
        return f"set aside: {result.excluded.value}"
    return "hit" if result.rank is not None and result.rank <= k else "miss"


def _serving_contributions(
    report: Report, case_runs: Sequence[CaseRun], labels: Mapping[str, Case]
) -> dict[str, Contribution]:
    """What each request contributed to the two shares and the verdict distribution."""
    unserved: Contribution = {
        (request.case_id, request.position): f"unserved: {request.cause.value}"
        for request in report.serving.unserved
    }
    wrong = {
        (abstention.case_id, abstention.position)
        for abstention in report.serving.wrong_abstentions
    }
    first: Contribution = {}
    second: Contribution = {}
    distribution: dict[str, Contribution] = {
        f"{VERDICT_DISTRIBUTION}.{verdict.value}": {} for verdict in FaqVerdict
    }
    for record in case_runs:
        case_id = record.case_id
        label = labels.get(case_id)
        # The serving scorer sets a case aside from all three metrics as soon as it
        # carries any turn-level reason - `handed_off_turn`, which is not case-scoped,
        # included - so this population is narrower than `_scored_cases`. Counting such
        # a case's outcomes here would name metrics its movement did not change, since
        # none of them counted it.
        if label is None or record.excluded is not None:
            continue
        for position, request in enumerate(label.requests):
            if not request.is_answerable_faq:
                continue
            item: Item = (case_id, position)
            first[item] = unserved.get(item, "served")
        for position, outcome in _outcomes(record).items():
            item = (case_id, position)
            if not outcome.verdict.answered:
                second[item] = (
                    "wrong abstention" if item in wrong else "right abstention"
                )
            distribution[f"{VERDICT_DISTRIBUTION}.{outcome.verdict.value}"][item] = (
                "counted"
            )
    return {
        UNSERVED_ANSWERABLE_SHARE: first,
        WRONG_ABSTENTION_SHARE: second,
        **distribution,
    }


def _booking_contributions(
    report: Report, case_runs: Sequence[CaseRun], labels: Mapping[str, Case]
) -> dict[str, Contribution]:
    """What each booking case contributed to the two booking metrics."""
    tools: Contribution = {}
    landed: Contribution = {}
    for record in _scored_cases(case_runs):
        label = labels.get(record.case_id)
        if label is None or not label.has_booking_request:
            continue
        tools[(record.case_id, None)] = _tool_state(report, record.case_id)
        landed[(record.case_id, None)] = _database_state(report, record.case_id)
    return {
        "tool_selection_correctness": tools,
        "end_to_end_task_success": landed,
    }
