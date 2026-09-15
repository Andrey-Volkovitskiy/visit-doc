"""The golden set's labels: loading them, choosing what to run, and fingerprinting them.

`cases.json` is validated against `schema.json` before anything reads it as a model, so
a malformed label fails here, as a label, naming its case - not later, as a scoring
result nobody can explain. The schema decides the shape; the models below only type it.
"""

import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from datetime import time as wall_time
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from chat.domain.schemas import IntentLabel
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from scheduler.core.config import Settings as SchedulerSettings
from scheduler.domain.availability import DailyRange, Interval, validate_start
from scheduler.domain.name_pools import PHYSICIAN_POOL
from scheduler.repositories.practitioner_repository import SESSION_SEED
from shared_models.scheduling import AppointmentStatus

# The practitioners a fresh session is seeded with, by the pool name each is given: the
# seed is created in order from the top of the pool.
SEEDED_PRACTITIONER_NAMES: Final = tuple(PHYSICIAN_POOL[: len(SESSION_SEED)])
_SEED_BY_NAME: Final = dict(zip(SEEDED_PRACTITIONER_NAMES, SESSION_SEED, strict=True))
_BOOKING_HORIZON_DAYS: Final[int] = SchedulerSettings.model_fields[
    "BOOKING_HORIZON_DAYS"
].default


class LabelError(ValueError):
    """The file is not a valid golden set; the message names the cases at fault."""


class SelectionError(ValueError):
    """A case selection named something the set does not hold, or nothing at all."""


class HistoryRole(StrEnum):
    """Who wrote one prior turn of a case's conversation."""

    USER = "user"
    ASSISTANT = "assistant"


class BookingTool(StrEnum):
    """The scheduling tools a booking request's label may require."""

    LIST_PRACTITIONERS = "list_practitioners"
    CHECK_AVAILABILITY = "check_availability"
    BOOK_APPOINTMENT = "book_appointment"
    LIST_MY_APPOINTMENTS = "list_my_appointments"
    RESCHEDULE_APPOINTMENT = "reschedule_appointment"
    CANCEL_APPOINTMENT = "cancel_appointment"


class HistoryEntry(BaseModel):
    """One prior turn planted before a case's message, oldest first."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: HistoryRole
    text: str


class LabelledRequest(BaseModel):
    """One request of a case's expected segmentation, at its position in `requests`.

    `gist` is a human reference for what the request asks and is never scored or
    compared to produced text anywhere: a segment has many valid restatements.

    `answerable` and `cites` are set exactly on a `faq_question` request, and `tools`
    exactly on a `booking` one - the schema enforces both before this model is built.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: IntentLabel
    gist: str
    answerable: bool | None = None
    cites: list[str] | None = None
    tools: list[BookingTool] | None = None


class AppointmentRef(BaseModel):
    """One appointment of a scheduling fixture, stated relative to the run clock.

    `day` is a signed day offset from the clock's date (`+3d`). `time` is None when
    any start that day matches. `status` is None on a precondition, which is always
    planted standing, and set on an expectation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    practitioner: str = Field(min_length=1)
    day: str = Field(pattern=r"^[+-][0-9]+d$")
    time: str | None = Field(default=None, pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    status: AppointmentStatus | None = None

    def date_on(self, clock: datetime) -> date:
        """Return the calendar date this entry names on `clock`."""
        return clock.date() + timedelta(days=int(self.day[:-1]))

    def start_time(self) -> wall_time | None:
        """Return the start time this entry names, or None when it names none."""
        if self.time is None:
            return None
        return wall_time.fromisoformat(self.time)


class SchedulingFixture(BaseModel):
    """What a case is expected to leave behind in the scheduler.

    `given` is planted before the turn; `expect` is the complete set of the patient's
    appointments after the case's last turn. `reply` is the patient's scripted answer
    to what the first turn asked, posted as a second turn in the same chat, and None on
    a case driven as one turn; with it, the appointments after the first turn must
    still be `given`, all standing, and `expect` is read after the reply.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    given: list[AppointmentRef]
    expect: list[AppointmentRef]
    reply: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _a_status_exactly_on_an_expectation(self) -> "SchedulingFixture":
        """Refuse a precondition with a status or no time, or a bare expectation."""
        if any(entry.status is not None or entry.time is None for entry in self.given):
            raise ValueError("a given entry must carry a time and no status")
        if any(entry.status is None for entry in self.expect):
            raise ValueError("an expect entry must carry a status")
        return self


