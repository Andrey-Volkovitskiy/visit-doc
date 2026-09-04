"""The `Scheduling` gRPC service implementation.

A booking the service evaluated and refused is a *successful* RPC carrying a typed
`BookingFailure`; gRPC status codes are reserved for transport, infrastructure, and
caller-defect failures. That split is what lets the chat service tell "the patient must
choose differently" from "the service is unreachable" without parsing a status string.
"""

from typing import Any, NoReturn

import grpc
from shared_models.localtime import format_local_date, format_local_datetime
from shared_models.scheduling import BookingFailureReason, NotFoundEntity
from shared_proto.scheduling.v1 import scheduling_pb2 as pb
from shared_proto.scheduling.v1 import scheduling_pb2_grpc
from sqlalchemy.exc import IntegrityError

from scheduler.core.config import get_settings
from scheduler.core.logging import get_logger
from scheduler.db.session import session_factory
from scheduler.domain import availability
from scheduler.grpc import converters
from scheduler.repositories import (
    appointment_repository,
    patient_repository,
    practitioner_repository,
)

_PROTO_BY_FAILURE_REASON = {
    BookingFailureReason.PRACTITIONER_BUSY: (
        pb.BOOKING_FAILURE_REASON_PRACTITIONER_BUSY
    ),
    BookingFailureReason.PATIENT_BUSY: pb.BOOKING_FAILURE_REASON_PATIENT_BUSY,
    BookingFailureReason.OUTSIDE_SCHEDULE: pb.BOOKING_FAILURE_REASON_OUTSIDE_SCHEDULE,
    BookingFailureReason.OFF_GRID: pb.BOOKING_FAILURE_REASON_OFF_GRID,
    BookingFailureReason.IN_PAST: pb.BOOKING_FAILURE_REASON_IN_PAST,
    BookingFailureReason.BEYOND_HORIZON: pb.BOOKING_FAILURE_REASON_BEYOND_HORIZON,
    BookingFailureReason.PRACTITIONER_NOT_FOUND: (
        pb.BOOKING_FAILURE_REASON_PRACTITIONER_NOT_FOUND
    ),
    BookingFailureReason.PATIENT_NOT_FOUND: (
        pb.BOOKING_FAILURE_REASON_PATIENT_NOT_FOUND
    ),
}


async def _abort(context: Any, code: grpc.StatusCode, detail: str) -> NoReturn:
    """End the RPC with `code`, never returning.

    `context.abort` already raises, but its stub is untyped, so callers that narrow a
    value afterwards need the `NoReturn` this annotation supplies.
    """
    await context.abort(code, detail)
    raise AssertionError("context.abort() returned")  # pragma: no cover


def _failure(reason: BookingFailureReason) -> pb.BookingFailure:
    """Render one refusal onto the wire.

    `detail` is for logs only - the assistant's explanation to the patient is built
    from `reason`, never from this string.
    """
    return pb.BookingFailure(
        reason=_PROTO_BY_FAILURE_REASON[reason], detail=reason.value
    )


def _change_response(
    outcome: appointment_repository.ChangeOutcome, *, carries_previous: bool
) -> pb.ChangeAppointmentResponse:
    """Render one change outcome onto the wire, as exactly one of its three results.

    Args:
        carries_previous: Whether this operation has a destination to have come from.
            False for a cancellation, which has none - filling the previous fields in
            would describe it as a move to the time it already had.
    """
    if isinstance(outcome, appointment_repository.ChangeRefused):
        return pb.ChangeAppointmentResponse(
            failure=converters.to_proto_change_failure(outcome.reason)
        )
    rendered = converters.to_proto_appointment(
        outcome.appointment, outcome.patient, outcome.practitioner
    )
    if isinstance(outcome, appointment_repository.ChangeNoOp):
        return pb.ChangeAppointmentResponse(no_change=pb.NoChange(appointment=rendered))
    if not carries_previous:
        return pb.ChangeAppointmentResponse(appointment=rendered)
    return pb.ChangeAppointmentResponse(
        appointment=rendered,
        previous_starts_at=format_local_datetime(outcome.previous_starts_at),
        previous_practitioner_id=outcome.previous_practitioner_id,
        previous_practitioner_full_name=outcome.previous_practitioner_full_name,
    )


