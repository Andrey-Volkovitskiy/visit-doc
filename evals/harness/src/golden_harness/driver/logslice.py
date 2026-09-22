"""Reading one turn's events out of the chat service's JSON log.

The log file is the chat process's whole stdout and stderr, so it holds more than the
shared structlog chain writes: uvicorn's startup and access lines and any traceback land
in it too. A line beginning with `{` is the chain's, and must parse as one JSON object
naming its `event`; every other line is foreign and skipped.

A case's slice is taken by byte offset - the file's size before the turn was posted,
read to the end once the turn has ended - and then narrowed to the one `turn_id` whose
`turn.message_received` names the case's own patient message. The offset is exact
against this run, which drives one case at a time; the `turn_id` selection is what makes
it exact against someone else using the same deployment meanwhile.

A slice that cannot be read or selected is a `SliceMissing` carrying why, never an empty
event list: an empty list would read as a turn that did nothing.
"""

import json
from collections.abc import Iterator
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

from golden_harness.record import ProducedSegmentation

type LogEvent = dict[str, JsonValue]

_SETTINGS_EVENT = "service.configured"
# A line lacking these bytes cannot be a settings event, so it is not parsed.
_SETTINGS_EVENT_BYTES = _SETTINGS_EVENT.encode()
_RECEIVED_EVENT = "turn.message_received"
_CLASSIFIED_EVENT = "intent.classified"
_TRACED_EVENT = "turn.traced"
# A turn logs `turn.traced` once, naming its trace id as a string.
_ONE_TRACE_ID: TypeAdapter[tuple[str]] = TypeAdapter(tuple[str])
_PRECEDING_BYTES = 256
# Every event the harness reads is logged at INFO, so a level above it empties the log
# of them as surely as the console format does.
_JSON_LOG_HINT = (
    "start the chat service with LOG_FORMAT=json, and LOG_LEVEL at INFO or below"
)


class SliceMissingReason(StrEnum):
    """Why no usable slice of a turn's events could be taken."""

    UNREADABLE = "unreadable"
    LOG_SHRANK = "log_shrank"
    LOG_REPLACED = "log_replaced"
    NO_MATCHING_TURN = "no_matching_turn"
    SEVERAL_MATCHING_TURNS = "several_matching_turns"
    NO_CLASSIFICATION = "no_classification"
    SEVERAL_CLASSIFICATIONS = "several_classifications"


class SliceMissing(BaseModel):
    """The typed absence of a turn's slice, or of the event a caller needed from it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: SliceMissingReason
    detail: str


class JsonLogMissingError(RuntimeError):
    """The log holds no line the service rendered as JSON; it is not running json."""


class ConditionsMissingError(RuntimeError):
    """The log holds no `service.configured` event to take a run's conditions from."""


def log_offset(path: Path) -> int:
    """Return the log file's current size in bytes - where the next line will start."""
    return path.stat().st_size


def preceding_bytes(path: Path, offset: int) -> bytes:
    """Return the bytes the log holds just before `offset`: up to the last 256 of them.

    Kept beside an offset, they tell the file the offset was taken in from one
    truncated in place and grown back past it, which the file's size alone cannot.
    Fewer bytes come back when the log is already shorter than `offset`, and they then
    match no log that has grown back past it.
    """
    start = max(offset - _PRECEDING_BYTES, 0)
    with path.open("rb") as handle:
        handle.seek(start)
        return handle.read(offset - start)


