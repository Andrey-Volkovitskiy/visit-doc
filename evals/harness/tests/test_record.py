"""The stored record of a run: models that round-trip, refuse, and land whole."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from golden_harness.record import (
    AppointmentState,
    CaseRun,
    ExclusionReason,
    ReplyTurn,
    Run,
    RunConditions,
    RunTracing,
    TerminalKind,
    Unplantable,
    UnplantableSituation,
    read_case,
    read_run,
    recorded_case_ids,
    write_case,
    write_run,
)
from pydantic import ValidationError

_CONDITIONS: dict[str, Any] = {
    "classification_model": "claude-haiku-4-5-20251001",
    "generation_model": "claude-sonnet-5",
    "embedding_model": "voyage-3.5",
    "rerank_model": "rerank-3",
    "retrieval_pool_size": 25,
    "similarity_floor": 0.3,
    "similarity_cap": 5,
    "rerank_floor": 0.58,
    "rerank_cap": 3,
    "max_segments": 3,
    "context_turns": 5,
}


def _run(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "run_id": "01K5AAAAAAAAAAAAAAAAAAAAAA",
        "started_at": "2026-09-14T10:00:00Z",
        "finished_at": None,
        "drive_seconds": 12.5,
        "clock": "2026-03-02T08:00:00",
        "session_id": "01K5BBBBBBBBBBBBBBBBBBBBBB",
        "corpus": {"live_sha256": "a" * 64, "pinned_sha256": "a" * 64, "matched": True},
        "selection": {"kind": "ids", "case_ids": ["G001", "G042"]},
        "entry_ids": {"referral": 101, "what-to-bring": 102},
        "conditions": copy.deepcopy(_CONDITIONS),
        "labels": {"G001": "1" * 64, "G042": "2" * 64},
        "cases": ["G001"],
    }
    raw.update(overrides)
    return raw


def _case(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "case_id": "G001",
        "chat_id": "01K5CCCCCCCCCCCCCCCCCCCCCC",
        "patient_id": "01K5DDDDDDDDDDDDDDDDDDDDDD",
        "attempts": 1,
        "elapsed_seconds": 4.2,
        "terminal": {
            "kind": "done",
            "payload": {"type": "done", "answer_source": "faq", "message": None},
        },
        "patient_message": {
            "id": "01K5EEEEEEEEEEEEEEEEEEEEEE",
            "content": "What should I bring to my first appointment?",
            "attention_mark": None,
        },
        "assistant_message": {
            "id": "01K5FFFFFFFFFFFFFFFFFFFFFF",
            "content": "Bring a photo ID.",
            "request_outcomes": [
                {
                    "position": 0,
                    "question": "What should I bring to my first appointment?",
                    "answer": "Bring a photo ID.",
                    "verdict": "answered",
                    "citations": [
                        {"entry_id": 102, "chunk_index": 0, "chunk_text": "Bring ID."}
                    ],
                }
            ],
        },
        "segments": {
            "segments": [
                {
                    "position": 0,
                    "intent": "faq_question",
                    "text": "What should I bring to my first appointment?",
                }
            ],
            "cap_bound": False,
        },
        "events": [
            {"event": "turn.message_received", "turn_id": "t1", "segment": None},
            {"event": "intent.classified", "turn_id": "t1", "cap_bound": False},
        ],
    }
    raw.update(overrides)
    return raw


def test_a_run_round_trips_through_json_unchanged() -> None:
    run = Run.model_validate(_run(finished_at="2026-09-14T10:05:00Z"))

    assert Run.model_validate_json(run.model_dump_json()) == run


def test_a_case_run_round_trips_through_json_unchanged() -> None:
    appointment = {
        "practitioner_full_name": "William Osler",
        "starts_at": "2026-03-03T10:00:00",
        "status": "cancelled",
    }
    case_run = CaseRun.model_validate(
        _case(
            scheduling_after=[appointment],
            cancelled_after=[{**appointment, "status": "standing"}],
            excluded="handed_off_turn",
        )
    )

    assert CaseRun.model_validate_json(case_run.model_dump_json()) == case_run


def test_request_outcomes_are_parsed_through_the_services_own_model() -> None:
    raw = _case()
    raw["assistant_message"]["request_outcomes"][0]["answer"] = None

    with pytest.raises(ValidationError, match="answered request"):
        CaseRun.model_validate(raw)


def test_an_unknown_verdict_is_rejected_not_bucketed() -> None:
    raw = _case()
    raw["assistant_message"]["request_outcomes"][0]["verdict"] = "answered_partly"

    with pytest.raises(ValidationError):
        CaseRun.model_validate(raw)


def test_an_unknown_produced_intent_is_rejected_not_bucketed() -> None:
    raw = _case()
    raw["segments"]["segments"][0]["intent"] = "faq"

    with pytest.raises(ValidationError):
        CaseRun.model_validate(raw)


def test_an_unknown_attention_mark_is_rejected() -> None:
    raw = _case()
    raw["patient_message"]["attention_mark"] = "assistant_broke"

    with pytest.raises(ValidationError):
        CaseRun.model_validate(raw)


def test_produced_segment_positions_run_from_zero_in_order() -> None:
    raw = _case()
    raw["segments"]["segments"][0]["position"] = 1

    with pytest.raises(ValidationError):
        CaseRun.model_validate(raw)


def test_the_exclusion_reasons_are_exactly_the_eleven_situations() -> None:
    assert {reason.value for reason in ExclusionReason} == {
        "run_error",
        "silenced_turn",
        "handed_off_turn",
        "cancelled_turn",
        "missing_log_slice",
        "unresolvable_fixture",
        "outcome_unknown",
        "reranker_unavailable",
        "not_reached_reranker",
        "no_search",
        "not_routed_to_faq",
    }


def test_the_terminal_kinds_are_exactly_four() -> None:
    assert {kind.value for kind in TerminalKind} == {
        "done",
        "silent",
        "cancelled",
        "error",
    }


def test_an_unknown_terminal_kind_is_rejected() -> None:
    raw = _case()
    raw["terminal"]["kind"] = "timeout"

    with pytest.raises(ValidationError):
        CaseRun.model_validate(raw)


def test_a_patient_id_is_carried_when_the_chat_has_one() -> None:
    assert CaseRun.model_validate(_case()).patient_id == "01K5DDDDDDDDDDDDDDDDDDDDDD"
    assert CaseRun.model_validate(_case(patient_id=None)).patient_id is None


def test_cancelled_after_is_a_list_of_appointment_states_empty_by_default() -> None:
    assert CaseRun.model_validate(_case()).cancelled_after == []

    case_run = CaseRun.model_validate(
        _case(
            cancelled_after=[
                {
                    "practitioner_full_name": "Andreas Vesalius",
                    "starts_at": "2026-03-07T09:00:00",
                    "status": "standing",
                }
            ]
        )
    )

    assert case_run.cancelled_after == [
        AppointmentState.model_validate(
            {
                "practitioner_full_name": "Andreas Vesalius",
                "starts_at": "2026-03-07T09:00:00",
                "status": "standing",
            }
        )
    ]


def test_scheduling_after_is_absent_for_a_case_with_no_fixture() -> None:
    assert CaseRun.model_validate(_case()).scheduling_after is None


# --- a reply the first turn made unnecessary ------------------------------------------


def test_a_skipped_reply_is_recorded_beside_the_read_that_skipped_it() -> None:
    case_run = CaseRun.model_validate(
        _case(
            scheduling_before_reply=[_CANCELLED_STATE],
            reply_skipped=True,
            scheduling_after=[_CANCELLED_STATE],
        )
    )

    assert case_run.reply_skipped is True


def test_a_case_recorded_before_replies_could_be_skipped_reads_as_not_skipped() -> None:
    assert CaseRun.model_validate(_case()).reply_skipped is False


def test_a_skipped_reply_with_no_read_to_decide_it_is_refused() -> None:
    with pytest.raises(ValidationError, match="skipped reply"):
        CaseRun.model_validate(_case(reply_skipped=True))


def test_a_skipped_reply_beside_a_posted_one_is_refused() -> None:
    with pytest.raises(ValidationError, match="skipped reply"):
        CaseRun.model_validate(
            _case(
                reply_turn=_reply_turn(),
                scheduling_before_reply=[_CANCELLED_STATE],
                reply_skipped=True,
            )
        )


_CANCELLED_STATE = {
    "practitioner_full_name": "William Osler",
    "starts_at": "2026-03-03T10:00:00",
    "status": "cancelled",
}


# --- the reply turn (FR-037b) --------------------------------------------------------

_STANDING = {
    "practitioner_full_name": "William Osler",
    "starts_at": "2026-03-03T10:00:00",
    "status": "standing",
}


def _reply_turn(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "terminal": {
            "kind": "done",
            "payload": {"type": "done", "answer_source": "booking", "message": None},
        },
        "patient_message": {
            "id": "01K5GGGGGGGGGGGGGGGGGGGGGG",
            "content": "Yes, please go ahead.",
            "attention_mark": None,
        },
        "assistant_message": {
            "id": "01K5HHHHHHHHHHHHHHHHHHHHHH",
            "content": "Your appointment is cancelled.",
            "request_outcomes": None,
        },
        "events": [
            {"event": "turn.message_received", "turn_id": "t2"},
            {
                "event": "booking.tool_called",
                "turn_id": "t2",
                "tool_name": "cancel_appointment",
            },
        ],
    }
    raw.update(overrides)
    return raw


def test_a_case_run_with_a_reply_turn_and_both_reads_round_trips_unchanged() -> None:
    case_run = CaseRun.model_validate(
        _case(
            reply_turn=_reply_turn(),
            scheduling_before_reply=[_STANDING],
            scheduling_after=[{**_STANDING, "status": "cancelled"}],
        )
    )

    assert CaseRun.model_validate_json(case_run.model_dump_json()) == case_run
    assert case_run.reply_turn is not None
    assert case_run.reply_turn.terminal is not None
    assert case_run.reply_turn.terminal.kind is TerminalKind.DONE
    assert case_run.reply_turn.patient_message is not None
    assert case_run.reply_turn.patient_message.content == "Yes, please go ahead."
    assert case_run.reply_turn.assistant_message is not None
    assert [e.get("tool_name") for e in case_run.reply_turn.events or []] == [
        None,
        "cancel_appointment",
    ]
    assert case_run.scheduling_before_reply == [
        AppointmentState.model_validate(_STANDING)
    ]


def test_a_case_run_carries_no_reply_turn_and_no_read_before_it_by_default() -> None:
    case_run = CaseRun.model_validate(_case())

    assert case_run.reply_turn is None
    assert case_run.scheduling_before_reply is None


def test_a_reply_turn_that_was_posted_but_not_read_back_has_every_field_optional() -> (
    None
):
    # A reply whose stream broke the service's contract, and whose thread was not read.
    reply_turn = ReplyTurn.model_validate({})

    assert (
        reply_turn.terminal,
        reply_turn.patient_message,
        reply_turn.assistant_message,
        reply_turn.events,
    ) == (None, None, None, None)
    case_run = CaseRun.model_validate(_case(reply_turn={}, excluded="outcome_unknown"))
    assert case_run.reply_turn == reply_turn


def test_a_reply_turn_with_no_stored_reply_round_trips() -> None:
    raw = _reply_turn(
        assistant_message=None,
        terminal={"kind": "error", "payload": {}},
    )
    raw["patient_message"]["attention_mark"] = "assistant_failed"

    case_run = CaseRun.model_validate(_case(reply_turn=raw, excluded="run_error"))

    assert CaseRun.model_validate_json(case_run.model_dump_json()) == case_run
    assert case_run.reply_turn is not None
    assert case_run.reply_turn.assistant_message is None


def test_a_read_before_the_reply_may_be_recorded_empty() -> None:
    case_run = CaseRun.model_validate(_case(scheduling_before_reply=[]))

    assert case_run.scheduling_before_reply == []


@pytest.mark.parametrize(
    "reply_turn",
    [
        {"terminal": {"kind": "finished", "payload": {}}},
        {"patient_message": {"id": "x", "content": "y", "attention_mark": "odd"}},
        {"surprise": True},
    ],
    ids=["unknown-terminal", "unknown-mark", "unknown-key"],
)
def test_a_reply_turn_breaking_its_shape_is_rejected(
    reply_turn: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        CaseRun.model_validate(_case(reply_turn=reply_turn))


def test_a_read_before_the_reply_with_an_unknown_status_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CaseRun.model_validate(
            _case(scheduling_before_reply=[{**_STANDING, "status": "tentative"}])
        )


def test_a_reply_turn_and_its_read_survive_a_case_file_write(tmp_path: Path) -> None:
    case_run = CaseRun.model_validate(
        _case(reply_turn=_reply_turn(), scheduling_before_reply=[_STANDING])
    )

    write_case(tmp_path, case_run)

    assert read_case(tmp_path, "G001") == case_run


def test_an_appointment_with_an_unknown_status_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AppointmentState.model_validate(
            {
                "practitioner_full_name": "William Osler",
                "starts_at": "2026-03-03T10:00:00",
                "status": "tentative",
            }
        )


@pytest.mark.parametrize(
    "reason",
    [
        "run_error",
        "silenced_turn",
        "handed_off_turn",
        "cancelled_turn",
        "missing_log_slice",
        "unresolvable_fixture",
        "outcome_unknown",
    ],
)
def test_a_case_run_accepts_every_turn_level_exclusion(reason: str) -> None:
    case_run = CaseRun.model_validate(_case(excluded=reason))

    assert case_run.excluded is ExclusionReason(reason)


@pytest.mark.parametrize(
    "reason",
    ["reranker_unavailable", "not_reached_reranker", "no_search", "not_routed_to_faq"],
)
def test_a_case_run_refuses_a_request_level_exclusion(reason: str) -> None:
    with pytest.raises(ValidationError):
        CaseRun.model_validate(_case(excluded=reason))


def test_a_case_run_is_unexcluded_by_default() -> None:
    assert CaseRun.model_validate(_case()).excluded is None


def test_a_case_run_records_its_attempts_elapsed_time_and_events() -> None:
    case_run = CaseRun.model_validate(_case(attempts=2, elapsed_seconds=9.0))

    assert case_run.attempts == 2
    assert case_run.elapsed_seconds == 9.0
    assert [event["event"] for event in case_run.events or []] == [
        "turn.message_received",
        "intent.classified",
    ]


def test_a_case_run_needs_at_least_one_attempt() -> None:
    with pytest.raises(ValidationError):
        CaseRun.model_validate(_case(attempts=0))


def test_a_run_requires_its_entry_ids() -> None:
    raw = _run()
    del raw["entry_ids"]

    with pytest.raises(ValidationError):
        Run.model_validate(raw)


def test_a_run_refuses_an_entry_id_that_is_not_an_integer() -> None:
    with pytest.raises(ValidationError):
        Run.model_validate(_run(entry_ids={"referral": "one hundred and one"}))


def test_a_run_requires_its_label_digests() -> None:
    raw = _run()
    del raw["labels"]

    with pytest.raises(ValidationError):
        Run.model_validate(raw)


def test_a_run_refuses_label_digests_that_are_not_one_per_selected_case() -> None:
    with pytest.raises(ValidationError):
        Run.model_validate(_run(labels={"G001": "1" * 64}))
    with pytest.raises(ValidationError):
        Run.model_validate(
            _run(labels={"G001": "1" * 64, "G042": "2" * 64, "G099": "3" * 64})
        )


def test_a_run_refuses_a_recorded_case_it_did_not_select() -> None:
    with pytest.raises(ValidationError):
        Run.model_validate(_run(cases=["G001", "G099"]))


@pytest.mark.parametrize("field", sorted(_CONDITIONS))
def test_run_conditions_require_every_field_of_the_settings_event(field: str) -> None:
    raw = _run()
    del raw["conditions"][field]

    with pytest.raises(ValidationError):
        Run.model_validate(raw)


def test_run_conditions_are_exactly_the_settings_event_fields() -> None:
    run = Run.model_validate(_run())

    assert set(run.conditions.model_dump()) == set(_CONDITIONS)


def test_a_case_file_is_written_and_read_back(tmp_path: Path) -> None:
    case_run = CaseRun.model_validate(_case())

    write_case(tmp_path, case_run)

    assert read_case(tmp_path, "G001") == case_run
    assert (tmp_path / "cases" / "G001.json").is_file()


def test_run_json_is_written_and_read_back(tmp_path: Path) -> None:
    run = Run.model_validate(_run())

    write_run(tmp_path, run)

    assert read_run(tmp_path) == run
    assert json.loads((tmp_path / "run.json").read_text())["run_id"] == run.run_id


def test_a_failed_case_write_leaves_the_previous_file_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = CaseRun.model_validate(_case())
    write_case(tmp_path, first)
    before = (tmp_path / "cases" / "G001.json").read_bytes()

    def _crash(*_args: object) -> None:
        raise OSError("disk went away")

    monkeypatch.setattr("golden_harness.record.os.replace", _crash)
    with pytest.raises(OSError, match="disk went away"):
        write_case(tmp_path, CaseRun.model_validate(_case(attempts=3)))

    assert (tmp_path / "cases" / "G001.json").read_bytes() == before
    assert sorted(p.name for p in (tmp_path / "cases").iterdir()) == ["G001.json"]


def test_a_failed_first_case_write_leaves_no_case_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _crash(*_args: object) -> None:
        raise OSError("disk went away")

    monkeypatch.setattr("golden_harness.record.os.replace", _crash)
    with pytest.raises(OSError):
        write_case(tmp_path, CaseRun.model_validate(_case()))

    assert recorded_case_ids(tmp_path) == []
    assert list((tmp_path / "cases").iterdir()) == []


def test_a_failed_run_write_leaves_the_previous_run_json_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_run(tmp_path, Run.model_validate(_run()))
    before = (tmp_path / "run.json").read_bytes()

    def _crash(*_args: object) -> None:
        raise OSError("disk went away")

    monkeypatch.setattr("golden_harness.record.os.replace", _crash)
    with pytest.raises(OSError):
        write_run(tmp_path, Run.model_validate(_run(cases=["G001", "G042"])))

    assert (tmp_path / "run.json").read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["run.json"]


def test_recorded_case_ids_is_a_listing_of_the_case_files(tmp_path: Path) -> None:
    for case_id in ("G042", "G001"):
        write_case(tmp_path, CaseRun.model_validate(_case(case_id=case_id)))
    (tmp_path / "cases" / ".G007.json.tmp").write_text("{")
    (tmp_path / "cases" / "notes.txt").write_text("not a case")

    assert recorded_case_ids(tmp_path) == ["G001", "G042"]


def test_recorded_case_ids_of_a_run_with_no_cases_yet_is_empty(tmp_path: Path) -> None:
    assert recorded_case_ids(tmp_path) == []


def test_run_conditions_are_built_from_a_settings_event_without_its_envelope() -> None:
    event = {
        "event": "service.configured",
        "level": "info",
        "timestamp": "2026-09-14T09:59:00Z",
        **_CONDITIONS,
    }

    conditions = RunConditions.from_event(event)

    assert conditions.model_dump() == _CONDITIONS


@pytest.mark.parametrize("tracing_enabled", [True, False])
def test_the_services_tracing_state_is_not_a_run_condition(
    tracing_enabled: bool,
) -> None:
    # `service.configured` states whether the service traces, but a traced and an
    # untraced run of one build answer the same way - so it must never enter a
    # condition delta, a band's sameness check or a restart comparison.
    event = {
        "event": "service.configured",
        **_CONDITIONS,
        "tracing_enabled": tracing_enabled,
    }

    conditions = RunConditions.from_event(event)

    assert conditions.model_dump() == _CONDITIONS
    assert "tracing_enabled" not in RunConditions.model_fields


def test_run_conditions_from_an_event_missing_a_field_are_refused() -> None:
    event = {"event": "service.configured", **_CONDITIONS}
    del event["rerank_floor"]

    with pytest.raises(ValidationError):
        RunConditions.from_event(event)


# --- why a fixture would not plant ---------------------------------------------------

_REFUSED: dict[str, Any] = {
    "situation": "booking_refused",
    "detail": "William Osler +1d 10:00: refused: "
    "BOOKING_FAILURE_REASON_PRACTITIONER_BUSY taken",
    "failure_reason": "BOOKING_FAILURE_REASON_PRACTITIONER_BUSY",
    "scheduler_message": "taken",
}


def test_the_unplantable_situations_are_exactly_six() -> None:
    assert {situation.value for situation in UnplantableSituation} == {
        "not_on_roster",
        "roster_unreadable",
        "booking_refused",
        "booking_unanswered",
        "booking_unreadable",
        "no_patient",
    }


def test_an_unresolvable_fixture_case_round_trips_with_why_it_would_not_plant() -> None:
    case_run = CaseRun.model_validate(
        _unplanted_case(unplantable=copy.deepcopy(_REFUSED))
    )

    assert case_run.unplantable == Unplantable.model_validate(_REFUSED)
    assert CaseRun.model_validate_json(case_run.model_dump_json()) == case_run


def test_a_case_run_carries_no_unplantable_detail_by_default() -> None:
    assert CaseRun.model_validate(_case()).unplantable is None
    assert CaseRun.model_validate(_unplanted_case()).unplantable is None


@pytest.mark.parametrize("excluded", [None, "run_error", "outcome_unknown"])
def test_an_unplantable_detail_is_refused_on_a_case_its_fixture_did_not_exclude(
    excluded: str | None,
) -> None:
    with pytest.raises(ValidationError, match="unresolvable_fixture"):
        CaseRun.model_validate(
            _case(excluded=excluded, unplantable=copy.deepcopy(_REFUSED))
        )


def test_an_unknown_unplantable_situation_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Unplantable.model_validate({**_REFUSED, "situation": "scheduler_down"})


def test_a_refused_booking_must_carry_its_failure_reason() -> None:
    with pytest.raises(ValidationError, match="failure_reason"):
        Unplantable.model_validate({**_REFUSED, "failure_reason": None})


def test_a_refused_booking_may_come_without_a_scheduler_message() -> None:
    unplantable = Unplantable.model_validate({**_REFUSED, "scheduler_message": None})

    assert unplantable.scheduler_message is None


@pytest.mark.parametrize(
    "situation",
    [
        "not_on_roster",
        "roster_unreadable",
        "booking_unanswered",
        "booking_unreadable",
        "no_patient",
    ],
)
@pytest.mark.parametrize("field", ["failure_reason", "scheduler_message"])
def test_only_a_refused_booking_carries_a_failure_reason_or_a_scheduler_message(
    situation: str, field: str
) -> None:
    raw = {"situation": situation, "detail": "d", field: "x"}

    assert Unplantable.model_validate({"situation": situation, "detail": "d"})
    with pytest.raises(ValidationError, match=field):
        Unplantable.model_validate(raw)


def _unplanted_case(**overrides: Any) -> dict[str, Any]:
    raw = _case(
        terminal=None,
        patient_message=None,
        assistant_message=None,
        segments=None,
        events=None,
        excluded="unresolvable_fixture",
    )
    raw.update(overrides)
    return raw


# --- a stream that broke the service's contract (FR-041d) ---------------------------


def test_no_turn_is_recorded_as_breaking_the_stream_contract_by_default() -> None:
    case_run = CaseRun.model_validate(_case(reply_turn=_reply_turn()))

    assert case_run.stream_broke_contract is False
    assert case_run.reply_turn is not None
    assert case_run.reply_turn.stream_broke_contract is False


def test_a_turn_whose_stream_broke_the_contract_round_trips_with_no_terminal() -> None:
    case_run = CaseRun.model_validate(
        _case(
            terminal=None,
            stream_broke_contract=True,
            reply_turn=_reply_turn(terminal=None, stream_broke_contract=True),
        )
    )

    assert CaseRun.model_validate_json(case_run.model_dump_json()) == case_run
    assert case_run.stream_broke_contract is True
    assert case_run.reply_turn is not None
    assert case_run.reply_turn.stream_broke_contract is True


@pytest.mark.parametrize("kind", ["done", "silent", "cancelled", "error"])
def test_a_stream_that_broke_the_contract_beside_a_terminal_is_rejected(
    kind: str,
) -> None:
    # A stream that broke the contract was not read to an ending, so it records none.
    terminal = {"kind": kind, "payload": {}}
    with pytest.raises(ValidationError):
        CaseRun.model_validate(_case(terminal=terminal, stream_broke_contract=True))
    with pytest.raises(ValidationError):
        CaseRun.model_validate(
            _case(reply_turn=_reply_turn(terminal=terminal, stream_broke_contract=True))
        )


# --- Tracing (014): the trace of each turn, and whether the run was traced ----------

_BASELINE = (
    Path(__file__).resolve().parents[2] / "baselines" / "01M321DWRXSVSY7GW9RY3CR9YW"
)


def test_a_case_run_names_no_traces_by_default() -> None:
    assert CaseRun.model_validate(_case()).traces == {}


def test_a_case_runs_traces_round_trip_through_its_file(tmp_path: Path) -> None:
    traces = {"01K5EEEEEEEEEEEEEEEEEEEEEE": "0af7651916cd43dd8448eb211c80319c"}
    case_run = CaseRun.model_validate(_case(traces=traces))

    write_case(tmp_path, case_run)

    assert read_case(tmp_path, "G001").traces == traces


def test_a_case_file_written_before_tracing_still_reads() -> None:
    raw = json.loads((_BASELINE / "cases" / "G-a-01.json").read_text())
    assert "traces" not in raw

    assert CaseRun.model_validate(raw).traces == {}


def test_a_case_run_still_refuses_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        CaseRun.model_validate(_case(trace_ids={}))


def test_a_runs_tracing_is_one_of_three_states() -> None:
    assert {state.value for state in RunTracing} == {
        "traced",
        "untraced_by_request",
        "untraced_service_off",
    }


@pytest.mark.parametrize("state", list(RunTracing))
def test_a_runs_tracing_round_trips_through_run_json(
    tmp_path: Path, state: RunTracing
) -> None:
    write_run(tmp_path, Run.model_validate(_run(tracing=state.value)))

    assert read_run(tmp_path).tracing is state


def test_a_run_recorded_before_tracing_reads_as_the_service_not_tracing() -> None:
    assert Run.model_validate(_run()).tracing is RunTracing.UNTRACED_SERVICE_OFF


def test_the_committed_baseline_still_reads_and_was_not_traced() -> None:
    assert "tracing" not in json.loads((_BASELINE / "run.json").read_text())

    assert read_run(_BASELINE).tracing is RunTracing.UNTRACED_SERVICE_OFF


def test_an_unknown_tracing_state_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Run.model_validate(_run(tracing="traced_sometimes"))


def test_a_runs_tracing_is_not_one_of_its_conditions() -> None:
    assert "tracing" not in RunConditions.model_fields
