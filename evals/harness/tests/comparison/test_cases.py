"""Case movements: one group per question a reader would ask, each derived, not judged.

Every movement here is read from what the scorers already publish - the retrieval
requests, the disagreements, the misses, the failures - or from the case record's own
`request_outcomes`, so a movement and the metric it sits under cannot disagree
(research R4). A direction is attached only where the label makes one available, and an
exclusion never gets one: a case that left a denominator behaved neither better nor
worse (FR-019, FR-021).
"""

from collections.abc import Callable, Sequence

from chat.domain.schemas import Citation, FaqVerdict
from golden_harness.cases import Case
from golden_harness.comparison.cases import case_movements
from golden_harness.comparison.model import (
    CaseMovement,
    MovementDirection,
    MovementGroup,
)
from golden_harness.record import CaseRun, ExclusionReason
from golden_harness.report import Report
from golden_harness.scoring.serving import (
    UNSERVED_ANSWERABLE_SHARE,
    WRONG_ABSTENTION_SHARE,
)

type Scored = Callable[..., Report]
type Records = Callable[[str], list[CaseRun]]


def _movements(
    scored: Scored,
    case_runs: Records,
    labels: Sequence[Case],
    base: str,
    new: str,
    *,
    only: list[str] | None = None,
) -> list[CaseMovement]:
    return case_movements(
        scored(base, only=only),
        scored(new, only=only),
        base_cases=[
            record
            for record in case_runs(base)
            if only is None or record.case_id in only
        ],
        new_cases=[
            record
            for record in case_runs(new)
            if only is None or record.case_id in only
        ],
        labels=labels,
    )


def _of_group(
    movements: Sequence[CaseMovement], group: MovementGroup
) -> list[CaseMovement]:
    return [movement for movement in movements if movement.group is group]


