"""Booking metrics over the hand-written `fixtures/runs/us3` run.

Expected values, counted by hand from the fixture's case files and `labels.json`, on the
run clock Monday 2026-03-02 08:00:

    case  labelled       tools called                  D1    post-state         D2
    G941  book           check_availability, book      hit   Fri 14:00 (any)    match
    G942  cancel         list_my_appointments, cancel  hit   Tue 10:00 cxl      match
    G943  list_pract.    list_practitioners, book      hit   Tue 09:00 extra    fail
    G944  book           check_availability, book x2   hit   Wed 10:00, 11:00   match
    G945  cancel + book  check_availability, book      miss  Fri kept, Mon      fail
    G946  cancel         - (unresolvable_fixture)      excl  -                  excl
    G947  reschedule     - (outcome_unknown)           excl  stored, unscored   excl
    G948  small_talk     - (no booking, no fixture)    -     -                  -

The last three carry a scripted reply, so each is read twice - after its first turn,
where the planted appointments must still stand, and after the reply - and its tools
are the union of both turns' calls:

    case  labelled  calls: first turn / reply      D1   first read    last read  D2
    G949  cancel    list_my / list_my, cancel      hit  Thu standing  Thu cxl    match
    G950  cancel    list_my, cancel / -            hit  Fri cxl       Fri cxl    fail
    G951  book      check_avail. / book, check_av. hit  none          none       fail

    G950 fails on its first read: it cancelled before the patient confirmed. G951 fails
    on its last: book_appointment was called in the reply and refused.

    D1 tool_selection_correctness: 7 / 8
    D2 end_to_end_task_success:    4 / 8
    both excluding unresolvable_fixture 1 and outcome_unknown 1
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from golden_harness.cases import AppointmentRef, BookingTool, Case, load_cases
from golden_harness.record import (
    AppointmentState,
    CaseRun,
    ExclusionReason,
    read_case,
    read_run,
    recorded_case_ids,
)
from golden_harness.scoring.alignment import align_run
from golden_harness.scoring.booking import (
    END_TO_END_TASK_SUCCESS,
    TOOL_SELECTION_CORRECTNESS,
    TOOL_SELECTION_STATEMENT,
    BookingScores,
    PostStateRead,
    match_post_state,
    score_booking,
)
from golden_harness.scoring.classification import score_classification
from golden_harness.scoring.retrieval import score_retrieval
from golden_harness.scoring.serving import score_serving

_RUN = Path(__file__).resolve().parents[1] / "fixtures" / "runs" / "us3"
_SCHEMA = Path(__file__).resolve().parents[4] / "evals" / "golden" / "schema.json"
_CLOCK = datetime(2026, 3, 2, 8, 0, 0)
_EXCLUDED = {
    ExclusionReason.UNRESOLVABLE_FIXTURE: 1,
    ExclusionReason.OUTCOME_UNKNOWN: 1,
}


def _labels() -> list[Case]:
    return load_cases(_RUN / "labels.json", _SCHEMA)


def _case_runs() -> list[CaseRun]:
    return [read_case(_RUN, case_id) for case_id in recorded_case_ids(_RUN)]


def _score(clock: datetime = _CLOCK) -> BookingScores:
    return score_booking(_labels(), _case_runs(), clock=clock)


def _ref(practitioner: str, day: str, time: str | None, status: str) -> AppointmentRef:
    return AppointmentRef.model_validate(
        {"practitioner": practitioner, "day": day, "time": time, "status": status}
    )


def _appointment(
    starts_at: str, status: str = "standing", practitioner: str = "William Osler"
) -> AppointmentState:
    raw: dict[str, Any] = {
        "practitioner_full_name": practitioner,
        "starts_at": starts_at,
        "status": status,
    }
    return AppointmentState.model_validate(raw)


def test_the_fixture_run_is_taken_on_the_monday_clock() -> None:
    assert read_run(_RUN).clock == _CLOCK


# --- end-to-end task success --------------------------------------------------------


def test_end_to_end_task_success_over_the_fixture_run() -> None:
    metric = _score().end_to_end_task_success

    assert metric.name == END_TO_END_TASK_SUCCESS
    assert (metric.numerator, metric.denominator) == (4, 8)
    assert metric.excluded.counts == _EXCLUDED


def test_an_expectation_with_no_time_matches_any_start_that_day() -> None:
    failed = {failure.case_id for failure in _score().task_failures}

    assert "G941" not in failed


def test_a_cancelled_planted_appointment_matches_a_cancelled_expectation() -> None:
    failed = {failure.case_id for failure in _score().task_failures}

    assert "G942" not in failed


def test_a_read_only_case_whose_turn_booked_fails_with_the_booking_unaccounted() -> (
    None
):
    (g943,) = [f for f in _score().task_failures if f.case_id == "G943"]

    assert g943.unmatched_expected == []
    assert g943.unaccounted_appointments == [
        _appointment("2026-03-03T09:00:00", practitioner="Andreas Vesalius")
    ]


def test_matching_is_exhaustive_so_the_order_of_expectations_does_not_matter() -> None:
    failed = {failure.case_id for failure in _score().task_failures}

    assert "G944" not in failed


def test_a_failure_names_too_little_and_too_much_separately() -> None:
    (g945,) = [f for f in _score().task_failures if f.case_id == "G945"]

    assert g945.unmatched_expected == [
        _ref("William Osler", "+4d", "10:00", "cancelled")
    ]
    assert g945.unaccounted_appointments == [_appointment("2026-03-06T10:00:00")]


def test_only_the_failing_cases_are_listed_in_case_order() -> None:
    assert [failure.case_id for failure in _score().task_failures] == [
        "G943",
        "G945",
        "G950",
        "G951",
    ]


def test_day_offsets_resolve_against_the_clock_scoring_is_given() -> None:
    week_later = datetime(2026, 3, 9, 8, 0, 0)

    scores = _score(clock=week_later)

    assert "G941" in {failure.case_id for failure in scores.task_failures}
    assert scores.end_to_end_task_success.numerator == 0


def test_excluded_cases_are_never_failures_even_with_a_stored_post_state() -> None:
    scores = _score()

    listed = {f.case_id for f in scores.task_failures} | {
        m.case_id for m in scores.tool_selection_misses
    }
    assert {"G946", "G947"}.isdisjoint(listed)


def test_a_case_with_no_fixture_is_in_neither_booking_metric() -> None:
    scores = _score()

    runs = [run for run in _case_runs() if run.case_id != "G948"]
    without = score_booking(_labels(), runs, clock=_CLOCK)
    assert without == scores


# --- the matching itself ------------------------------------------------------------


def test_a_greedy_trap_in_either_order_still_matches() -> None:
    any_time = _ref("William Osler", "+2d", None, "standing")
    at_ten = _ref("William Osler", "+2d", "10:00", "standing")
    after = [_appointment("2026-03-04T10:00:00"), _appointment("2026-03-04T11:00:00")]

    for expect in ([any_time, at_ten], [at_ten, any_time]):
        for appointments in (after, list(reversed(after))):
            matching = match_post_state(expect, appointments, _CLOCK)
            assert matching.matched
            assert matching.unmatched_expected == []
            assert matching.unaccounted_appointments == []


def test_matching_requires_practitioner_date_status_and_a_stated_time() -> None:
    expect = [_ref("William Osler", "+1d", "10:00", "standing")]

    for wrong in (
        _appointment("2026-03-03T10:00:00", practitioner="Andreas Vesalius"),
        _appointment("2026-03-04T10:00:00"),
        _appointment("2026-03-03T10:00:00", status="cancelled"),
        _appointment("2026-03-03T11:00:00"),
    ):
        matching = match_post_state(expect, [wrong], _CLOCK)
        assert not matching.matched
        assert matching.unmatched_expected == expect
        assert matching.unaccounted_appointments == [wrong]


def test_an_empty_expectation_over_no_appointments_matches() -> None:
    assert match_post_state([], [], _CLOCK).matched


def test_one_appointment_cannot_satisfy_two_expectations() -> None:
    entry = _ref("William Osler", "+1d", None, "standing")

    matching = match_post_state(
        [entry, entry], [_appointment("2026-03-03T10:00:00")], _CLOCK
    )

    assert not matching.matched
    assert matching.unmatched_expected == [entry]
    assert matching.unaccounted_appointments == []


# --- tool-selection correctness -----------------------------------------------------


def test_tool_selection_correctness_over_the_fixture_run() -> None:
    metric = _score().tool_selection_correctness

    assert metric.name == TOOL_SELECTION_CORRECTNESS
    assert (metric.numerator, metric.denominator) == (7, 8)
    assert metric.excluded.counts == _EXCLUDED


def test_a_turn_with_two_booking_requests_is_scored_once_against_their_union() -> None:
    (miss,) = _score().tool_selection_misses

    assert miss.case_id == "G945"
    assert miss.labelled == [
        BookingTool.CANCEL_APPOINTMENT,
        BookingTool.BOOK_APPOINTMENT,
    ]
    assert miss.missing == [BookingTool.CANCEL_APPOINTMENT]
    assert miss.called == ["check_availability", "book_appointment"]


def test_an_extra_tool_is_not_a_miss_and_a_labelled_tool_never_called_is() -> None:
    missed = {miss.case_id for miss in _score().tool_selection_misses}

    # G941 called check_availability beside its labelled book_appointment; G943 booked
    # beside its labelled list_practitioners. G945 never called cancel_appointment.
    assert {"G941", "G943"}.isdisjoint(missed)
    assert "G945" in missed


def test_tools_are_read_from_the_booking_tool_called_events(tmp_path: Path) -> None:
    runs = _case_runs()
    edited = []
    for run in runs:
        if run.case_id == "G942" and run.events is not None:
            events = [
                {**event, "tool_name": "list_my_appointments"}
                if event.get("event") == "booking.tool_called"
                else event
                for event in run.events
            ]
            run = run.model_copy(update={"events": events})
        edited.append(run)

    scores = score_booking(_labels(), edited, clock=_CLOCK)

    assert "G942" in {miss.case_id for miss in scores.tool_selection_misses}


def test_the_scores_state_that_tool_selection_is_per_booking_half() -> None:
    scores = _score()

    assert "booking half" in TOOL_SELECTION_STATEMENT
    assert scores.tool_selection_statement == TOOL_SELECTION_STATEMENT
    assert [m.name for m in scores.metrics()] == [
        TOOL_SELECTION_CORRECTNESS,
        END_TO_END_TASK_SUCCESS,
    ]


# --- a case with a scripted reply (FR-037b) -------------------------------------------


def _edited(case_id: str, **update: Any) -> list[CaseRun]:
    return [
        run.model_copy(update=update) if run.case_id == case_id else run
        for run in _case_runs()
    ]


def _failures_of(case_id: str, runs: list[CaseRun] | None = None) -> list[Any]:
    scores = _score() if runs is None else score_booking(_labels(), runs, clock=_CLOCK)
    return [f for f in scores.task_failures if f.case_id == case_id]


def test_a_reply_case_succeeds_when_both_of_its_reads_match() -> None:
    assert _failures_of("G949") == []


def test_a_first_turn_that_already_cancelled_fails_on_the_read_before_the_reply() -> (
    None
):
    (g950,) = _failures_of("G950")

    assert g950.read is PostStateRead.BEFORE_REPLY
    # The expectation of that read is the planted appointment restated as standing.
    assert g950.unmatched_expected == [
        _ref("William Osler", "+4d", "10:00", "standing")
    ]
    assert g950.unaccounted_appointments == [
        _appointment("2026-03-06T10:00:00", "cancelled")
    ]


def test_a_first_turn_right_and_a_reply_that_wrote_nothing_fails_on_the_last_read() -> (
    None
):
    (g951,) = _failures_of("G951")

    assert g951.read is PostStateRead.AFTER
    assert g951.unmatched_expected == [
        _ref("William Osler", "+7d", "09:00", "standing")
    ]
    assert g951.unaccounted_appointments == []


def test_a_case_without_a_reply_fails_on_its_one_read() -> None:
    assert {
        f.read for f in _score().task_failures if f.case_id in {"G943", "G945"}
    } == {PostStateRead.AFTER}


def test_a_reply_case_succeeds_once_its_first_read_matches_as_well() -> None:
    fixed = _edited(
        "G950", scheduling_before_reply=[_appointment("2026-03-06T10:00:00")]
    )

    assert _failures_of("G950", fixed) == []
    assert (
        score_booking(_labels(), fixed, clock=_CLOCK).end_to_end_task_success.numerator
        == 5
    )


def test_a_reply_case_whose_two_reads_both_failed_is_listed_once_per_read() -> None:
    runs = _edited("G950", scheduling_after=[])

    failures = _failures_of("G950", runs)

    assert [f.read for f in failures] == [
        PostStateRead.BEFORE_REPLY,
        PostStateRead.AFTER,
    ]
    assert failures[1].unmatched_expected == [
        _ref("William Osler", "+4d", "10:00", "cancelled")
    ]
    scores = score_booking(_labels(), runs, clock=_CLOCK)
    assert scores.end_to_end_task_success.numerator == 4


def test_a_cancel_called_only_in_the_reply_turn_is_a_tool_selection_hit() -> None:
    (g949,) = [run for run in _case_runs() if run.case_id == "G949"]
    assert g949.events is not None
    assert "cancel_appointment" not in [e.get("tool_name") for e in g949.events]

    assert "G949" not in {miss.case_id for miss in _score().tool_selection_misses}


def test_tool_selection_reads_the_reply_turns_own_events() -> None:
    (g949,) = [run for run in _case_runs() if run.case_id == "G949"]
    assert g949.reply_turn is not None and g949.reply_turn.events is not None
    without_cancel = [
        e for e in g949.reply_turn.events if e.get("tool_name") != "cancel_appointment"
    ]
    runs = _edited(
        "G949",
        reply_turn=g949.reply_turn.model_copy(update={"events": without_cancel}),
    )

    (miss,) = [
        m
        for m in score_booking(_labels(), runs, clock=_CLOCK).tool_selection_misses
        if m.case_id == "G949"
    ]

    # Called once in each turn, and listed once.
    assert miss.called == ["list_my_appointments"]
    assert miss.missing == [BookingTool.CANCEL_APPOINTMENT]


def test_classification_ignores_the_reply_turn() -> None:
    labels, runs = _labels(), _case_runs()
    stripped = [run.model_copy(update={"reply_turn": None}) for run in runs]

    assert score_classification(
        labels, align_run(labels, runs), runs
    ) == score_classification(labels, align_run(labels, stripped), stripped)


def test_a_reply_case_whose_first_turn_did_not_reply_is_scored_on_its_one_read() -> (
    None
):
    # A handed-off first turn is not set aside from the booking metrics, and no reply
    # was posted: the appointments read after that turn are all there is to score.
    runs = _edited(
        "G949",
        reply_turn=None,
        scheduling_before_reply=None,
        scheduling_after=[_appointment("2026-03-05T11:00:00")],
        excluded=ExclusionReason.HANDED_OFF_TURN,
    )

    (g949,) = _failures_of("G949", runs)

    assert g949.read is PostStateRead.AFTER
    assert g949.unaccounted_appointments == [_appointment("2026-03-05T11:00:00")]


def test_a_reply_turn_that_handed_off_is_scored_for_classification_and_booking() -> (
    None
):
    # FR-037b, FR-018a: G949 given an answerable FAQ request beside its cancel, and a
    # reply turn that handed off. The exclusion reaches the first turn's FAQ request
    # too, and sets nothing aside from classification or booking.
    (g949,) = [case for case in _labels() if case.id == "G949"]
    faq = {
        "intent": "faq_question",
        "gist": "g",
        "answerable": True,
        "cites": ["payment"],
    }
    label = Case.model_validate(
        {
            **g949.model_dump(mode="json", exclude_none=True),
            "requests": [
                *g949.model_dump(mode="json", exclude_none=True)["requests"],
                faq,
            ],
        }
    )
    (run,) = [run for run in _case_runs() if run.case_id == "G949"]
    assert run.segments is not None and run.reply_turn is not None
    reply_terminal = run.reply_turn.terminal
    assert reply_terminal is not None
    handed_off = run.model_copy(
        update={
            "segments": run.segments.model_copy(
                update={
                    "segments": [
                        *run.segments.segments,
                        run.segments.segments[0].model_copy(
                            update={"position": 1, "intent": "faq_question"}
                        ),
                    ]
                }
            ),
            "reply_turn": run.reply_turn.model_copy(
                update={
                    "terminal": reply_terminal.model_copy(
                        update={
                            "payload": {
                                **reply_terminal.payload,
                                "answer_source": "hand_off",
                            }
                        }
                    )
                }
            ),
            "excluded": ExclusionReason.HANDED_OFF_TURN,
        }
    )
    labels, runs = [label], [handed_off]
    alignment = align_run(labels, runs)
    run_json = read_run(_RUN)

    classification = score_classification(labels, alignment, runs)
    retrieval = score_retrieval(
        labels,
        alignment,
        runs,
        entry_ids=run_json.entry_ids,
        similarity_cap=run_json.conditions.similarity_cap,
    )
    serving = score_serving(labels, alignment, runs)
    booking = score_booking(labels, runs, clock=_CLOCK)

    count = classification.request_count_accuracy
    assert (count.numerator, count.denominator) == (1, 1)
    assert classification.intent_accuracy.denominator == 2
    assert count.excluded.counts == {}
    (request,) = retrieval.requests
    assert request.similarity.excluded is ExclusionReason.HANDED_OFF_TURN
    assert request.rerank.excluded is ExclusionReason.HANDED_OFF_TURN
    assert retrieval.similarity_hit_at_1.denominator == 0
    assert serving.unserved_answerable_share.denominator == 0
    assert serving.unserved_answerable_share.excluded.counts == {
        ExclusionReason.HANDED_OFF_TURN: 1
    }
    assert serving.not_permitted_to_answer == ["G949"]
    tools, success = booking.tool_selection_correctness, booking.end_to_end_task_success
    assert (tools.numerator, tools.denominator) == (1, 1)
    assert (success.numerator, success.denominator) == (1, 1)
    assert success.excluded.counts == {}


def test_a_posted_reply_with_no_read_before_it_is_refused() -> None:
    runs = _edited("G949", scheduling_before_reply=None)

    with pytest.raises(ValueError, match="G949"):
        score_booking(_labels(), runs, clock=_CLOCK)


def test_a_posted_reply_with_no_events_and_no_exclusion_is_refused() -> None:
    (g949,) = [run for run in _case_runs() if run.case_id == "G949"]
    assert g949.reply_turn is not None
    runs = _edited(
        "G949", reply_turn=g949.reply_turn.model_copy(update={"events": None})
    )

    with pytest.raises(ValueError, match="G949"):
        score_booking(_labels(), runs, clock=_CLOCK)


def test_a_reply_turn_recorded_on_a_case_whose_label_has_no_reply_is_refused() -> None:
    (g949,) = [run for run in _case_runs() if run.case_id == "G949"]
    runs = _edited("G942", reply_turn=g949.reply_turn)

    with pytest.raises(ValueError, match="G942"):
        score_booking(_labels(), runs, clock=_CLOCK)