class Case(BaseModel):
    """One labelled patient message.

    Keys the schema admits but this model does not yet type are kept rather than
    dropped, so a field added to the schema is still carried into `label_digests` - the
    schema, validated first, is what refuses a key nobody declared.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    id: str
    family: str
    message: str
    requests: list[LabelledRequest]
    source: str
    note: str | None = None
    history: list[HistoryEntry] | None = None
    scheduling: SchedulingFixture | None = None

    @property
    def has_booking_request(self) -> bool:
        """Whether any labelled request of the case is a booking."""
        return any(r.intent is IntentLabel.BOOKING for r in self.requests)


class SelectionKind(StrEnum):
    """How a run's cases were chosen."""

    ALL = "all"
    IDS = "ids"
    FAMILY = "family"


class Selection(BaseModel):
    """The cases a run covers, in file order, and how they were chosen.

    `family` is set exactly when `kind` is `family`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SelectionKind
    family: str | None = None
    case_ids: list[str]

    @model_validator(mode="after")
    def _family_follows_the_kind(self) -> "Selection":
        """Refuse a family unless the selection was chosen by family, and vice versa."""
        if (self.kind is SelectionKind.FAMILY) != (self.family is not None):
            raise ValueError("a selection names a family exactly when chosen by family")
        return self


def load_cases(path: Path, schema_path: Path | None = None) -> list[Case]:
    """Load the golden set, validating it against its JSON schema first.

    Args:
        schema_path: the schema to validate against; `schema.json` beside `path` when
            omitted.

    Raises: LabelError when the file breaks the schema, repeats a case id, or carries a
        scheduling fixture on a case other than exactly those with a booking request;
        the message names every offending case.
    """
    schema_file = (
        schema_path if schema_path is not None else path.with_name("schema.json")
    )
    schema = json.loads(schema_file.read_text(encoding="utf-8"))
    raw = json.loads(path.read_text(encoding="utf-8"))

    problems = [
        f"{_case_named_by(raw, list(error.absolute_path))}: "
        f"{'/'.join(str(part) for part in error.absolute_path)}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema).iter_errors(raw),
            key=lambda error: [str(part) for part in error.absolute_path],
        )
    ]
    if problems:
        raise LabelError(
            "cases.json does not match its schema:\n" + "\n".join(problems)
        )

    cases = [Case.model_validate(entry) for entry in raw]
    repeated = sorted(
        case_id
        for case_id, count in Counter(case.id for case in cases).items()
        if count > 1
    )
    if repeated:
        raise LabelError(f"case ids appear more than once: {', '.join(repeated)}")
    misplaced = [
        case.id
        for case in cases
        if case.has_booking_request != (case.scheduling is not None)
    ]
    if misplaced:
        raise LabelError(
            "a scheduling fixture belongs on exactly the cases with a booking request: "
            + ", ".join(misplaced)
        )
    return cases


def validate_plantable(fixture: SchedulingFixture, clock: datetime) -> None:
    """Check that every precondition of `fixture` would be planted on `clock`.

    Raises: LabelError naming the first entry that names a practitioner a fresh session
        is not seeded with, starts at a time the scheduler would refuse - its refusal
        reason in the message - or overlaps another precondition of the same
        practitioner.

    Each start is judged by the scheduler's own booking predicates against the seeded
    practitioner's schedule and the scheduler's default horizon. An expectation's
    practitioner must be seeded too, since an entry naming anyone else can never match.
    """
    for entry in fixture.expect:
        if entry.practitioner not in _SEED_BY_NAME:
            raise LabelError(f"expects {entry.practitioner}, who is not seeded")

    planted: list[tuple[str, Interval]] = []
    for entry in fixture.given:
        seed = _SEED_BY_NAME.get(entry.practitioner)
        if seed is None:
            raise LabelError(f"plants with {entry.practitioner}, who is not seeded")
        clock_time = entry.start_time()
        assert clock_time is not None
        start = datetime.combine(entry.date_on(clock), clock_time)
        refusal = validate_start(
            start,
            schedule=[
                DailyRange(day, begins, ends) for day, begins, ends in seed.schedule
            ],
            duration_minutes=seed.appointment_duration_minutes,
            local_now=clock,
            horizon_days=_BOOKING_HORIZON_DAYS,
        )
        if refusal is not None:
            raise LabelError(
                f"{entry.practitioner} {entry.day} {entry.time} would be refused: "
                f"{refusal.value}"
            )
        slot = Interval(
            start, start + timedelta(minutes=seed.appointment_duration_minutes)
        )
        for name, other in planted:
            if name == entry.practitioner and slot.overlaps(other):
                raise LabelError(
                    f"{entry.practitioner} {entry.day} {entry.time} would overlap "
                    "another precondition"
                )
        planted.append((entry.practitioner, slot))


def _case_named_by(raw: Any, path: list[str | int]) -> str:
    """Return the id of the case a validation error's path points into."""
    if not path or not isinstance(raw, list) or not isinstance(path[0], int):
        return "<file>"
    entry = raw[path[0]]
    if isinstance(entry, dict) and isinstance(entry.get("id"), str):
        return str(entry["id"])
    return f"<case at index {path[0]}>"


