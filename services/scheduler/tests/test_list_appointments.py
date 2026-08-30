"""The two-axis, two-leg listing: four corners, two orderings, one cap.

The axes are genuinely independent - one is computed against the client's clock, the
other is stored - so the tests walk all four corners and assert that nothing leaks
between them.
"""

from datetime import datetime, timedelta

from scheduler.repositories import appointment_repository
from scheduler.repositories.appointment_repository import PAST_LEG_LIMIT
from shared_models.scheduling import AppointmentStatus, StatusFilter, TimeFilter
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import make_appointment, new_id, seed_patient, seed_practitioner

_LOCAL_NOW = datetime(2026, 8, 18, 12, 0)


class _Corners:
    """One appointment in each of the four time/status corners, plus the ids."""

    def __init__(self, session_id: str, patient_id: str) -> None:
        self.session_id = session_id
        self.patient_id = patient_id
        self.future_standing = ""
        self.future_cancelled = ""
        self.past_standing = ""
        self.past_cancelled = ""


async def _seed_corners(session: AsyncSession) -> _Corners:
    session_id = new_id()
    practitioner = await seed_practitioner(session, session_id)
    patient = await seed_patient(session, session_id)
    corners = _Corners(session_id, patient.id)

    for attribute, start, status in (
        ("future_standing", _LOCAL_NOW + timedelta(days=1), AppointmentStatus.STANDING),
        (
            "future_cancelled",
            _LOCAL_NOW + timedelta(days=2),
            AppointmentStatus.CANCELLED,
        ),
        ("past_standing", _LOCAL_NOW - timedelta(days=1), AppointmentStatus.STANDING),
        ("past_cancelled", _LOCAL_NOW - timedelta(days=2), AppointmentStatus.CANCELLED),
    ):
        appointment = make_appointment(
            session_id,
            patient.id,
            practitioner.id,
            start,
            start + timedelta(hours=1),
            status=status,
        )
        session.add(appointment)
        setattr(corners, attribute, appointment.id)
    await session.commit()
    return corners


async def _list(
    session: AsyncSession,
    corners: _Corners,
    *,
    time_filter: TimeFilter = TimeFilter.FUTURE,
    status_filter: StatusFilter = StatusFilter.STANDING,
    session_id: str | None = None,
    patient_id: str | None = None,
) -> appointment_repository.AppointmentListing:
    return await appointment_repository.list_for_patient(
        session,
        session_id=session_id if session_id is not None else corners.session_id,
        patient_id=patient_id if patient_id is not None else corners.patient_id,
        local_now=_LOCAL_NOW,
        time_filter=time_filter,
        status_filter=status_filter,
    )


async def test_the_unqualified_question_answers_future_and_standing(
    db_session: AsyncSession,
) -> None:
    corners = await _seed_corners(db_session)

    listing = await _list(db_session, corners)

    assert [a.id for a in listing.future] == [corners.future_standing]
    assert listing.past == []


async def test_the_future_cancelled_corner_returns_only_its_own(
    db_session: AsyncSession,
) -> None:
    corners = await _seed_corners(db_session)

    listing = await _list(db_session, corners, status_filter=StatusFilter.CANCELLED)

    assert [a.id for a in listing.future] == [corners.future_cancelled]
    assert listing.past == []


async def test_the_past_standing_corner_returns_only_its_own(
    db_session: AsyncSession,
) -> None:
    corners = await _seed_corners(db_session)

    listing = await _list(db_session, corners, time_filter=TimeFilter.PAST)

    assert [a.id for a in listing.past] == [corners.past_standing]
    assert listing.future == []


async def test_the_past_cancelled_corner_returns_only_its_own(
    db_session: AsyncSession,
) -> None:
    corners = await _seed_corners(db_session)

    listing = await _list(
        db_session,
        corners,
        time_filter=TimeFilter.PAST,
        status_filter=StatusFilter.CANCELLED,
    )

    assert [a.id for a in listing.past] == [corners.past_cancelled]
    assert listing.future == []


async def test_a_request_spanning_both_axes_returns_two_separate_legs(
    db_session: AsyncSession,
) -> None:
    # SC-013: the legs are separate fields, so neither can crowd the other out.
    corners = await _seed_corners(db_session)

    listing = await _list(
        db_session,
        corners,
        time_filter=TimeFilter.BOTH,
        status_filter=StatusFilter.BOTH,
    )

    assert {a.id for a in listing.future} == {
        corners.future_standing,
        corners.future_cancelled,
    }
    assert {a.id for a in listing.past} == {
        corners.past_standing,
        corners.past_cancelled,
    }