def _render_leg(
    appointments: list[Any],
    patient: Any,
    practitioners: dict[str, Any],
) -> list[pb.Appointment]:
    """Render one leg of a listing, dropping any appointment whose practitioner is gone.

    A practitioner deleted between the two reads takes their appointments by cascade, so
    the row being described no longer exists either - omitting it is more accurate than
    failing the call, which would report a healthy scheduler as unreachable.
    """
    rendered = []
    for appointment in appointments:
        practitioner = practitioners.get(appointment.practitioner_id)
        if practitioner is None:
            get_logger().warning(
                "appointment.practitioner_missing",
                appointment_id=appointment.id,
                practitioner_id=appointment.practitioner_id,
            )
            continue
        rendered.append(
            converters.to_proto_appointment(appointment, patient, practitioner)
        )
    return rendered


class SchedulingServicer(scheduling_pb2_grpc.SchedulingServicer):
    """Serves the nine scheduling RPCs against the scheduler's own database.

    Every handler opens its own session from the shared factory and owns its
    transaction, matching the repository layer's session-as-a-parameter shape.

    Handlers read their request fields straight through `converters`, without guarding
    each read: `LoggingInterceptor` turns a `ConversionError` into `INVALID_ARGUMENT`
    for every RPC at once, so a new one cannot be added without that behavior.
    """

    async def EnsureSessionProvisioned(  # noqa: N802 - name fixed by the gRPC contract
        self,
        request: pb.EnsureSessionProvisionedRequest,
        context: Any,
    ) -> pb.EnsureSessionProvisionedResponse:
        """Create this chat's patient and, if the session has none, its practitioners.

        Idempotent on both counts, which is what makes it safe to call on every visit
        and to retry after a failure: the patient is keyed by chat, and seeding is
        guarded on the session having no practitioners at all - so a second, third, or
        hundredth chat in one session never seeds another roster.
        """
        session_id = converters.read_required_id(request.session_id, "session_id")
        chat_id = converters.read_required_id(request.chat_id, "chat_id")

        async with session_factory() as session:
            try:
                patient, patient_created = await patient_repository.create_if_absent(
                    session, session_id, chat_id
                )
            except patient_repository.ChatSessionMismatchError:
                # The chat exists, but under another session. Reported as not-found
                # like every other cross-session id, and never by handing back the
                # patient that holds it.
                await _abort(
                    context, grpc.StatusCode.NOT_FOUND, NotFoundEntity.CHAT.value
                )
            # Read onto the wire before the practitioner step below, which may roll
            # back: a rollback expires every object loaded before it, and reading an
            # expired attribute afterwards would attempt IO where none is expected.
            patient_message = converters.to_proto_patient(patient)
            practitioners = await practitioner_repository.list_for_session(
                session, session_id
            )
            practitioner_created = False
            if not practitioners:
                try:
                    await practitioner_repository.seed_session(session, session_id)
                    practitioner_created = True
                except IntegrityError:
                    # A concurrent first visit in the same session seeded the roster
                    # first. The name's UNIQUE constraint is the guard, and it cannot
                    # fire until that transaction committed, so the loser simply
                    # re-reads the whole roster rather than appending to it.
                    await session.rollback()
                    get_logger().warning(
                        "name.collision_retried",
                        entity="practitioner",
                        attempt=1,
                    )
                practitioners = await practitioner_repository.list_for_session(
                    session, session_id
                )
            schedules = await practitioner_repository.get_schedules(
                session, [p.id for p in practitioners]
            )
            return pb.EnsureSessionProvisionedResponse(
                patient=patient_message,
                practitioners=[
                    converters.to_proto_practitioner(p, schedules[p.id])
                    for p in practitioners
                ],
                patient_created=patient_created,
                practitioner_created=practitioner_created,
            )

    async def ListPractitioners(  # noqa: N802 - name fixed by the gRPC contract
        self,
        request: pb.ListPractitionersRequest,
        context: Any,
    ) -> pb.ListPractitionersResponse:
        """Return every practitioner in the caller's session, with their schedule."""
        session_id = converters.read_required_id(request.session_id, "session_id")

        async with session_factory() as session:
            practitioners = await practitioner_repository.list_for_session(
                session, session_id
            )
            schedules = await practitioner_repository.get_schedules(
                session, [p.id for p in practitioners]
            )

        return pb.ListPractitionersResponse(
            practitioners=[
                converters.to_proto_practitioner(p, schedules[p.id])
                for p in practitioners
            ]
        )

    async def CheckAvailability(  # noqa: N802 - name fixed by the gRPC contract
        self,
        request: pb.CheckAvailabilityRequest,
        context: Any,
    ) -> pb.CheckAvailabilityResponse:
        """Return the start times bookable by this patient with this practitioner.

        Every returned start is bookable by this patient at the moment it is produced:
        it passes exactly the predicates `BookAppointment` applies, evaluated by the
        same code, and excludes both the practitioner's and the patient's existing
        appointments. Only another patient taking a slot in between can undo that.

        An unknown practitioner or patient - including one belonging to another session
        - is answered with NOT_FOUND rather than an empty result, which the response's
        own contract reserves for a practitioner who genuinely has nothing to offer.
        The status detail is the `NotFoundEntity` that failed to resolve, so the caller
        explains the actual cause instead of assuming it was the practitioner.
        """
        session_id = converters.read_required_id(request.session_id, "session_id")
        practitioner_id = converters.read_required_id(
            request.practitioner_id, "practitioner_id"
        )
        patient_id = converters.read_required_id(request.patient_id, "patient_id")
        from_date = converters.read_local_date(request.from_date, "from_date")
        to_date = converters.read_local_date(request.to_date, "to_date")
        local_now = converters.read_local_datetime(request.local_now, "local_now")

        if to_date < from_date:
            await _abort(
                context,
                grpc.StatusCode.INVALID_ARGUMENT,
                f"to_date {request.to_date} precedes from_date {request.from_date}",
            )

        async with session_factory() as session:
            practitioner = await practitioner_repository.get(
                session, practitioner_id, session_id
            )
            if practitioner is None:
                await _abort(
                    context,
                    grpc.StatusCode.NOT_FOUND,
                    NotFoundEntity.PRACTITIONER.value,
                )
            # Scoped like every other read: an unscoped patient id would still have its
            # appointments subtracted from the answer, leaking the times it holds
            # through the slots that went missing.
            patient = await patient_repository.get(session, patient_id, session_id)
            if patient is None:
                await _abort(
                    context, grpc.StatusCode.NOT_FOUND, NotFoundEntity.PATIENT.value
                )

            schedule = practitioner_repository.to_daily_ranges(
                await practitioner_repository.get_schedule(session, practitioner.id)
            )
            busy = await appointment_repository.busy_intervals(
                session,
                session_id=session_id,
                practitioner_id=practitioner.id,
                patient_id=patient.id,
                from_date=from_date,
                to_date=to_date,
                # Optional, and empty means no exclusion. Set when offering times for a
                # change, so the appointment being moved does not block its own new
                # time - including a move to the time it already holds.
                excluded_appointment_id=request.excluded_appointment_id or None,
            )

        settings = get_settings()
        starts, truncated = availability.available_starts(
            schedule=schedule,
            duration_minutes=practitioner.appointment_duration_minutes,
            busy=busy,
            from_date=from_date,
            to_date=to_date,
            local_now=local_now,
            horizon_days=settings.BOOKING_HORIZON_DAYS,
            max_window_days=settings.AVAILABILITY_MAX_WINDOW_DAYS,
            max_slots=settings.AVAILABILITY_MAX_SLOTS,
        )
        get_logger().info(
            "availability.computed",
            practitioner_id=practitioner.id,
            from_date=format_local_date(from_date),
            to_date=format_local_date(to_date),
            slot_count=len(starts),
            truncated=truncated,
        )
        return pb.CheckAvailabilityResponse(
            available_starts=[format_local_datetime(s) for s in starts],
            truncated=truncated,
            appointment_duration_minutes=practitioner.appointment_duration_minutes,
        )

    async def BookAppointment(  # noqa: N802 - name fixed by the gRPC contract
        self,
        request: pb.BookAppointmentRequest,
        context: Any,
    ) -> pb.BookAppointmentResponse:
        """Create one appointment, or explain in a typed failure why it was refused."""
        session_id = converters.read_required_id(request.session_id, "session_id")
        patient_id = converters.read_required_id(request.patient_id, "patient_id")
        practitioner_id = converters.read_required_id(
            request.practitioner_id, "practitioner_id"
        )
        starts_at = converters.read_local_datetime(request.starts_at, "starts_at")
        local_now = converters.read_local_datetime(request.local_now, "local_now")
        idempotency_key = converters.read_required_id(
            request.idempotency_key, "idempotency_key"
        )

        async with session_factory() as session:
            try:
                outcome = await appointment_repository.book(
                    session,
                    session_id=session_id,
                    patient_id=patient_id,
                    practitioner_id=practitioner_id,
                    starts_at=starts_at,
                    local_now=local_now,
                    idempotency_key=idempotency_key,
                    horizon_days=get_settings().BOOKING_HORIZON_DAYS,
                )
            except appointment_repository.IdempotencyKeyMismatchError as exc:
                # Not a BookingFailure: the caller's key derivation is broken, which is
                # nothing the patient can resolve by choosing differently. Returning
                # the stored appointment here would confirm a time they never asked for.
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))

            if isinstance(outcome, appointment_repository.BookingRefused):
                return pb.BookAppointmentResponse(failure=_failure(outcome.reason))

            return pb.BookAppointmentResponse(
                appointment=converters.to_proto_appointment(
                    outcome.appointment, outcome.patient, outcome.practitioner
                ),
                idempotent_replay=outcome.idempotent_replay,
            )

    async def RescheduleAppointment(  # noqa: N802 - name fixed by the gRPC contract
        self,
        request: pb.RescheduleAppointmentRequest,
        context: Any,
    ) -> pb.ChangeAppointmentResponse:
        """Move one appointment, or explain in a typed failure why it was not.

        `new_practitioner_id` left empty means "keep the practitioner it has". When set,
        practitioner, start and end change together in one write - never a cancellation
        plus a new booking.

        `previous_starts_at` and `previous_practitioner_id` accompany a completed move
        and nothing else: a request that transitioned nothing has no state it came from.
        """
        session_id = converters.read_required_id(request.session_id, "session_id")
        patient_id = converters.read_required_id(request.patient_id, "patient_id")
        appointment_id = converters.read_required_id(
            request.appointment_id, "appointment_id"
        )
        new_starts_at = converters.read_local_datetime(
            request.new_starts_at, "new_starts_at"
        )
        expected_starts_at = converters.read_local_datetime(
            request.expected_starts_at, "expected_starts_at"
        )
        expected_practitioner_id = converters.read_required_id(
            request.expected_practitioner_id, "expected_practitioner_id"
        )
        local_now = converters.read_local_datetime(request.local_now, "local_now")

        async with session_factory() as session:
            outcome = await appointment_repository.reschedule(
                session,
                session_id=session_id,
                patient_id=patient_id,
                appointment_id=appointment_id,
                new_starts_at=new_starts_at,
                # Empty is the contract's "keep the one it has", not a missing field.
                new_practitioner_id=request.new_practitioner_id or None,
                expected_starts_at=expected_starts_at,
                expected_practitioner_id=expected_practitioner_id,
                local_now=local_now,
                horizon_days=get_settings().BOOKING_HORIZON_DAYS,
            )
            return _change_response(outcome, carries_previous=True)

    async def CancelAppointment(  # noqa: N802 - name fixed by the gRPC contract
        self,
        request: pb.CancelAppointmentRequest,
        context: Any,
    ) -> pb.ChangeAppointmentResponse:
        """Cancel one appointment, or explain in a typed failure why it was not.

        Three outcomes, never two: the write took effect, the appointment was already
        cancelled, or one rule refused it. Collapsing the middle one into either of the
        others would leave the caller unable to tell a cancellation from a cancellation
        re-sent - and telling a patient their cancellation failed when the appointment
        is, in fact, cancelled.
        """
        session_id = converters.read_required_id(request.session_id, "session_id")
        patient_id = converters.read_required_id(request.patient_id, "patient_id")
        appointment_id = converters.read_required_id(
            request.appointment_id, "appointment_id"
        )
        expected_starts_at = converters.read_local_datetime(
            request.expected_starts_at, "expected_starts_at"
        )
        expected_practitioner_id = converters.read_required_id(
            request.expected_practitioner_id, "expected_practitioner_id"
        )
        local_now = converters.read_local_datetime(request.local_now, "local_now")

        async with session_factory() as session:
            outcome = await appointment_repository.cancel(
                session,
                session_id=session_id,
                patient_id=patient_id,
                appointment_id=appointment_id,
                expected_starts_at=expected_starts_at,
                expected_practitioner_id=expected_practitioner_id,
                local_now=local_now,
            )
            return _change_response(outcome, carries_previous=False)

    async def ListAppointments(  # noqa: N802 - name fixed by the gRPC contract
        self,
        request: pb.ListAppointmentsRequest,
        context: Any,
    ) -> pb.ListAppointmentsResponse:
        """Return this patient's appointments in the corner of the grid asked for.

        A patient that does not resolve in this session is answered with NOT_FOUND, not
        with two empty legs: empty legs are the answer for a patient who exists and has
        nothing matching, and one value cannot mean both without the caller having to
        guess which - a guess that reads to the patient as "you have nothing booked".

        An appointment whose practitioner was deleted between the two reads is omitted
        rather than raising: the practitioner is already gone, and failing the whole
        call would report a healthy scheduler as unreachable.
        """
        session_id = converters.read_required_id(request.session_id, "session_id")
        patient_id = converters.read_required_id(request.patient_id, "patient_id")
        local_now = converters.read_local_datetime(request.local_now, "local_now")
        time_filter = converters.read_time_filter(request.time_filter)
        status_filter = converters.read_status_filter(request.status_filter)

        async with session_factory() as session:
            patient = await patient_repository.get(session, patient_id, session_id)
            if patient is None:
                await _abort(
                    context, grpc.StatusCode.NOT_FOUND, NotFoundEntity.PATIENT.value
                )
            listing = await appointment_repository.list_for_patient(
                session,
                session_id=session_id,
                patient_id=patient.id,
                local_now=local_now,
                time_filter=time_filter,
                status_filter=status_filter,
            )
            practitioners = await practitioner_repository.get_by_ids(
                session,
                session_id,
                [a.practitioner_id for a in listing.future + listing.past],
            )

        return pb.ListAppointmentsResponse(
            future=_render_leg(listing.future, patient, practitioners),
            past=_render_leg(listing.past, patient, practitioners),
            past_truncated=listing.past_truncated,
        )

    async def DeletePatientForChat(  # noqa: N802 - name fixed by the gRPC contract
        self,
        request: pb.DeletePatientForChatRequest,
        context: Any,
    ) -> pb.DeletePatientForChatResponse:
        """Delete this chat's patient and, by cascade, that patient's appointments.

        Idempotent: deleting an already-absent patient succeeds, so a caller retrying
        after a lost response never has to distinguish "already gone" from "never was".
        """
        session_id = converters.read_required_id(request.session_id, "session_id")
        chat_id = converters.read_required_id(request.chat_id, "chat_id")

        async with session_factory() as session:
            existed, deleted = await patient_repository.delete_for_chat(
                session, session_id, chat_id
            )

        return pb.DeletePatientForChatResponse(
            patient_existed=existed, appointments_deleted=deleted
        )

    async def DeleteSession(  # noqa: N802 - name fixed by the gRPC contract
        self,
        request: pb.DeleteSessionRequest,
        context: Any,
    ) -> pb.DeleteSessionResponse:
        """Delete everything one session owns here, and report what went.

        One transaction, so a caller reporting per session has two outcomes to
        distinguish rather than a spectrum. Idempotent: a session that owns nothing -
        including one that never existed - succeeds with every count at zero, because
        "already gone" and "was never here" are the same end state and nobody acts
        differently on them.

        Appointments follow their practitioner and their patient by cascade, and those
        cascades are status-blind, so cancelled appointments go with the rest.
        """
        session_id = converters.read_required_id(request.session_id, "session_id")

        async with session_factory() as session:
            appointments = await appointment_repository.count_for_session(
                session, session_id
            )
            practitioners = await practitioner_repository.delete_for_session(
                session, session_id
            )
            patients = await patient_repository.delete_for_session(session, session_id)
            await session.commit()

        get_logger().info(
            "session.purged",
            session_id=session_id,
            patients_deleted=patients,
            practitioners_deleted=practitioners,
            appointments_deleted=appointments,
        )
        return converters.to_delete_session_response(
            patients_deleted=patients,
            practitioners_deleted=practitioners,
            appointments_deleted=appointments,
        )