def select(
    cases: Sequence[Case],
    ids: Sequence[str] | None = None,
    family: str | None = None,
) -> Selection:
    """Choose the cases a run covers: those ids, that family, or - given neither - all.

    The selection is always in file order, whatever order `ids` names them in.

    Raises: SelectionError when both `ids` and `family` are given, when `ids` is empty
        or names a case the set does not hold, or when no case has `family`.
    """
    if ids is not None and family is not None:
        raise SelectionError("select by ids or by family, not both")

    if ids is not None:
        wanted = set(ids)
        if not wanted:
            raise SelectionError("an id selection must name at least one case")
        known = {case.id for case in cases}
        unknown = sorted(wanted - known)
        if unknown:
            raise SelectionError(f"no such case ids: {', '.join(unknown)}")
        return Selection(
            kind=SelectionKind.IDS,
            case_ids=[case.id for case in cases if case.id in wanted],
        )

    if family is not None:
        chosen = [case.id for case in cases if case.family == family]
        if not chosen:
            raise SelectionError(f"no case belongs to family {family!r}")
        return Selection(kind=SelectionKind.FAMILY, family=family, case_ids=chosen)

    return Selection(kind=SelectionKind.ALL, case_ids=[case.id for case in cases])


def label_digests(cases: Sequence[Case]) -> dict[str, str]:
    """Fingerprint each case over the fields scoring reads.

    Returns: each case id mapped to the hex sha256 of its scored fields, in case order.

    The digest covers every field of a case but `gist`, `note`, `source` and `family`
    - today `id`, `message`, `history`, each request's `intent`, `answerable`, `cites`
    and `tools`, and `scheduling` - so a corrected note leaves a run re-scorable while a
    changed intent does not. The fields left out are named rather than the ones kept,
    so a field added to the schema is fingerprinted from the start instead of changing
    unnoticed. The fields are serialized as canonical JSON (sorted keys, no
    whitespace), and a field a case does not carry is absent from it rather than null.
    """
    return {case.id: _digest(_scored_fields(case)) for case in cases}


# The fields no scorer reads: a human reference, provenance and grouping.
_UNSCORED_CASE_FIELDS: Final = frozenset({"note", "source", "family"})
_UNSCORED_REQUEST_FIELDS: Final = frozenset({"gist"})


def _scored_fields(case: Case) -> dict[str, JsonValue]:
    """Return the part of a case scoring reads, as plain JSON data."""
    dumped = case.model_dump(mode="json", exclude_none=True)
    scored: dict[str, JsonValue] = {
        key: value for key, value in dumped.items() if key not in _UNSCORED_CASE_FIELDS
    }
    scored["requests"] = [
        {
            key: value
            for key, value in request.items()
            if key not in _UNSCORED_REQUEST_FIELDS
        }
        for request in dumped["requests"]
    ]
    return scored


def _digest(fields: dict[str, JsonValue]) -> str:
    """Return the hex sha256 of `fields` rendered as canonical JSON."""
    canonical = json.dumps(
        fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
