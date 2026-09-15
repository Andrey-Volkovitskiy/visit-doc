"""Taking one turn's events out of the JSON log: one test per invariant."""

import json
from pathlib import Path
from typing import Any

import pytest
from chat.domain.schemas import IntentLabel
from golden_harness.driver.logslice import (
    ConditionsMissingError,
    JsonLogMissingError,
    SliceMissing,
    SliceMissingReason,
    group_by_turn,
    log_offset,
    preceding_bytes,
    produced_segmentation,
    read_slice,
    require_json_log,
    restarts_in,
    restarts_since,
    select_turn,
    service_conditions,
)
from golden_harness.record import ProducedSegmentation

_UVICORN_STARTUP = (
    "INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)"
)
_UVICORN_ACCESS = 'INFO:     127.0.0.1:51234 - "POST /chat HTTP/1.1" 200 OK'
_CONSOLE_LINE = (
    "\x1b[2m2026-09-14T10:00:00.000Z\x1b[0m [\x1b[32minfo     \x1b[0m] "
    "turn.message_received  message='hi'"
)
_TRACEBACK = [
    "Traceback (most recent call last):",
    '  File "/app/chat/api/turn.py", line 1, in run_pipeline',
    "RuntimeError: boom",
]

_CONFIGURED: dict[str, Any] = {
    "event": "service.configured",
    "level": "info",
    "timestamp": "2026-09-14T09:59:00Z",
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


def _event(name: str, turn_id: str | None = None, **fields: Any) -> str:
    payload: dict[str, Any] = {"event": name, "level": "info"}
    if turn_id is not None:
        payload["turn_id"] = turn_id
    payload.update(fields)
    return json.dumps(payload)


def _received(turn_id: str, *message_ids: str) -> str:
    return _event(
        "turn.message_received",
        turn_id,
        message="hello",
        message_ids_unified=list(message_ids),
    )


def _classified(turn_id: str, *intents: str, cap_bound: bool = False) -> str:
    return _event(
        "intent.classified",
        turn_id,
        intents=list(intents),
        segments=[
            {"position": i, "intent": intent, "text": f"request {i}"}
            for i, intent in enumerate(intents)
        ],
        cap_bound=cap_bound,
    )


def _append(path: Path, *lines: str, newline: bool = True) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + ("\n" if newline else ""))


def _events(result: object) -> list[dict[str, Any]]:
    assert isinstance(result, list), result
    return result


def test_the_offset_is_the_files_current_size(tmp_path: Path) -> None:
    log = tmp_path / "chat.log"
    _append(log, _UVICORN_STARTUP)

    assert log_offset(log) == log.stat().st_size


def test_a_slice_holds_only_lines_appended_after_the_offset(tmp_path: Path) -> None:
    log = tmp_path / "chat.log"
    _append(log, _event("before", "t0"), _event("also.before", "t0"))
    offset = log_offset(log)
    _append(log, _event("after", "t1"), _event("also.after", "t1"))

    names = [event["event"] for event in _events(read_slice(log, offset))]

    assert names == ["after", "also.after"]


def test_events_are_grouped_by_turn_id_in_log_order(tmp_path: Path) -> None:
    log = tmp_path / "chat.log"
    _append(
        log,
        _event("a.first", "t1"),
        _event("b.first", "t2"),
        _event("startup.line"),
        _event("a.second", "t1"),
    )

    groups = group_by_turn(_events(read_slice(log, 0)))

    assert {turn: [e["event"] for e in g] for turn, g in groups.items()} == {
        "t1": ["a.first", "a.second"],
        "t2": ["b.first"],
    }


def test_the_turn_whose_received_message_ids_hold_the_patient_message_is_selected(
    tmp_path: Path,
) -> None:
    log = tmp_path / "chat.log"
    _append(
        log,
        _received("t1", "M-PLANTED", "M-CASE"),
        _classified("t1", "faq_question"),
    )

    selected = _events(select_turn(_events(read_slice(log, 0)), "M-CASE"))

    assert [event["event"] for event in selected] == [
        "turn.message_received",
        "intent.classified",
    ]
    assert {event["turn_id"] for event in selected} == {"t1"}


def test_an_interleaved_second_turn_is_left_out(tmp_path: Path) -> None:
    log = tmp_path / "chat.log"
    _append(
        log,
        _received("t-case", "M-CASE"),
        _received("t-browser", "M-BROWSER"),
        _classified("t-browser", "booking"),
        _classified("t-case", "faq_question"),
        _event("faq.retrieval_completed", "t-browser", segment=0),
        _event("faq.retrieval_completed", "t-case", segment=0),
    )

    selected = _events(select_turn(_events(read_slice(log, 0)), "M-CASE"))

    assert {event["turn_id"] for event in selected} == {"t-case"}
    assert len(selected) == 3


