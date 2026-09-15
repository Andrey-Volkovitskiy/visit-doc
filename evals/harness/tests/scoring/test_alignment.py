"""Alignment: every labelled request aligned, unaligned or excluded, exactly once."""

from typing import Any

import pytest
from golden_harness.cases import Case
from golden_harness.record import CaseRun, ExclusionReason
from golden_harness.scoring.alignment import (
    Alignment,
    AlignmentState,
    AlignmentTotals,
    ConservationError,
    align_case,
    align_run,
)

_INTENT_FIELDS: dict[str, dict[str, Any]] = {
    "faq_question": {"answerable": True, "cites": ["what-to-bring"]},
    "booking": {"tools": ["book_appointment"]},
}


def _case(case_id: str, *intents: str) -> Case:
    return Case.model_validate(
        {
            "id": case_id,
            "family": "test",
            "message": f"message of {case_id}",
            "requests": [
                {"intent": intent, "gist": "g", **_INTENT_FIELDS.get(intent, {})}
                for intent in intents
            ],
            "source": "new",
        }
    )


def _run(
    case_id: str, *produced: str, excluded: str | None = None, classified: bool = True
) -> CaseRun:
    raw: dict[str, Any] = {
        "case_id": case_id,
        "chat_id": f"chat-{case_id}",
        "attempts": 1,
        "elapsed_seconds": 1.0,
        "excluded": excluded,
    }
    if classified:
        raw["segments"] = {
            "segments": [
                {"position": i, "intent": intent, "text": f"request {i}"}
                for i, intent in enumerate(produced)
            ],
            "cap_bound": False,
        }
    return CaseRun.model_validate(raw)


def test_equal_counts_align_by_position() -> None:
    case = _case("G001", "faq_question", "booking")

    alignment = align_case(case, _run("G001", "booking", "faq_question"))

    assert alignment.state is AlignmentState.ALIGNED
    assert [pair.position for pair in alignment.pairs] == [0, 1]
    assert [pair.labelled.intent.value for pair in alignment.pairs] == [
        "faq_question",
        "booking",
    ]
    # Position, not intent, decides the pairing: a swapped order pairs across intents.
    assert [pair.produced.intent.value for pair in alignment.pairs] == [
        "booking",
        "faq_question",
    ]
    assert alignment.unaligned_positions == []
    assert alignment.excluded_positions == []


@pytest.mark.parametrize(
    "produced", [("faq_question",), ("faq_question", "booking", "small_talk")]
)
def test_unequal_counts_leave_every_labelled_request_unaligned(
    produced: tuple[str, ...],
) -> None:
    case = _case("G002", "faq_question", "booking")

    alignment = align_case(case, _run("G002", *produced))

    assert alignment.state is AlignmentState.UNALIGNED
    assert alignment.pairs == []
    assert alignment.unaligned_positions == [0, 1]
    assert alignment.produced_count == len(produced)


@pytest.mark.parametrize(
    "reason",
    [
        "run_error",
        "silenced_turn",
        "cancelled_turn",
        "missing_log_slice",
        "unresolvable_fixture",
        "outcome_unknown",
    ],
)
def test_a_case_scoped_reason_excludes_the_case_and_it_is_never_aligned(
    reason: str,
) -> None:
    case = _case("G003", "faq_question", "booking")

    # Counts that would align, so only the exclusion can keep it from aligning.
    alignment = align_case(
        case, _run("G003", "faq_question", "booking", excluded=reason)
    )

    assert alignment.state is AlignmentState.EXCLUDED
    assert alignment.pairs == []
    assert alignment.excluded_positions == [0, 1]
    assert alignment.turn_exclusion is ExclusionReason(reason)


def test_an_excluded_case_needs_no_produced_segmentation() -> None:
    case = _case("G004", "small_talk")

    alignment = align_case(
        case, _run("G004", excluded="silenced_turn", classified=False)
    )

    assert alignment.state is AlignmentState.EXCLUDED