def read_slice(
    path: Path, since_offset: int, *, preceding: bytes | None = None
) -> list[LogEvent] | SliceMissing:
    """Parse every complete log line appended after `since_offset`.

    Args:
        preceding: the `preceding_bytes` taken with `since_offset`; when given, a log
            holding other bytes before the offset has been replaced since.

    Returns: the parsed events in log order, or a SliceMissing when a `{` line does not
        parse as a JSON object with an `event` name (`unreadable`), the file is now
        shorter than `since_offset` (`log_shrank`), or it holds other bytes before the
        offset than `preceding` (`log_replaced`). A replacement holding the very same
        bytes before the offset is not told apart.

    A trailing line with no newline yet is still being written and is not read.
    """
    if path.stat().st_size < since_offset:
        return SliceMissing(
            reason=SliceMissingReason.LOG_SHRANK,
            detail=f"the log is shorter than the offset {since_offset}",
        )
    if preceding is not None and preceding_bytes(path, since_offset) != preceding:
        return SliceMissing(
            reason=SliceMissingReason.LOG_REPLACED,
            detail=f"the log holds other bytes before the offset {since_offset}",
        )
    events: list[LogEvent] = []
    for number, line in enumerate(_complete_lines(path, since_offset, None), start=1):
        if not line.startswith(b"{"):
            continue
        event = _parse(line)
        if event is None:
            return SliceMissing(
                reason=SliceMissingReason.UNREADABLE,
                detail=f"line {number} after offset {since_offset} is not a log event",
            )
        events.append(event)
    return events


def group_by_turn(events: list[LogEvent]) -> dict[str, list[LogEvent]]:
    """Group events by their `turn_id`, keeping log order within each group.

    Returns: each turn id mapped to that turn's events; an event carrying no string
        `turn_id` belongs to no group.
    """
    groups: dict[str, list[LogEvent]] = {}
    for event in events:
        turn_id = event.get("turn_id")
        if isinstance(turn_id, str):
            groups.setdefault(turn_id, []).append(event)
    return groups


def select_turn(
    events: list[LogEvent], patient_message_id: str
) -> list[LogEvent] | SliceMissing:
    """Select the one turn that received the case's own patient message.

    Returns: that turn's events in log order, or a SliceMissing when no turn's
        `turn.message_received` lists `patient_message_id` in `message_ids_unified`
        (`no_matching_turn`) or more than one does (`several_matching_turns`).
    """
    matching = [
        group
        for group in group_by_turn(events).values()
        if any(_received(event, patient_message_id) for event in group)
    ]
    if not matching:
        return SliceMissing(
            reason=SliceMissingReason.NO_MATCHING_TURN,
            detail=f"no turn received message {patient_message_id}",
        )
    if len(matching) > 1:
        return SliceMissing(
            reason=SliceMissingReason.SEVERAL_MATCHING_TURNS,
            detail=f"{len(matching)} turns received message {patient_message_id}",
        )
    return matching[0]


def require_json_log(path: Path, since_offset: int) -> None:
    """Confirm the service writes JSON lines: one complete event after `since_offset`.

    Raises: JsonLogMissingError when no complete line after `since_offset` parses as a
        JSON object carrying an `event` name.
    """
    for line in _complete_lines(path, since_offset, None):
        if line.startswith(b"{") and _parse(line) is not None:
            return
    raise JsonLogMissingError(
        f"{path} holds no JSON log event after byte {since_offset}; {_JSON_LOG_HINT}"
    )


def service_conditions(path: Path, before_offset: int) -> LogEvent:
    """Return the latest `service.configured` event written before `before_offset`.

    Returns: that event's fields, envelope included.

    A `{` line that does not parse is skipped here rather than refusing the whole
    history before the run: it is some earlier process's, and cannot be a settings
    event anyone could read.

    Raises: ConditionsMissingError when no such event precedes `before_offset`.
    """
    latest: LogEvent | None = None
    any_event = False
    for line in _complete_lines(path, 0, before_offset):
        if not line.startswith(b"{"):
            continue
        # Once one line has shown the log holds JSON events, only a line that could be a
        # settings event is parsed: the whole history before the run is read here.
        if any_event and _SETTINGS_EVENT_BYTES not in line:
            continue
        event = _parse(line)
        any_event = any_event or event is not None
        if event is not None and event["event"] == _SETTINGS_EVENT:
            latest = event
    if latest is None:
        # A service logging for people writes no JSON line at all, and this is the
        # first check a run makes - so the fix is named here, not only by
        # `require_json_log`, which a console log never gets as far as. One logging
        # JSON above INFO writes other events but never this one.
        hint = (
            f"; {_SETTINGS_EVENT} is logged at INFO, so check LOG_LEVEL"
            if any_event
            else f"; {_JSON_LOG_HINT}"
        )
        raise ConditionsMissingError(
            f"{path} holds no {_SETTINGS_EVENT} event before byte {before_offset}{hint}"
        )
    return latest