def test_no_matching_turn_is_a_typed_missing_slice_not_an_empty_list(
    tmp_path: Path,
) -> None:
    log = tmp_path / "chat.log"
    _append(log, _received("t1", "M-OTHER"), _classified("t1", "small_talk"))

    result = select_turn(_events(read_slice(log, 0)), "M-CASE")

    assert isinstance(result, SliceMissing)
    assert result.reason is SliceMissingReason.NO_MATCHING_TURN


def test_two_matching_turns_are_a_typed_missing_slice(tmp_path: Path) -> None:
    log = tmp_path / "chat.log"
    _append(log, _received("t1", "M-CASE"), _received("t2", "M-CASE"))

    result = select_turn(_events(read_slice(log, 0)), "M-CASE")

    assert isinstance(result, SliceMissing)
    assert result.reason is SliceMissingReason.SEVERAL_MATCHING_TURNS


def test_an_empty_slice_selects_nothing_and_says_so(tmp_path: Path) -> None:
    log = tmp_path / "chat.log"
    _append(log, _UVICORN_ACCESS)

    result = select_turn(_events(read_slice(log, 0)), "M-CASE")

    assert isinstance(result, SliceMissing)
    assert result.reason is SliceMissingReason.NO_MATCHING_TURN


def test_a_trailing_partial_line_is_not_parsed(tmp_path: Path) -> None:
    log = tmp_path / "chat.log"
    _append(log, _received("t1", "M-CASE"))
    _append(log, '{"event": "faq.retrieval_compl', newline=False)

    events = _events(read_slice(log, 0))

    assert [event["event"] for event in events] == ["turn.message_received"]


def test_foreign_lines_are_skipped_and_every_turn_event_still_selected(
    tmp_path: Path,
) -> None:
    log = tmp_path / "chat.log"
    _append(
        log,
        _UVICORN_STARTUP,
        _received("t1", "M-CASE"),
        _CONSOLE_LINE,
        *_TRACEBACK,
        _classified("t1", "faq_question"),
        _UVICORN_ACCESS,
        "",
        _event("turn.completed", "t1"),
    )

    selected = _events(select_turn(_events(read_slice(log, 0)), "M-CASE"))

    assert [event["event"] for event in selected] == [
        "turn.message_received",
        "intent.classified",
        "turn.completed",
    ]


def test_a_brace_line_that_does_not_parse_makes_the_slice_unreadable(
    tmp_path: Path,
) -> None:
    log = tmp_path / "chat.log"
    _append(log, _received("t1", "M-CASE"), '{"event": "cut short', _event("x", "t1"))

    result = read_slice(log, 0)

    assert isinstance(result, SliceMissing)
    assert result.reason is SliceMissingReason.UNREADABLE


def test_a_json_object_without_an_event_name_makes_the_slice_unreadable(
    tmp_path: Path,
) -> None:
    log = tmp_path / "chat.log"
    _append(log, _received("t1", "M-CASE"), json.dumps({"turn_id": "t1"}))

    result = read_slice(log, 0)

    assert isinstance(result, SliceMissing)
    assert result.reason is SliceMissingReason.UNREADABLE


def test_a_log_shorter_than_the_offset_is_a_typed_missing_slice(
    tmp_path: Path,
) -> None:
    log = tmp_path / "chat.log"
    _append(log, _received("t1", "M-CASE"))
    offset = log_offset(log)
    log.write_text(_UVICORN_STARTUP + "\n")

    result = read_slice(log, offset)

    assert isinstance(result, SliceMissing)
    assert result.reason is SliceMissingReason.LOG_SHRANK


def test_a_json_log_after_the_offset_is_accepted(tmp_path: Path) -> None:
    log = tmp_path / "chat.log"
    _append(log, _UVICORN_STARTUP)
    offset = log_offset(log)
    _append(log, _UVICORN_ACCESS, _event("session.created"))

    require_json_log(log, offset)


def test_a_log_with_no_json_line_after_the_offset_is_refused(tmp_path: Path) -> None:
    log = tmp_path / "chat.log"
    _append(log, _event("from.an.earlier.process"))
    offset = log_offset(log)
    _append(log, _UVICORN_STARTUP, _UVICORN_ACCESS, _CONSOLE_LINE, *_TRACEBACK)

    with pytest.raises(JsonLogMissingError):
        require_json_log(log, offset)


