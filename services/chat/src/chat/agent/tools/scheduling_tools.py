"""The scheduling capabilities, as tools.

Each handler is a thin adapter over `clients/scheduling.py`: it translates the model's
arguments into a domain call, and the domain result into a small object the model can
read. Every refusal reason maps to one fixed, handler-authored sentence, so the model
rephrases a cause it cannot invent.
"""

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from functools import wraps
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from shared_models.localtime import (
    format_local_datetime,
    parse_local_date,
    parse_local_datetime,
)
from shared_models.scheduling import BookingFailureReason, NotFoundEntity

from chat.agent.tools.registry import (
    Tool,
    ToolArgumentError,
    ToolContext,
    ToolResult,
    required_argument,
)
from chat.clients import scheduling
from chat.clients.scheduling import (
    BookingRefusal,
    SchedulingNotFoundError,
    SchedulingRequestError,
    SchedulingUnavailableError,
)
from chat.core.logging import get_logger

# Namespace for the derived booking key. Fixed, so the same booking always derives the
# same key across processes and restarts.
_BOOKING_NAMESPACE = uuid5(NAMESPACE_URL, "https://visitdoc.local/booking")

# A closed, empty schema: the tool takes nothing, and the model cannot smuggle an
# ambient argument in beside it.
_NO_ARGUMENTS: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_UNAVAILABLE_EXPLANATION = "Booking is temporarily unavailable. Nothing was booked."

# Used when the scheduler stopped answering during a *write* and cannot confirm what it
# did. Claiming nothing was booked would be a guess, and the one guess that turns into
# the patient booking a second appointment they cannot cancel.
_OUTCOME_UNKNOWN_EXPLANATION = (
    "The booking service stopped responding, so it is not known whether that "
    "appointment was created. Do not say it was booked, and do not book it again - "
    "tell the patient to check with the clinic before trying again."
)

# One sentence per id that can fail to resolve. The scheduler names which one, so the
# handler never has to pick a cause - naming the practitioner for what was really a
# missing patient record is how the assistant ends up denying that a real, listed
# practitioner works here.
_NOT_FOUND_EXPLANATIONS = {
    NotFoundEntity.PRACTITIONER: "That practitioner is not one of this clinic's.",
    NotFoundEntity.PATIENT: (
        "This chat has no patient record, so no times could be checked."
    ),
    NotFoundEntity.CHAT: (
        "This chat has no patient record, so no times could be checked."
    ),
}

# Used when the scheduler named an entity this build has no member for. It says only
# what is known - the lookup failed - rather than blaming one of the possibilities.
_UNRESOLVED_EXPLANATION = (
    "Something in that request could not be looked up. Do not guess which part, and do "
    "not offer an alternative - ask the patient to start again."
)

# `list_my_appointments` cannot be answered at all without a patient record, and an
# empty list would read as "you have nothing booked" - which is not known here.
_NO_PATIENT_LIST_EXPLANATION = (
    "This chat has no patient record, so its appointments could not be looked up. Do "
    "not say the patient has none."
)

# One sentence per refusal reason. The set is closed and the scheduler picks exactly one
# per attempt, so this mapping is total and a refusal is reproducible from the request.
_EXPLANATION_BY_REASON = {
    BookingFailureReason.PRACTITIONER_BUSY: (
        "That time was taken while we were talking."
    ),
    BookingFailureReason.PATIENT_BUSY: (
        "You already have an appointment that overlaps that time."
    ),
    BookingFailureReason.OUTSIDE_SCHEDULE: (
        "That time is outside the hours this practitioner sees patients."
    ),
    BookingFailureReason.OFF_GRID: (
        "Appointments start at fixed times, and that is not one of them."
    ),
    BookingFailureReason.IN_PAST: "That time has already passed.",
    BookingFailureReason.BEYOND_HORIZON: (
        "That is further ahead than appointments can be booked."
    ),
    BookingFailureReason.PRACTITIONER_NOT_FOUND: (
        "That practitioner is not one of this clinic's."
    ),
    BookingFailureReason.PATIENT_NOT_FOUND: (
        "This chat has no patient record, so nothing could be booked."
    ),
}


def derive_idempotency_key(
    patient_id: str, practitioner_id: str, starts_at: datetime
) -> str:
    """Derive the booking key from exactly the fields the scheduler checks it against.

    The same booking always produces the same key, so a lost confirmation replays rather
    than colliding with the patient's own appointment; different bookings never share
    one. Derived rather than random, and never a tool parameter, so a model can neither
    weaken it nor reuse one.
    """
    seed = f"{patient_id}|{practitioner_id}|{format_local_datetime(starts_at)}"
    return str(uuid5(_BOOKING_NAMESPACE, seed))


def _required_date(arguments: dict[str, Any], name: str) -> date:
    """Read `name` from a model-supplied arguments dict as a local date.

    Raises: ToolArgumentError if `name` is absent or is not a `YYYY-MM-DD` date.
    """
    value = required_argument(arguments, name)
    try:
        return parse_local_date(value)
    except ValueError as exc:
        raise ToolArgumentError(
            f"{name} must be a local date, YYYY-MM-DD, not {value!r}"
        ) from exc