async def test_an_appointment_starting_exactly_at_local_now_falls_in_the_past_leg(
    db_session: AsyncSession,
) -> None:
    # The future leg is strictly after `local_now`, so one under way is past - matching
    # how a start at exactly `local_now` is refused as in-past by booking.
    corners = await _seed_corners(db_session)
    session_id = corners.session_id
    practitioner = await seed_practitioner(db_session, session_id, full_name="Dr Z")
    under_way = make_appointment(
        session_id,
        corners.patient_id,
        practitioner.id,
        _LOCAL_NOW,
        _LOCAL_NOW + timedelta(hours=1),
    )
    db_session.add(under_way)
    await db_session.commit()

    listing = await _list(db_session, corners, time_filter=TimeFilter.BOTH)

    assert under_way.id in [a.id for a in listing.past]
    assert under_way.id not in [a.id for a in listing.future]


async def test_the_future_leg_ascends_and_the_past_leg_descends(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    corners = _Corners(session_id, patient.id)
    for offset in (1, 2, 3):
        for sign in (1, -1):
            start = _LOCAL_NOW + timedelta(days=offset * sign)
            db_session.add(
                make_appointment(
                    session_id,
                    patient.id,
                    practitioner.id,
                    start,
                    start + timedelta(hours=1),
                )
            )
    await db_session.commit()

    listing = await _list(db_session, corners, time_filter=TimeFilter.BOTH)

    future_starts = [a.starts_at for a in listing.future]
    past_starts = [a.starts_at for a in listing.past]
    assert future_starts == sorted(future_starts)
    assert past_starts == sorted(past_starts, reverse=True)


async def test_the_future_leg_is_unbounded_and_never_marked_truncated(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    corners = _Corners(session_id, patient.id)
    for day in range(1, PAST_LEG_LIMIT + 6):
        start = _LOCAL_NOW + timedelta(days=day)
        db_session.add(
            make_appointment(
                session_id,
                patient.id,
                practitioner.id,
                start,
                start + timedelta(hours=1),
            )
        )
    await db_session.commit()

    listing = await _list(db_session, corners)

    assert len(listing.future) == PAST_LEG_LIMIT + 5
    assert listing.past_truncated is False


async def test_the_past_leg_is_capped_and_reports_that_it_was(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    corners = _Corners(session_id, patient.id)
    for day in range(1, PAST_LEG_LIMIT + 4):
        start = _LOCAL_NOW - timedelta(days=day)
        db_session.add(
            make_appointment(
                session_id,
                patient.id,
                practitioner.id,
                start,
                start + timedelta(hours=1),
            )
        )
    await db_session.commit()

    listing = await _list(db_session, corners, time_filter=TimeFilter.PAST)

    assert len(listing.past) == PAST_LEG_LIMIT
    assert listing.past_truncated is True
    # Descending, so the cap keeps the most recent - the ones a conversation is
    # actually about.
    assert listing.past[0].starts_at == _LOCAL_NOW - timedelta(days=1)


async def test_a_past_leg_exactly_at_the_cap_is_not_marked_truncated(
    db_session: AsyncSession,
) -> None:
    # The `LIMIT 21` probe exists to tell "exactly twenty" from "twenty of more", and
    # this is the boundary where a naive `len(rows) == 20` check gets it wrong.
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    corners = _Corners(session_id, patient.id)
    for day in range(1, PAST_LEG_LIMIT + 1):
        start = _LOCAL_NOW - timedelta(days=day)
        db_session.add(
            make_appointment(
                session_id,
                patient.id,
                practitioner.id,
                start,
                start + timedelta(hours=1),
            )
        )
    await db_session.commit()

    listing = await _list(db_session, corners, time_filter=TimeFilter.PAST)

    assert len(listing.past) == PAST_LEG_LIMIT
    assert listing.past_truncated is False


async def test_the_read_is_scoped_to_the_session(db_session: AsyncSession) -> None:
    corners = await _seed_corners(db_session)

    listing = await _list(
        db_session,
        corners,
        session_id=new_id(),
        time_filter=TimeFilter.BOTH,
        status_filter=StatusFilter.BOTH,
    )

    assert listing.future == []
    assert listing.past == []


async def test_the_read_is_scoped_to_the_patient(db_session: AsyncSession) -> None:
    corners = await _seed_corners(db_session)
    other = await seed_patient(db_session, corners.session_id, full_name="Bram")

    listing = await _list(
        db_session,
        corners,
        patient_id=other.id,
        time_filter=TimeFilter.BOTH,
        status_filter=StatusFilter.BOTH,
    )

    assert listing.future == []
    assert listing.past == []
