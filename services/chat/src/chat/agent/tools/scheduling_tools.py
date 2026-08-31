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
from shared_models.scheduling import (
    BookingFailureReason,
    ChangeFailureReason,
    NotFoundEntity,
    StatusFilter,
    TimeFilter,
)

from chat.agent.tools.registry import (
    Tool,
    ToolArgumentError,
    ToolContext,
    ToolResult,
    optional_id_argument,
    required_argument,
    required_id_argument,
)
from chat.clients import scheduling
from chat.clients.scheduling import (
    BookingRefusal,
    ChangeApplied,
    ChangeNoOp,
    ChangeRefusal,
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

# The same fact for a change. Booking's sentence is the wrong verb here: a cancellation
# that never left this service did not fail to *book* anything, and handing the model
# that wording invites it to answer a cancellation request with "nothing was booked".
_CHANGE_UNAVAILABLE_EXPLANATION = (
    "The scheduling service is temporarily unavailable, and nothing was changed. The "
    "appointment still stands exactly as it was."
)

# Used when the scheduler stopped answering during a *write* and cannot confirm what it
# did. Claiming nothing was booked would be a guess, and the one guess that turns into
# the patient booking a second appointment they cannot cancel.
_OUTCOME_UNKNOWN_EXPLANATION = (
    "The scheduling service stopped responding, so it is not known whether that went "
    "through. Do not say it did, do not say it did not, and do not try it again - tell "
    "the patient to check with the clinic."
)

# One sentence per change reason. The set is closed and the scheduler picks exactly one
# per attempt, so this mapping is total - and it is the handler's, not the model's, so
# the model rephrases a cause it cannot invent or contradict.
CHANGE_EXPLANATION_BY_REASON = {
    ChangeFailureReason.APPOINTMENT_NOT_FOUND: (
        "There is no such appointment on this patient's record."
    ),
    ChangeFailureReason.ALREADY_CANCELLED: (
        "That appointment was already cancelled, so there is nothing to move."
    ),
    ChangeFailureReason.ALREADY_STARTED: (
        "That appointment has already started, so it can no longer be changed."
    ),
    ChangeFailureReason.STALE_CONFIRMATION: (
        "That appointment has changed since it was read out. Describe it as it now "
        "stands and ask again - do not repeat the change."
    ),
    ChangeFailureReason.PRACTITIONER_NOT_FOUND: (
        "That practitioner is not one of this clinic's."
    ),
    ChangeFailureReason.PATIENT_NOT_FOUND: (
        "This chat has no patient record, so nothing could be changed."
    ),
    ChangeFailureReason.IN_PAST: "That new time has already passed.",
    ChangeFailureReason.BEYOND_HORIZON: (
        "That is further ahead than appointments can be booked."
    ),
    ChangeFailureReason.OUTSIDE_SCHEDULE: (
        "That time is outside the hours this practitioner sees patients."
    ),
    ChangeFailureReason.OFF_GRID: (
        "Appointments start at fixed times, and that is not one of them."
    ),
    ChangeFailureReason.PRACTITIONER_BUSY: (
        "That practitioner already has something booked then."
    ),
    ChangeFailureReason.PATIENT_BUSY: (
        "The patient already has another appointment overlapping that time."
    ),
}

# What the appointment was already in the state asked for. Reported as done, never as a
# failure and never as a second change.
_UNCHANGED_EXPLANATION = (
    "That appointment is already in exactly that state, so nothing needed to change. "
    "Report it as done."
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


def _change_unavailable() -> ToolResult:
    """The result for a change that provably did not happen.

    `unavailable` rather than `unknown`, because this path is reached only when it is
    actually known that nothing changed - either no attempt reached the scheduler, or
    the scheduler answered by declining to act. Saying so is a fact here, not the guess
    it would be on the unknown path.
    """
    return {"status": "unavailable", "explanation": _CHANGE_UNAVAILABLE_EXPLANATION}


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
    """The result for a write whose fate the scheduler could not confirm.

    Its own status, not a variant of `unavailable`: `unavailable` states that nothing
    happened, and saying that about a write whose answer never arrived is exactly the
    claim the contract forbids. Kept in `status` rather than only in the explanation so
    the turn's outcome derivation and the composing step both see the difference, not
    just a reader of English.
    """
    return {"status": "unknown", "explanation": _OUTCOME_UNKNOWN_EXPLANATION}


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

    `excluded_appointment_id` is optional and omits one appointment from both parties'
    commitments, so an appointment being moved does not block its own new time.

    The registry guarantees a patient record before this runs, so `patient_id` is never
    None here.
    """
    assert context.patient_id is not None

    practitioner_id = required_id_argument(arguments, "practitioner_id")
    excluded_appointment_id = optional_id_argument(arguments, "excluded_appointment_id")
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
            excluded_appointment_id=excluded_appointment_id,
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

    practitioner_id = required_id_argument(arguments, "practitioner_id")
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


def _optional_enum(
    arguments: dict[str, Any], name: str, enum: type[TimeFilter] | type[StatusFilter]
) -> Any:
    """Read an optional axis argument as its enum member, or None when absent.

    Raises: ToolArgumentError if the value is outside the closed set - rejected before
        anything happens, so the model can correct the call within the same turn rather
        than silently getting a corner it did not ask for.
    """
    value = arguments.get(name)
    if value is None:
        return None
    try:
        return enum(value)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum)
        raise ToolArgumentError(
            f"{name} must be one of {allowed}, not {value!r}"
        ) from exc


def _rendered_appointment(appointment: Any) -> dict[str, Any]:
    """Render one appointment for the model.

    Carries `id` because a change has to name an appointment, and `status` because a
    cancelled one must be identified as cancelled wherever it appears. Neither is ever
    said to the patient - that is the prompt's rule, not this layer's.
    """
    return {
        "id": appointment.id,
        "practitioner_full_name": appointment.practitioner_full_name,
        "specialty": appointment.practitioner_specialty,
        "starts_at": format_local_datetime(appointment.starts_at),
        "ends_at": format_local_datetime(appointment.ends_at),
        "status": appointment.status.value,
    }


async def list_my_appointments(
    context: ToolContext, arguments: dict[str, Any]
) -> ToolResult:
    """Return this patient's appointments, in two separately bounded legs.

    Both axes are optional and default to the narrowest corner, so the unqualified
    question answers "still to come, and not cancelled" even when the model sends no
    arguments at all.

    Two empty legs are an explicit "nothing matching", not an error - a patient the
    scheduler cannot resolve is reported as its own result instead, so the model never
    reads "no appointments" off a lookup that never happened.

    The registry guarantees a patient record before this runs, so `patient_id` is never
    None here.
    """
    assert context.patient_id is not None

    time_filter = _optional_enum(arguments, "time_filter", TimeFilter)
    status_filter = _optional_enum(arguments, "status_filter", StatusFilter)
    try:
        listing = await scheduling.list_appointments(
            context.channel,
            context.settings,
            session_id=context.session_id,
            patient_id=context.patient_id,
            local_now=context.local_now,
            time_filter=time_filter if time_filter is not None else TimeFilter.FUTURE,
            status_filter=(
                status_filter if status_filter is not None else StatusFilter.STANDING
            ),
        )
    except SchedulingNotFoundError as exc:
        get_logger().error(
            "appointments.patient_unresolved",
            patient_id=context.patient_id,
            entity=exc.entity.value if exc.entity else None,
        )
        return {"status": "unavailable", "explanation": _NO_PATIENT_LIST_EXPLANATION}
    return {
        "future": [_rendered_appointment(a) for a in listing.future],
        "past": [_rendered_appointment(a) for a in listing.past],
        "past_truncated": listing.past_truncated,
    }


def _change_result(
    outcome: ChangeApplied | ChangeNoOp | ChangeRefusal, change: str
) -> ToolResult:
    """Render one change outcome for the model, as one of its four shapes.

    Args:
        change: What this operation is called in a completed result - "cancelled" or
            "rescheduled" - so the model reports the change that happened rather than
            inferring it from which tool it called.

    A no-op is `unchanged`, never `refused`: the appointment is in the state that was
    asked for, which is success. Only a `refused` result carries a reason, and its
    explanation comes from the closed table here rather than from the wire's `detail`.
    """
    if isinstance(outcome, ChangeRefusal):
        return {
            "status": "refused",
            "reason": outcome.reason.value,
            "explanation": CHANGE_EXPLANATION_BY_REASON[outcome.reason],
        }
    if isinstance(outcome, ChangeNoOp):
        return {
            "status": "unchanged",
            "appointment": _rendered_appointment(outcome.appointment),
            "explanation": _UNCHANGED_EXPLANATION,
        }
    result: ToolResult = {
        "status": "changed",
        "change": change,
        "appointment": _rendered_appointment(outcome.appointment),
        "previous_starts_at": format_local_datetime(outcome.previous_starts_at),
        "previous_practitioner_full_name": outcome.previous_practitioner_full_name,
    }
    return result


async def reschedule_appointment(
    context: ToolContext, arguments: dict[str, Any]
) -> ToolResult:
    """Move one real appointment, or explain why it was not moved.

    The appointment keeps its identity - this is one write, not a cancellation plus a
    new booking. `ends_at` is recomputed from whichever practitioner will hold it, so a
    swap can return an appointment longer or shorter than it went in.

    The guard arguments are the model's own: only it knows what it stated to the
    patient. Re-reading the appointment here would return its current state, which
    matches itself by definition and disables the guard completely.

    The registry guarantees a patient record before this runs, so `patient_id` is never
    None here.
    """
    assert context.patient_id is not None

    appointment_id = required_id_argument(arguments, "appointment_id")
    new_starts_at = _required_datetime(arguments, "new_starts_at")
    # Absent means "keep the practitioner it has", which is the common case.
    new_practitioner_id = optional_id_argument(arguments, "new_practitioner_id")
    expected_starts_at = _required_datetime(arguments, "expected_starts_at")
    expected_practitioner_id = required_id_argument(
        arguments, "expected_practitioner_id"
    )
    try:
        outcome = await scheduling.reschedule_appointment(
            context.channel,
            context.settings,
            session_id=context.session_id,
            patient_id=context.patient_id,
            appointment_id=appointment_id,
            new_starts_at=new_starts_at,
            new_practitioner_id=new_practitioner_id,
            expected_starts_at=expected_starts_at,
            expected_practitioner_id=expected_practitioner_id,
            local_now=context.local_now,
        )
    except SchedulingUnavailableError as exc:
        return _write_failed(exc)
    except SchedulingRequestError as exc:
        # The scheduler answered, and its answer was that it did nothing: the request
        # was rejected before it acted, or the refusal named a reason this build cannot
        # explain. Nothing changed, and that is known - so this is `unavailable`, the
        # same conclusion `book_appointment` draws from the same exception.
        get_logger().error(
            "change.response_unreadable",
            operation="reschedule",
            appointment_id=appointment_id,
            error_detail=str(exc),
        )
        return _change_unavailable()

    return _change_result(outcome, change="rescheduled")


async def cancel_appointment(
    context: ToolContext, arguments: dict[str, Any]
) -> ToolResult:
    """Cancel one real appointment, or explain why it was not.

    The guard arguments are the model's own: only it knows what it stated to the
    patient. Re-reading the appointment here would return its current state, which
    matches itself by definition and disables the guard completely.

    An `unknown` result means the scheduler stopped answering mid-write, so whether the
    appointment is cancelled is genuinely not known - distinct from `unavailable`, which
    says nothing happened.

    The registry guarantees a patient record before this runs, so `patient_id` is never
    None here.
    """
    assert context.patient_id is not None

    appointment_id = required_id_argument(arguments, "appointment_id")
    expected_starts_at = _required_datetime(arguments, "expected_starts_at")
    expected_practitioner_id = required_id_argument(
        arguments, "expected_practitioner_id"
    )
    try:
        outcome = await scheduling.cancel_appointment(
            context.channel,
            context.settings,
            session_id=context.session_id,
            patient_id=context.patient_id,
            appointment_id=appointment_id,
            expected_starts_at=expected_starts_at,
            expected_practitioner_id=expected_practitioner_id,
            local_now=context.local_now,
        )
    except SchedulingUnavailableError as exc:
        return _write_failed(exc)
    except SchedulingRequestError as exc:
        # The scheduler answered, and its answer was that it did nothing: the request
        # was rejected before it acted, or the refusal named a reason this build cannot
        # explain. Nothing changed, and that is known - so this is `unavailable`, the
        # same conclusion `book_appointment` draws from the same exception.
        get_logger().error(
            "change.response_unreadable",
            operation="cancel",
            appointment_id=appointment_id,
            error_detail=str(exc),
        )
        return _change_unavailable()

    return _change_result(outcome, change="cancelled")


def _write_failed(exc: SchedulingUnavailableError) -> ToolResult:
    """Render an unreachable scheduler for a change, saying only what is known.

    A budget exhausted after the request was sent proves nothing about what the server
    did, so that case is `unknown`. Only when every attempt provably failed to reach the
    scheduler is "nothing changed" a fact rather than a guess.

    The turn's own record of an unknown outcome is emitted by the node, which sees the
    tool call that produced it - so this path writes none, and one lost write leaves one
    record rather than two.
    """
    if not exc.outcome_unknown:
        return _change_unavailable()
    return _outcome_unknown()


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
            "availability - offer to look further ahead. When you are offering times "
            "for a CHANGE, always pass excluded_appointment_id - without it the "
            "appointment's own current slot is missing from its options."
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
                "excluded_appointment_id": {
                    "type": "string",
                    "description": (
                        "the appointment being moved, so it does not block its own "
                        "new time"
                    ),
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
            "Lists this patient's appointments. By default: still to come, and not "
            "cancelled. Widen either axis only when the patient asks - and note the "
            "axes are independent, so widening one does not widen the other. For "
            "'what have I cancelled?' pass status_filter 'cancelled' AND time_filter "
            "'both', since a cancellation is not something the patient is still "
            "waiting for and most of them are already in the past. Results come "
            "back as two separate lists - future and past - which are never merged. "
            "The past list holds at most the 20 most recent; if past_truncated is "
            "true, say that PART of the list is incomplete, never that the whole "
            "answer is. Every appointment carries an id you need in order to change "
            "or cancel it - never say an id to the patient."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "time_filter": {
                    "type": "string",
                    "enum": [f.value for f in TimeFilter],
                    "description": "defaults to future",
                },
                "status_filter": {
                    "type": "string",
                    "enum": [f.value for f in StatusFilter],
                    "description": "defaults to standing (not cancelled)",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=_reports_unavailable(list_my_appointments),
        requires_patient=True,
    ),
    Tool(
        name="reschedule_appointment",
        description=(
            "Moves a REAL appointment to a different time, and optionally to a "
            "different practitioner. The appointment keeps its identity - this is not "
            "a cancellation plus a new booking. Only call this after the patient has "
            "explicitly confirmed, in this turn, the appointment being moved and the "
            "exact new time. expected_starts_at and expected_practitioner_id must be "
            "the values you stated to the patient when you asked them to confirm - not "
            "values you have just re-read."
            " appointment_id is not something you can work out: it comes from a "
            "list_my_appointments result in THIS turn. Earlier turns' tool results "
            "are not in the conversation you can see, so call list_my_appointments "
            "again if you do not have one - even when you already know which "
            "appointment the patient means."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "appointment_id": {
                    "type": "string",
                    "description": (
                        "the id this appointment carried in a list_my_appointments "
                        "result in this turn - never one you composed"
                    ),
                },
                "new_starts_at": {
                    "type": "string",
                    "description": "local date-time, YYYY-MM-DDTHH:MM:SS",
                },
                "new_practitioner_id": {
                    "type": "string",
                    "description": "omit to keep the current practitioner",
                },
                "expected_starts_at": {
                    "type": "string",
                    "description": (
                        "the start you read out to the patient, YYYY-MM-DDTHH:MM:SS"
                    ),
                },
                "expected_practitioner_id": {
                    "type": "string",
                    "description": "the practitioner you read out",
                },
            },
            "required": [
                "appointment_id",
                "new_starts_at",
                "expected_starts_at",
                "expected_practitioner_id",
            ],
            "additionalProperties": False,
        },
        handler=reschedule_appointment,
        requires_patient=True,
        writes=True,
    ),
    Tool(
        name="cancel_appointment",
        description=(
            "Cancels a REAL appointment. Cancellation is final - there is no way to "
            "un-cancel, and the freed time may be taken by someone else immediately. "
            "Only call this after the patient has explicitly confirmed, in this turn, "
            "which appointment is being cancelled. expected_starts_at and "
            "expected_practitioner_id must be the values you stated to the patient "
            "when you asked them to confirm - not values you have just re-read."
            " appointment_id is not something you can work out: it comes from a "
            "list_my_appointments result in THIS turn. Earlier turns' tool results "
            "are not in the conversation you can see, so call list_my_appointments "
            "again if you do not have one - even when you already know which "
            "appointment the patient means."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "appointment_id": {
                    "type": "string",
                    "description": (
                        "the id this appointment carried in a list_my_appointments "
                        "result in this turn - never one you composed"
                    ),
                },
                "expected_starts_at": {
                    "type": "string",
                    "description": (
                        "the start you read out to the patient, YYYY-MM-DDTHH:MM:SS"
                    ),
                },
                "expected_practitioner_id": {
                    "type": "string",
                    "description": "the practitioner you read out",
                },
            },
            "required": [
                "appointment_id",
                "expected_starts_at",
                "expected_practitioner_id",
            ],
            "additionalProperties": False,
        },
        handler=cancel_appointment,
        requires_patient=True,
        writes=True,
    ),
]