def test_a_handed_off_turn_still_aligns_and_carries_its_reason() -> None:
    case = _case("G005", "faq_question", "urgent_condition")

    alignment = align_case(
        case,
        _run("G005", "faq_question", "urgent_condition", excluded="handed_off_turn"),
    )

    assert alignment.state is AlignmentState.ALIGNED
    assert len(alignment.pairs) == 2
    assert alignment.turn_exclusion is ExclusionReason.HANDED_OFF_TURN


def test_a_handed_off_turn_with_unequal_counts_is_unaligned() -> None:
    case = _case("G006", "faq_question", "call_staff")

    alignment = align_case(case, _run("G006", "call_staff", excluded="handed_off_turn"))

    assert alignment.state is AlignmentState.UNALIGNED


def test_an_unexcluded_case_without_a_produced_segmentation_is_refused() -> None:
    with pytest.raises(ValueError, match="G007"):
        align_case(_case("G007", "small_talk"), _run("G007", classified=False))


def test_a_record_of_another_case_is_refused() -> None:
    with pytest.raises(ValueError, match="G008"):
        align_case(_case("G008", "small_talk"), _run("G009", "small_talk"))


def _mixed_run() -> tuple[list[Case], list[CaseRun]]:
    cases = [
        _case("G010", "faq_question", "booking"),
        _case("G011", "faq_question", "faq_question"),
        _case("G012", "booking"),
        _case("G013", "faq_question", "urgent_condition", "small_talk"),
        _case("G014", "small_talk"),
    ]
    runs = [
        _run("G010", "faq_question", "booking"),
        _run("G011", "faq_question"),
        _run("G012", excluded="run_error", classified=False),
        _run(
            "G013",
            "faq_question",
            "urgent_condition",
            "small_talk",
            excluded="handed_off_turn",
        ),
        _run("G014", "small_talk"),
    ]
    return cases, runs


def test_totals_over_a_run_sum_to_its_labelled_total() -> None:
    cases, runs = _mixed_run()

    result = align_run(cases, runs)

    assert result.totals == AlignmentTotals(
        aligned=6, unaligned=2, excluded=1, labelled=9
    )
    assert (
        result.totals.aligned + result.totals.unaligned + result.totals.excluded
        == result.totals.labelled
        == sum(len(case.requests) for case in cases)
    )
    assert [a.case_id for a in result.alignments] == [r.case_id for r in runs]


def test_a_run_is_totalled_over_its_recorded_cases_only() -> None:
    cases, runs = _mixed_run()

    result = align_run(cases, runs[:2])

    assert result.totals.labelled == 4


def test_a_recorded_case_with_no_label_is_refused() -> None:
    cases, runs = _mixed_run()

    with pytest.raises(ValueError, match="G099"):
        align_run(cases, [*runs, _run("G099", "small_talk")])


def test_a_case_recorded_twice_is_refused() -> None:
    cases, runs = _mixed_run()

    with pytest.raises(ValueError, match="G010"):
        align_run(cases, [*runs, runs[0]])


def _tampered(alignment: Alignment, **fields: Any) -> Alignment:
    return Alignment.model_construct(**{**dict(alignment), **fields})


def test_a_request_in_no_state_breaks_conservation() -> None:
    cases, runs = _mixed_run()
    alignments = [align_case(c, r) for c, r in zip(cases, runs, strict=True)]
    alignments[1] = _tampered(alignments[1], unaligned_positions=[0])

    with pytest.raises(ConservationError, match="G011"):
        AlignmentTotals.of(alignments, cases)


def test_a_request_in_two_states_breaks_conservation() -> None:
    cases, runs = _mixed_run()
    alignments = [align_case(c, r) for c, r in zip(cases, runs, strict=True)]
    alignments[0] = _tampered(alignments[0], unaligned_positions=[1])

    with pytest.raises(ConservationError, match="G010"):
        AlignmentTotals.of(alignments, cases)


def test_a_missing_and_a_doubled_request_cannot_cancel_out() -> None:
    # Right total, wrong accounting: position 0 twice and position 1 never.
    cases, runs = _mixed_run()
    alignments = [align_case(c, r) for c, r in zip(cases, runs, strict=True)]
    alignments[1] = _tampered(alignments[1], unaligned_positions=[0, 0])

    with pytest.raises(ConservationError, match="G011"):
        AlignmentTotals.of(alignments, cases)