def test_a_run_compared_with_itself_produces_no_movement(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    assert _movements(scored, case_runs, labels, "base", "base") == []


def test_an_abstention_that_became_an_answer_is_improved_against_the_label(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    movements = _of_group(
        _movements(scored, case_runs, labels, "base", "verdict"), MovementGroup.VERDICT
    )

    assert len(movements) == 1
    moved = movements[0]
    assert (moved.case_id, moved.position) == ("G802", 0)
    assert (moved.base, moved.new) == ("abstained_rerank_floor", "answered")
    assert moved.direction is MovementDirection.IMPROVED
    assert moved.question == "What should I bring?"


def test_an_answer_that_became_an_abstention_is_degraded_against_the_label(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    (moved,) = _of_group(
        _movements(scored, case_runs, labels, "verdict", "base"), MovementGroup.VERDICT
    )

    assert moved.direction is MovementDirection.DEGRADED


def test_a_verdict_movement_names_the_metrics_it_changed_the_contribution_to(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    (moved,) = _of_group(
        _movements(scored, case_runs, labels, "base", "verdict"), MovementGroup.VERDICT
    )

    assert UNSERVED_ANSWERABLE_SHARE in moved.affects
    assert WRONG_ABSTENTION_SHARE in moved.affects


def test_an_abstention_that_moved_between_gates_has_no_direction(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    # The same outcome for the patient and the same miss for the metric: only the gate
    # differs, so neither `improved` nor `degraded` would be true (FR-019).
    base = scored("base")
    other_gate = _at_another_gate(case_runs("base"), "G802")

    movements = case_movements(
        base,
        base,
        base_cases=case_runs("base"),
        new_cases=other_gate,
        labels=labels,
    )

    (moved,) = _of_group(movements, MovementGroup.VERDICT)
    assert (moved.base, moved.new) == (
        "abstained_rerank_floor",
        "abstained_similarity_floor",
    )
    assert moved.direction is MovementDirection.DIRECTIONLESS


def _at_another_gate(records: list[CaseRun], case_id: str) -> list[CaseRun]:
    """Return the records with one case's abstention moved to another gate."""
    moved: list[CaseRun] = []
    for record in records:
        if record.case_id != case_id or record.assistant_message is None:
            moved.append(record)
            continue
        outcomes = record.assistant_message.request_outcomes or []
        reply = record.assistant_message.model_copy(
            update={
                "request_outcomes": [
                    outcome.model_copy(
                        update={"verdict": FaqVerdict.ABSTAINED_SIMILARITY_FLOOR}
                    )
                    for outcome in outcomes
                ]
            }
        )
        moved.append(record.model_copy(update={"assistant_message": reply}))
    return moved


def _without_outcomes(records: list[CaseRun], case_id: str) -> list[CaseRun]:
    """Return the records with one case's reply carrying no request outcomes."""
    stripped: list[CaseRun] = []
    for record in records:
        if record.case_id != case_id or record.assistant_message is None:
            stripped.append(record)
            continue
        reply = record.assistant_message.model_copy(update={"request_outcomes": None})
        stripped.append(record.model_copy(update={"assistant_message": reply}))
    return stripped


def test_a_request_that_stopped_producing_an_outcome_is_reported(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    # A request answered in one run and producing nothing in the other did move, and a
    # report that shows it in no group at all leaves a reader believing it held.
    base = scored("base")
    silent = _without_outcomes(case_runs("base"), "G801")

    movements = case_movements(
        base, base, base_cases=case_runs("base"), new_cases=silent, labels=labels
    )

    (moved,) = _of_group(movements, MovementGroup.VERDICT)
    assert (moved.case_id, moved.position) == ("G801", 0)
    assert moved.base == "answered"
    assert "no outcome" in moved.new
    assert moved.direction is MovementDirection.DIRECTIONLESS


def test_a_request_that_started_producing_an_outcome_is_reported_too(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    base = scored("base")
    silent = _without_outcomes(case_runs("base"), "G801")

    movements = case_movements(
        base, base, base_cases=silent, new_cases=case_runs("base"), labels=labels
    )

    (moved,) = _of_group(movements, MovementGroup.VERDICT)
    assert "no outcome" in moved.base
    assert moved.new == "answered"
    assert moved.direction is MovementDirection.DIRECTIONLESS


def test_a_cited_chunk_ranking_higher_is_an_improved_retrieval_rank(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    movements = _of_group(
        _movements(scored, case_runs, labels, "base", "rank"),
        MovementGroup.RETRIEVAL_RANK,
    )

    assert len(movements) == 1
    moved = movements[0]
    assert (moved.case_id, moved.position) == ("G802", 0)
    assert "2" in moved.base and "1" in moved.new
    assert moved.direction is MovementDirection.IMPROVED
    assert any(name.startswith("similarity") for name in moved.affects)


def test_a_cited_chunk_ranking_lower_is_a_degraded_retrieval_rank(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    (moved,) = _of_group(
        _movements(scored, case_runs, labels, "rank", "base"),
        MovementGroup.RETRIEVAL_RANK,
    )

    assert moved.direction is MovementDirection.DEGRADED


def test_a_request_that_left_the_scored_population_has_no_rank_direction(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    # Scored at one stage on one side and set aside on the other: a rank and an
    # exclusion are not two values of one scale.
    movements = _of_group(
        _movements(scored, case_runs, labels, "base", "excluded", only=None),
        MovementGroup.RETRIEVAL_RANK,
    )

    for moved in movements:
        assert moved.direction is MovementDirection.DIRECTIONLESS


def test_a_segmentation_moving_away_from_the_label_is_degraded(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    movements = _of_group(
        _movements(scored, case_runs, labels, "base", "segmentation"),
        MovementGroup.SEGMENTATION,
    )

    assert len(movements) == 1
    moved = movements[0]
    assert moved.case_id == "G803"
    assert moved.position is None
    assert "small_talk" in moved.base and "small_talk" not in moved.new
    assert moved.direction is MovementDirection.DEGRADED


def test_a_segmentation_moving_towards_the_label_is_improved(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    (moved,) = _of_group(
        _movements(scored, case_runs, labels, "segmentation", "base"),
        MovementGroup.SEGMENTATION,
    )

    assert moved.direction is MovementDirection.IMPROVED


def test_a_lost_tool_call_is_a_degraded_tool_selection(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    movements = _of_group(
        _movements(scored, case_runs, labels, "base", "tool"),
        MovementGroup.TOOL_SELECTION,
    )

    assert len(movements) == 1
    moved = movements[0]
    assert moved.case_id == "G804"
    assert "book_appointment" in moved.new
    assert moved.direction is MovementDirection.DEGRADED
    assert moved.affects == ["tool_selection_correctness"]


def test_a_regained_tool_call_is_an_improved_tool_selection(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    (moved,) = _of_group(
        _movements(scored, case_runs, labels, "tool", "base"),
        MovementGroup.TOOL_SELECTION,
    )

    assert moved.direction is MovementDirection.IMPROVED


def test_a_booking_that_stopped_landing_is_a_degraded_database_state(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    movements = _of_group(
        _movements(scored, case_runs, labels, "base", "poststate"),
        MovementGroup.DATABASE_STATE,
    )

    assert len(movements) == 1
    moved = movements[0]
    assert moved.case_id == "G804"
    assert moved.direction is MovementDirection.DEGRADED
    assert moved.affects == ["end_to_end_task_success"]


def test_a_booking_that_started_landing_is_an_improved_database_state(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    (moved,) = _of_group(
        _movements(scored, case_runs, labels, "poststate", "base"),
        MovementGroup.DATABASE_STATE,
    )

    assert moved.direction is MovementDirection.IMPROVED


def test_an_exclusion_movement_is_reported_with_both_states_and_no_direction(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    movements = _of_group(
        _movements(scored, case_runs, labels, "base", "excluded"),
        MovementGroup.EXCLUSION,
    )

    assert len(movements) == 1
    moved = movements[0]
    assert (moved.case_id, moved.base, moved.new) == ("G805", "scored", "run_error")
    assert moved.direction is MovementDirection.DIRECTIONLESS


def test_an_exclusion_lifted_is_directionless_too(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    (moved,) = _of_group(
        _movements(scored, case_runs, labels, "excluded", "base"),
        MovementGroup.EXCLUSION,
    )

    assert (moved.base, moved.new) == ("run_error", "scored")
    assert moved.direction is MovementDirection.DIRECTIONLESS


def test_an_exclusion_movement_names_the_metrics_whose_denominator_it_moved(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    (moved,) = _of_group(
        _movements(scored, case_runs, labels, "base", "excluded"),
        MovementGroup.EXCLUSION,
    )

    assert "similarity_hit_at_1" in moved.affects
    assert UNSERVED_ANSWERABLE_SHARE in moved.affects


def test_movements_are_ordered_by_case_then_position_then_group(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    movements = _movements(scored, case_runs, labels, "base", "excluded")

    keys = [
        (movement.case_id, movement.position or 0, movement.group.value)
        for movement in movements
    ]

    assert keys == sorted(keys)


def test_every_movement_of_an_unchanged_case_is_absent(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    movements = _movements(scored, case_runs, labels, "base", "verdict")

    assert {movement.case_id for movement in movements} == {"G802"}


def _also_abstained(records: list[CaseRun], case_id: str) -> list[CaseRun]:
    """Return the records with one case's answered request abstained instead."""
    changed: list[CaseRun] = []
    for record in records:
        if record.case_id != case_id or record.assistant_message is None:
            changed.append(record)
            continue
        outcomes = [
            outcome.model_copy(
                update={
                    "verdict": FaqVerdict.ABSTAINED_RERANK_FLOOR,
                    "answer": None,
                    "citations": [],
                }
            )
            for outcome in (record.assistant_message.request_outcomes or [])
        ]
        reply = record.assistant_message.model_copy(
            update={"request_outcomes": outcomes}
        )
        changed.append(record.model_copy(update={"assistant_message": reply}))
    return changed


def test_a_movement_at_a_request_claims_no_metric_computed_per_turn(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    # G803's turn is segmented differently *and* its first request abstains. The two
    # changes are two movements, and neither may claim the other's metrics: a request's
    # verdict cannot move a metric computed once per turn.
    base, changed = scored("base"), scored("segmentation")
    new_records = _also_abstained(case_runs("segmentation"), "G803")

    movements = _movements_of(
        base, changed, case_runs("base"), new_records, labels, "G803"
    )

    verdict = _one(movements, MovementGroup.VERDICT)
    segmentation = _one(movements, MovementGroup.SEGMENTATION)
    assert "exact_segmentation_match" not in verdict.affects
    assert "request_count_accuracy" not in verdict.affects
    assert "exact_segmentation_match" in segmentation.affects


def test_a_movement_about_the_turn_still_claims_its_requests_metrics(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    # The other direction: a turn-level movement asks about every item of its case,
    # since what it changed may well be a per-request contribution.
    base, changed = scored("base"), scored("segmentation")

    movements = _movements_of(
        base, changed, case_runs("base"), case_runs("segmentation"), labels, "G803"
    )

    segmentation = _one(movements, MovementGroup.SEGMENTATION)
    assert "intent_accuracy" in segmentation.affects


def _movements_of(
    base: Report,
    new: Report,
    base_records: list[CaseRun],
    new_records: list[CaseRun],
    labels: Sequence[Case],
    case_id: str,
) -> list[CaseMovement]:
    return [
        movement
        for movement in case_movements(
            base,
            new,
            base_cases=base_records,
            new_cases=new_records,
            labels=labels,
        )
        if movement.case_id == case_id
    ]


def _one(movements: Sequence[CaseMovement], group: MovementGroup) -> CaseMovement:
    (found,) = _of_group(movements, group)
    return found


def _excluded(records: list[CaseRun], case_id: str) -> list[CaseRun]:
    """Return the records with one case set aside by a case-scoped reason."""
    return [
        record.model_copy(update={"excluded": ExclusionReason.RUN_ERROR})
        if record.case_id == case_id
        else record
        for record in records
    ]


def _handed_off(records: list[CaseRun], case_id: str) -> list[CaseRun]:
    """Return the records with one case's turn handed off to a person."""
    return [
        record.model_copy(update={"excluded": ExclusionReason.HANDED_OFF_TURN})
        if record.case_id == case_id
        else record
        for record in records
    ]


def _answered(records: list[CaseRun], case_id: str) -> list[CaseRun]:
    """Return the records with one case's abstained request answered instead."""
    changed: list[CaseRun] = []
    for record in records:
        if record.case_id != case_id or record.assistant_message is None:
            changed.append(record)
            continue
        outcomes = [
            outcome.model_copy(
                update={
                    "verdict": FaqVerdict.ANSWERED,
                    "answer": "Yes, we do.",
                    "citations": [
                        Citation(entry_id=101, chunk_index=0, chunk_text="chunk of 101")
                    ],
                }
            )
            for outcome in (record.assistant_message.request_outcomes or [])
        ]
        reply = record.assistant_message.model_copy(
            update={"request_outcomes": outcomes}
        )
        changed.append(record.model_copy(update={"assistant_message": reply}))
    return changed


def test_a_booking_case_set_aside_is_not_read_as_every_labelled_tool_called(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    # G804 missed a tool in `tool` and errors out on the new side. The booking scorers
    # publish no miss for a case they set aside, and reading that silence as the clean
    # state would report an errored case as an improvement over one that missed a tool.
    base, new = scored("tool"), scored("base")
    new_records = _excluded(case_runs("base"), "G804")

    movements = _movements_of(base, new, case_runs("tool"), new_records, labels, "G804")

    tools = _one(movements, MovementGroup.TOOL_SELECTION)
    assert (tools.base, tools.new) == (
        "missing book_appointment",
        "not scored for booking",
    )
    assert tools.direction is MovementDirection.DIRECTIONLESS


def test_a_booking_case_set_aside_is_not_read_as_expected_appointments_found(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    base, new = scored("poststate"), scored("base")
    new_records = _excluded(case_runs("base"), "G804")

    movements = _movements_of(
        base, new, case_runs("poststate"), new_records, labels, "G804"
    )

    landed = _one(movements, MovementGroup.DATABASE_STATE)
    assert landed.new == "not scored for booking"
    assert landed.direction is MovementDirection.DIRECTIONLESS


def test_a_segmentation_movement_on_a_case_set_aside_carries_no_direction(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    # The classification scorers measure nothing for a case a case-scoped reason set
    # aside, so its segmentation neither agreed nor disagreed with the label.
    base, new = scored("base"), scored("segmentation")
    new_records = _excluded(case_runs("segmentation"), "G803")

    movements = _movements_of(base, new, case_runs("base"), new_records, labels, "G803")

    assert _one(movements, MovementGroup.SEGMENTATION).direction is (
        MovementDirection.DIRECTIONLESS
    )


def test_a_handed_off_turns_outcome_claims_no_serving_metric(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    # The serving scorer sets aside every case carrying a turn-level reason,
    # `handed_off_turn` included, so none of its outcomes counts towards the two shares
    # or the verdict distribution - and a movement of one may claim none of them.
    report = scored("base")
    base_records = _handed_off(case_runs("base"), "G803")
    new_records = _also_abstained(_handed_off(case_runs("base"), "G803"), "G803")

    movements = _movements_of(report, report, base_records, new_records, labels, "G803")

    claimed = {name for movement in movements for name in movement.affects}
    assert WRONG_ABSTENTION_SHARE not in claimed
    assert UNSERVED_ANSWERABLE_SHARE not in claimed
    assert not {name for name in claimed if name.startswith("verdict_distribution")}


def test_a_verdict_movement_on_a_labelled_gap_is_named_a_gap(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    # G806 is labelled `answerable: false`. Answering it is what the label warns
    # against, so the movement is a degradation - and calling it "labelled answerable"
    # would state the opposite of the label the direction came from.
    report = scored("base")
    new_records = _answered(case_runs("base"), "G806")

    movements = _movements_of(
        report, report, case_runs("base"), new_records, labels, "G806"
    )

    verdict = _one(movements, MovementGroup.VERDICT)
    assert verdict.labelled == "a gap"
    assert verdict.direction is MovementDirection.DEGRADED


def test_a_verdict_movement_on_an_answerable_request_is_named_answerable(
    scored: Scored, case_runs: Records, labels: list[Case]
) -> None:
    verdict = _one(
        _movements(scored, case_runs, labels, "base", "verdict"), MovementGroup.VERDICT
    )

    assert verdict.labelled == "answerable"