def restarts_since(
    path: Path, offset: int, *, preceding: bytes | None = None
) -> tuple[list[LogEvent], int]:
    """Read every `service.configured` event in the complete lines after `offset`.

    Args:
        preceding: the `preceding_bytes` taken with `offset`; when given, a log holding
            other bytes before the offset has been replaced since.

    Returns: those events in log order, and the offset just past the last complete line
        read - where the next read has to start for no line to go unread. A trailing
        line with no newline yet is left for that next read.

    A `{` line that does not parse is skipped, as `service_conditions` skips one: it
    cannot be a settings event anyone could read. A log now shorter than `offset`, or
    holding other bytes before it than `preceding`, has been replaced since it was last
    read, so all of it is read. A replacement holding the very same bytes before the
    offset is not told apart.
    """
    replaced = path.stat().st_size < offset or (
        preceding is not None and preceding_bytes(path, offset) != preceding
    )
    start = 0 if replaced else offset
    with path.open("rb") as handle:
        handle.seek(start)
        data = handle.read()
    *complete, unterminated = data.split(b"\n")
    restarts: list[LogEvent] = []
    for line in complete:
        if not line.startswith(b"{") or _SETTINGS_EVENT_BYTES not in line:
            continue
        event = _parse(line)
        if event is not None and event["event"] == _SETTINGS_EVENT:
            restarts.append(event)
    return restarts, start + len(data) - len(unterminated)


def produced_segmentation(
    events: list[LogEvent],
) -> ProducedSegmentation | SliceMissing:
    """Read the turn's produced segmentation from its `intent.classified` event.

    Returns: the segments and `cap_bound`, or a SliceMissing when the turn has no
        classification event (`no_classification`) or several
        (`several_classifications`).

    Raises: pydantic.ValidationError when the event's segments break the record's
        shape - a disagreement with the log contract, reported rather than excluded.
    """
    classified = [event for event in events if event["event"] == _CLASSIFIED_EVENT]
    if not classified:
        return SliceMissing(
            reason=SliceMissingReason.NO_CLASSIFICATION,
            detail=f"the turn logged no {_CLASSIFIED_EVENT}",
        )
    if len(classified) > 1:
        return SliceMissing(
            reason=SliceMissingReason.SEVERAL_CLASSIFICATIONS,
            detail=f"the turn logged {len(classified)} {_CLASSIFIED_EVENT} events",
        )
    event = classified[0]
    return ProducedSegmentation.model_validate(
        {"segments": event.get("segments"), "cap_bound": event.get("cap_bound")}
    )


def trace_id(events: list[LogEvent]) -> str | None:
    """Read the id of the trace the turn exported from its `turn.traced` event.

    Returns: the trace id, or None when the turn logged no `turn.traced` - which is
        what a turn that exported no trace looks like, and nothing else does.

    Raises: pydantic.ValidationError when the turn logged the event more than once, or
        without a string `trace_id` - a disagreement with the log contract, reported
        as a segmentation breaking the record's shape is.
    """
    traced = [
        event.get("trace_id") for event in events if event["event"] == _TRACED_EVENT
    ]
    if not traced:
        return None
    (only,) = _ONE_TRACE_ID.validate_python(traced)
    return only


def _complete_lines(path: Path, start: int, end: int | None) -> Iterator[bytes]:
    """Yield each newline-terminated line between two byte offsets, without its newline.

    A final segment with no newline is still being written, and is not yielded.
    """
    with path.open("rb") as handle:
        handle.seek(start)
        data = handle.read() if end is None else handle.read(max(end - start, 0))
    *complete, _unterminated = data.split(b"\n")
    yield from complete


def _parse(line: bytes) -> LogEvent | None:
    """Parse a `{` line as a log event, or return None when it is not one."""
    try:
        parsed = json.loads(line)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("event"), str):
        return None
    event: LogEvent = parsed
    return event


def _received(event: LogEvent, patient_message_id: str) -> bool:
    """Return whether `event` is the turn receiving `patient_message_id`."""
    if event["event"] != _RECEIVED_EVENT:
        return False
    unified = event.get("message_ids_unified")
    return isinstance(unified, list) and patient_message_id in unified