def _required_datetime(arguments: dict[str, Any], name: str) -> datetime:
    """Read `name` from a model-supplied arguments dict as a local date-time.

    Raises: ToolArgumentError if `name` is absent or is not a
        `YYYY-MM-DDTHH:MM:SS` date-time.
    """
    value = required_argument(arguments, name)
    try:
        return parse_local_datetime(value)
    except ValueError as exc:
        raise ToolArgumentError(
            f"{name} must be a local date-time, YYYY-MM-DDTHH:MM:SS, not {value!r}"
        ) from exc


def _unavailable() -> ToolResult:
    """The result every path takes when nothing could be created."""
    return {"status": "unavailable", "explanation": _UNAVAILABLE_EXPLANATION}


def _reports_unavailable(
    handler: Callable[[ToolContext, dict[str, Any]], Awaitable[ToolResult]],
) -> Callable[[ToolContext, dict[str, Any]], Awaitable[ToolResult]]:
    """Turn an unreachable scheduler into the model-readable `unavailable` result.

    For the read-only tools only, and written once here rather than as a `try` in each
    of them: a failed read wrote nothing by construction, so there is exactly one thing
    to say about it. `book_appointment` is deliberately not wrapped - a write that may
    or may not have landed has a second thing to say, and answers for itself.
    """

    @wraps(handler)
    async def _wrapper(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        try:
            return await handler(context, arguments)
        except SchedulingUnavailableError:
            return _unavailable()

    return _wrapper


def _outcome_unknown() -> ToolResult:
    """The result for a write whose fate the scheduler could not confirm."""
    return {"status": "unavailable", "explanation": _OUTCOME_UNKNOWN_EXPLANATION}


# Which refusal reason to report per unresolved entity, so the model reads a cause it
# already knows how to explain rather than a second vocabulary for the same facts.
_REASON_BY_ENTITY = {
    NotFoundEntity.PRACTITIONER: BookingFailureReason.PRACTITIONER_NOT_FOUND,
    NotFoundEntity.PATIENT: BookingFailureReason.PATIENT_NOT_FOUND,
    NotFoundEntity.CHAT: BookingFailureReason.PATIENT_NOT_FOUND,
}


def _not_found(exc: SchedulingNotFoundError) -> ToolResult:
    """Render an id that did not resolve, naming the one the scheduler named.

    An entity this build cannot name is reported as `unavailable` rather than as a
    refusal: a refusal is something the patient can act on by choosing differently, and
    an unknown cause is not.
    """
    if exc.entity is None:
        return {"status": "unavailable", "explanation": _UNRESOLVED_EXPLANATION}
    return {
        "status": "refused",
        "reason": _REASON_BY_ENTITY[exc.entity].value,
        "explanation": _NOT_FOUND_EXPLANATIONS[exc.entity],
    }


def _refused(refusal: BookingRefusal) -> ToolResult:
    """Render one evaluated refusal for the model."""
    return {
        "status": "refused",
        "reason": refusal.reason.value,
        "explanation": _EXPLANATION_BY_REASON[refusal.reason],
    }


async def list_practitioners(
    context: ToolContext, _arguments: dict[str, Any]
) -> ToolResult:
    """Return the session's practitioners, each marked bookable or not."""
    practitioners = await scheduling.list_practitioners(
        context.channel, context.settings, session_id=context.session_id
    )
    return {
        "practitioners": [
            {
                "id": p.id,
                "full_name": p.full_name,
                "specialty": p.specialty,
                "appointment_duration_minutes": p.appointment_duration_minutes,
                "bookable": p.bookable,
            }
            for p in practitioners
        ]
    }


async def check_availability(
    context: ToolContext, arguments: dict[str, Any]
) -> ToolResult:
    """Return the start times this patient can book with one practitioner.

    The registry guarantees a patient record before this runs, so `patient_id` is never
    None here.
    """
    assert context.patient_id is not None

    practitioner_id = required_argument(arguments, "practitioner_id")
    from_date = _required_date(arguments, "from_date")
    to_date = _required_date(arguments, "to_date")
    try:
        result = await scheduling.check_availability(
            context.channel,
            context.settings,
            session_id=context.session_id,
            practitioner_id=practitioner_id,
            patient_id=context.patient_id,
            from_date=from_date,
            to_date=to_date,
            local_now=context.local_now,
        )
    except SchedulingNotFoundError as exc:
        # Distinct from an empty result, which means this practitioner exists and has
        # nothing free - offering to look further ahead for an id that does not resolve
        # would never return anything.
        return _not_found(exc)

    return {
        "available_starts": [format_local_datetime(s) for s in result.available_starts],
        "appointment_duration_minutes": result.appointment_duration_minutes,
        "truncated": result.truncated,
    }


async def book_appointment(
    context: ToolContext, arguments: dict[str, Any]
) -> ToolResult:
    """Create one real appointment, or explain why it was refused.

    An `unavailable` result means either that nothing was created, or - when the
    scheduler stopped answering mid-write - that its fate is genuinely unknown, which
    the explanation says so the model neither confirms nor re-attempts the booking. The
    key mismatch case is always "nothing was created": it can only be a defect in this
    service's own key derivation, never a conflict the patient could resolve.

    The registry guarantees a patient record before this runs, so `patient_id` is never
    None here.
    """
    assert context.patient_id is not None

    practitioner_id = required_argument(arguments, "practitioner_id")
    starts_at = _required_datetime(arguments, "starts_at")
    try:
        outcome = await scheduling.book_appointment(
            context.channel,
            context.settings,
            session_id=context.session_id,
            patient_id=context.patient_id,
            practitioner_id=practitioner_id,
            starts_at=starts_at,
            local_now=context.local_now,
            idempotency_key=derive_idempotency_key(
                context.patient_id, practitioner_id, starts_at
            ),
        )
    except SchedulingUnavailableError as exc:
        if exc.outcome_unknown:
            get_logger().error(
                "booking.outcome_unknown",
                practitioner_id=practitioner_id,
                starts_at=format_local_datetime(starts_at),
                error_detail=str(exc),
            )
            return _outcome_unknown()
        return _unavailable()
    except SchedulingRequestError as exc:
        get_logger().error(
            "booking.key_derivation_rejected",
            practitioner_id=practitioner_id,
            error_detail=str(exc),
        )
        return _unavailable()

    if isinstance(outcome, BookingRefusal):
        return _refused(outcome)

    appointment = outcome.appointment
    return {
        "status": "booked",
        "appointment": {
            "id": appointment.id,
            "practitioner_full_name": appointment.practitioner_full_name,
            "starts_at": format_local_datetime(appointment.starts_at),
            "ends_at": format_local_datetime(appointment.ends_at),
        },
    }


async def list_my_appointments(
    context: ToolContext, _arguments: dict[str, Any]
) -> ToolResult:
    """Return this patient's upcoming appointments, earliest first.

    An empty list is an explicit "nothing upcoming", not an error - a patient the
    scheduler cannot resolve is reported as its own result instead, so the model never
    reads "no appointments" off a lookup that never happened.

    The registry guarantees a patient record before this runs, so `patient_id` is never
    None here.
    """
    assert context.patient_id is not None
    try:
        appointments = await scheduling.list_upcoming_appointments(
            context.channel,
            context.settings,
            session_id=context.session_id,
            patient_id=context.patient_id,
            local_now=context.local_now,
        )
    except SchedulingNotFoundError as exc:
        get_logger().error(
            "appointments.patient_unresolved",
            patient_id=context.patient_id,
            entity=exc.entity.value if exc.entity else None,
        )
        return {"status": "unavailable", "explanation": _NO_PATIENT_LIST_EXPLANATION}
    return {
        "appointments": [
            {
                "practitioner_full_name": a.practitioner_full_name,
                "specialty": a.practitioner_specialty,
                "starts_at": format_local_datetime(a.starts_at),
                "ends_at": format_local_datetime(a.ends_at),
            }
            for a in appointments
        ]
    }


SCHEDULING_TOOLS = [
    Tool(
        name="list_practitioners",
        description=(
            "Lists the clinic's practitioners with their specialties. Call this when "
            "the patient asks who is available, or before offering appointment times "
            "when they named a specialty rather than a person. A practitioner marked "
            "bookable=false has no times to offer at all - say so rather than "
            "presenting an empty list of times."
        ),
        input_schema=_NO_ARGUMENTS,
        handler=_reports_unavailable(list_practitioners),
    ),
    Tool(
        name="check_availability",
        description=(
            "Returns bookable start times for one practitioner over a date range. Only "
            "these times can be booked. Never offer a time this tool did not return. "
            "If truncated is true, the list is not the practitioner's whole "
            "availability - offer to look further ahead."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "practitioner_id": {"type": "string"},
                "from_date": {
                    "type": "string",
                    "description": "local date, YYYY-MM-DD, inclusive",
                },
                "to_date": {
                    "type": "string",
                    "description": "local date, YYYY-MM-DD, inclusive",
                },
            },
            "required": ["practitioner_id", "from_date", "to_date"],
            "additionalProperties": False,
        },
        handler=_reports_unavailable(check_availability),
        requires_patient=True,
    ),
    Tool(
        name="book_appointment",
        description=(
            "Creates a REAL appointment. Only call this after the patient has "
            "explicitly confirmed both the practitioner and the exact start time. "
            "There is no way to cancel or change an appointment in this version, so "
            "never call it to 'check' whether something is possible - use "
            "check_availability for that."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "practitioner_id": {"type": "string"},
                "starts_at": {
                    "type": "string",
                    "description": "local date-time, YYYY-MM-DDTHH:MM:SS",
                },
            },
            "required": ["practitioner_id", "starts_at"],
            "additionalProperties": False,
        },
        handler=book_appointment,
        requires_patient=True,
        writes=True,
    ),
    Tool(
        name="list_my_appointments",
        description=(
            "Lists this patient's upcoming appointments, earliest first. Past "
            "appointments are not available."
        ),
        input_schema=_NO_ARGUMENTS,
        handler=_reports_unavailable(list_my_appointments),
        requires_patient=True,
    ),
]