def test_json_lines_without_an_event_name_do_not_satisfy_the_requirement(
    tmp_path: Path,
) -> None:
    log = tmp_path / "chat.log"
    _append(log, json.dumps({"level": "info"}), '{"event": "cut short')
    _append(log, _event("unterminated"), newline=False)

    with pytest.raises(JsonLogMissingError):
        require_json_log(log, 0)


def test_the_conditions_are_the_latest_settings_event_before_the_offset(
    tmp_path: Path,
) -> None:
    log = tmp_path / "chat.log"
    _append(log, json.dumps({**_CONFIGURED, "rerank_floor": 0.4}))
    _append(log, _UVICORN_STARTUP, json.dumps(_CONFIGURED), _event("other"))
    offset = log_offset(log)
    _append(log, json.dumps({**_CONFIGURED, "rerank_floor": 0.9}))

    conditions = service_conditions(log, offset)

    assert conditions["rerank_floor"] == 0.58
    assert {k: conditions[k] for k in _CONFIGURED} == _CONFIGURED


def test_no_settings_event_before_the_offset_is_refused(tmp_path: Path) -> None:
    log = tmp_path / "chat.log"
    _append(log, _UVICORN_STARTUP, _event("session.created"))
    offset = log_offset(log)
    _append(log, json.dumps(_CONFIGURED))

    with pytest.raises(ConditionsMissingError):
        service_conditions(log, offset)


def test_restarts_are_every_settings_event_inside_a_slice(tmp_path: Path) -> None:
    log = tmp_path / "chat.log"
    _append(log, json.dumps(_CONFIGURED))
    offset = log_offset(log)
    _append(
        log,
        _received("t1", "M-CASE"),
        json.dumps({**_CONFIGURED, "timestamp": "a"}),
        _UVICORN_STARTUP,
        json.dumps({**_CONFIGURED, "timestamp": "b"}),
    )

    restarts = restarts_in(_events(read_slice(log, offset)))

    assert [event["timestamp"] for event in restarts] == ["a", "b"]


def test_a_slice_without_a_restart_has_none(tmp_path: Path) -> None:
    log = tmp_path / "chat.log"
    _append(log, _received("t1", "M-CASE"))

    assert restarts_in(_events(read_slice(log, 0))) == []


def test_restarts_since_an_offset_are_read_to_the_last_complete_line(
    tmp_path: Path,
) -> None:
    log = tmp_path / "chat.log"
    _append(log, json.dumps({**_CONFIGURED, "timestamp": "before"}))
    offset = log_offset(log)
    _append(
        log,
        _UVICORN_ACCESS,
        "{not json",
        json.dumps({**_CONFIGURED, "timestamp": "a"}),
        _received("t1", "M-CASE"),
    )
    complete = log_offset(log)
    _append(log, json.dumps({**_CONFIGURED, "timestamp": "b"}), newline=False)

    restarts, checked = restarts_since(log, offset)

    assert [event["timestamp"] for event in restarts] == ["a"]
    assert checked == complete


def test_a_partial_line_left_unchecked_is_read_once_it_is_complete(
    tmp_path: Path,
) -> None:
    log = tmp_path / "chat.log"
    _append(log, json.dumps({**_CONFIGURED, "timestamp": "b"})[:20], newline=False)
    _restarts, checked = restarts_since(log, 0)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({**_CONFIGURED, "timestamp": "b"})[20:] + "\n")

    restarts, end = restarts_since(log, checked)

    assert checked == 0
    assert [event["timestamp"] for event in restarts] == ["b"]
    assert end == log_offset(log)


def test_a_log_shorter_than_the_checked_offset_is_read_from_its_start(
    tmp_path: Path,
) -> None:
    log = tmp_path / "chat.log"
    _append(log, *(_received(f"t{i}", f"M-{i}") for i in range(10)))
    offset = log_offset(log)
    log.write_text(json.dumps({**_CONFIGURED, "timestamp": "replaced"}) + "\n")
    assert log_offset(log) < offset

    restarts, checked = restarts_since(log, offset)

    assert [event["timestamp"] for event in restarts] == ["replaced"]
    assert checked == log_offset(log)


def test_the_produced_segmentation_is_read_from_the_classification_event(
    tmp_path: Path,
) -> None:
    log = tmp_path / "chat.log"
    _append(
        log,
        _received("t1", "M-CASE"),
        _classified("t1", "faq_question", "booking", cap_bound=True),
    )

    result = produced_segmentation(_events(read_slice(log, 0)))

    assert isinstance(result, ProducedSegmentation)
    assert [s.intent for s in result.segments] == [
        IntentLabel.FAQ_QUESTION,
        IntentLabel.BOOKING,
    ]
    assert [s.text for s in result.segments] == ["request 0", "request 1"]
    assert result.cap_bound is True


def test_no_classification_event_is_a_typed_missing_slice(tmp_path: Path) -> None:
    log = tmp_path / "chat.log"
    _append(log, _received("t1", "M-CASE"), _event("turn.silenced", "t1"))

    result = produced_segmentation(_events(read_slice(log, 0)))

    assert isinstance(result, SliceMissing)
    assert result.reason is SliceMissingReason.NO_CLASSIFICATION


def test_two_classification_events_are_a_typed_missing_slice(tmp_path: Path) -> None:
    log = tmp_path / "chat.log"
    _append(log, _classified("t1", "small_talk"), _classified("t1", "booking"))

    result = produced_segmentation(_events(read_slice(log, 0)))

    assert isinstance(result, SliceMissing)
    assert result.reason is SliceMissingReason.SEVERAL_CLASSIFICATIONS


# --- a log truncated in place and grown back past an offset (FR-047c) ----------------


def _truncate_in_place(path: Path, *lines: str) -> None:
    """Empty the file on its own inode, as `> "$log"` does, then write `lines`."""
    with path.open("r+b") as handle:
        handle.truncate(0)
    _append(path, *lines)


def _regrown_past(path: Path, offset: int, *first: str) -> None:
    """Truncate the log in place and write `first`, then foreign lines past `offset`."""
    _truncate_in_place(path, *first)
    while log_offset(path) <= offset:
        _append(path, _UVICORN_ACCESS)


def test_the_preceding_bytes_are_what_the_log_holds_just_before_the_offset(
    tmp_path: Path,
) -> None:
    log = tmp_path / "chat.log"
    _append(log, *(_received(f"t{i}", f"M-{i}") for i in range(10)))
    offset = log_offset(log)
    _append(log, _received("t10", "M-10"))

    preceding = preceding_bytes(log, offset)

    assert 0 < len(preceding) <= offset
    assert log.read_bytes()[:offset].endswith(preceding)
    assert preceding_bytes(log, 0) == b""


def test_restarts_since_a_matching_fingerprint_are_read_on_from_the_offset(
    tmp_path: Path,
) -> None:
    log = tmp_path / "chat.log"
    _append(log, json.dumps({**_CONFIGURED, "timestamp": "before"}))
    offset = log_offset(log)
    preceding = preceding_bytes(log, offset)
    _append(log, json.dumps({**_CONFIGURED, "timestamp": "after"}))

    restarts, checked = restarts_since(log, offset, preceding=preceding)

    assert [event["timestamp"] for event in restarts] == ["after"]
    assert checked == log_offset(log)


def test_a_log_truncated_in_place_and_grown_back_past_the_offset_is_read_from_its_start(
    tmp_path: Path,
) -> None:
    log = tmp_path / "chat.log"
    _append(log, *(_received(f"t{i}", f"M-{i}") for i in range(10)))
    offset = log_offset(log)
    preceding = preceding_bytes(log, offset)
    _regrown_past(log, offset, json.dumps({**_CONFIGURED, "timestamp": "replaced"}))
    assert log_offset(log) > offset

    restarts, checked = restarts_since(log, offset, preceding=preceding)

    assert [event["timestamp"] for event in restarts] == ["replaced"]
    assert checked == log_offset(log)


def test_a_slice_of_a_log_truncated_in_place_and_grown_back_is_a_typed_missing_slice(
    tmp_path: Path,
) -> None:
    log = tmp_path / "chat.log"
    _append(log, *(_received(f"t{i}", f"M-{i}") for i in range(10)))
    offset = log_offset(log)
    preceding = preceding_bytes(log, offset)
    _regrown_past(log, offset, _received("t1", "M-CASE"))

    result = read_slice(log, offset, preceding=preceding)

    assert isinstance(result, SliceMissing)
    assert result.reason is SliceMissingReason.LOG_REPLACED


def test_a_slice_with_a_matching_fingerprint_holds_the_lines_after_the_offset(
    tmp_path: Path,
) -> None:
    log = tmp_path / "chat.log"
    _append(log, _event("before", "t0"))
    offset = log_offset(log)
    preceding = preceding_bytes(log, offset)
    _append(log, _event("after", "t1"))

    names = [
        event["event"]
        for event in _events(read_slice(log, offset, preceding=preceding))
    ]

    assert names == ["after"]
