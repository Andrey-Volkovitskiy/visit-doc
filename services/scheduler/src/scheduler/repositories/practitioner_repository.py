"""Practitioner and working-range reads and writes, scoped to one session.

Module-level functions taking the `AsyncSession` explicitly rather than a class holding
one, so the caller owns the transaction boundary and these stay reusable across the gRPC
and HTTP surfaces alike.
"""

from datetime import time

from shared_models.scheduling import Specialty, Weekday
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from scheduler.core.logging import get_logger
from scheduler.domain.availability import DailyRange
from scheduler.domain.models import Practitioner, WorkingRange
from scheduler.domain.name_pools import PHYSICIAN_POOL
from scheduler.domain.naming import MAX_NAME_ATTEMPTS, NamedEntity, allocate_name

# The constraints a write to these tables can violate, named here beside the tables
# themselves so the retry below and the HTTP layer's status mapping read the same
# strings rather than each keeping their own copy.
NAME_UNIQUE_CONSTRAINT = "practitioners_name_unique"
RANGE_CONSTRAINTS = (
    "working_ranges_no_overlap",
    "working_ranges_weekday_range",
    "working_ranges_ordered",
)

# Applied to a practitioner created with nothing supplied, so a bare create yields
# someone immediately bookable rather than a listed name with no time to offer.
DEFAULT_SPECIALTY = Specialty.GENERAL_PRACTICE
DEFAULT_DURATION_MINUTES = 60
DEFAULT_SCHEDULE: tuple[tuple[Weekday, time, time], ...] = tuple(
    (Weekday(weekday), time(9, 0), time(17, 0)) for weekday in range(5)
)


async def list_for_session(
    session: AsyncSession, session_id: str
) -> list[Practitioner]:
    """Return the session's practitioners, oldest first."""
    result = await session.execute(
        select(Practitioner)
        .where(Practitioner.session_id == session_id)
        .order_by(Practitioner.created_at.asc(), Practitioner.id.asc())
    )
    return list(result.scalars().all())


async def get(
    session: AsyncSession, practitioner_id: str, session_id: str
) -> Practitioner | None:
    """Return `practitioner_id` if it belongs to `session_id`, else None.

    Scoped by filter, so an id from another session simply does not resolve and is
    reported as not-found, indistinguishably from one that never existed.
    """
    result = await session.execute(
        select(Practitioner).where(
            Practitioner.id == practitioner_id,
            Practitioner.session_id == session_id,
        )
    )
    return result.scalars().first()


async def get_by_ids(
    session: AsyncSession, session_id: str, practitioner_ids: list[str]
) -> dict[str, Practitioner]:
    """Return the named practitioners belonging to `session_id`, keyed by id.

    An id that does not resolve - unknown, deleted, or another session's - is simply
    absent from the result, so a caller rendering rows that reference it can skip them
    rather than failing.
    """
    if not practitioner_ids:
        return {}
    result = await session.execute(
        select(Practitioner).where(
            Practitioner.id.in_(set(practitioner_ids)),
            Practitioner.session_id == session_id,
        )
    )
    return {p.id: p for p in result.scalars().all()}


async def get_schedules(
    session: AsyncSession, practitioner_ids: list[str]
) -> dict[str, list[WorkingRange]]:
    """Return every listed practitioner's working ranges.

    Returns: a dict keyed by practitioner id, each value that practitioner's ranges
        ordered by weekday then start time.

    One query for the whole list rather than one per practitioner, since listing a
    session's practitioners always needs all of their schedules. Every listed id gets a
    key, empty for a practitioner with no ranges.
    """
    if not practitioner_ids:
        return {}
    result = await session.execute(
        select(WorkingRange)
        .where(WorkingRange.practitioner_id.in_(practitioner_ids))
        .order_by(WorkingRange.weekday.asc(), WorkingRange.start_time.asc())
    )
    schedules: dict[str, list[WorkingRange]] = {pid: [] for pid in practitioner_ids}
    for working_range in result.scalars().all():
        schedules[working_range.practitioner_id].append(working_range)
    return schedules


async def get_schedule(
    session: AsyncSession, practitioner_id: str
) -> list[WorkingRange]:
    """Return `practitioner_id`'s working ranges, by weekday then start time."""
    return (await get_schedules(session, [practitioner_id]))[practitioner_id]


def to_daily_ranges(ranges: list[WorkingRange]) -> list[DailyRange]:
    """Convert persisted working ranges into the availability walk's own type."""
    return [DailyRange(Weekday(r.weekday), r.start_time, r.end_time) for r in ranges]


async def taken_names(session: AsyncSession, session_id: str) -> set[str]:
    """Return every practitioner name already used in `session_id`."""
    result = await session.execute(
        select(Practitioner.full_name).where(Practitioner.session_id == session_id)
    )
    return set(result.scalars().all())


def _add_ranges(
    session: AsyncSession,
    practitioner_id: str,
    ranges: list[tuple[Weekday, time, time]] | tuple[tuple[Weekday, time, time], ...],
) -> None:
    """Stage one working-range row per supplied triple, without flushing."""
    for weekday, start_time, end_time in ranges:
        session.add(
            WorkingRange(
                id=str(ULID()),
                practitioner_id=practitioner_id,
                weekday=weekday,
                start_time=start_time,
                end_time=end_time,
            )
        )


async def create(
    session: AsyncSession,
    session_id: str,
    *,
    full_name: str | None = None,
    specialty: Specialty = DEFAULT_SPECIALTY,
    appointment_duration_minutes: int = DEFAULT_DURATION_MINUTES,
    schedule: list[tuple[Weekday, time, time]] | None = None,
    retry_pool_name: bool = True,
) -> Practitioner:
    """Create a practitioner, applying the defaults for anything not supplied.

    Args:
        full_name: Omitted means the next unused physician-pool name.
        schedule: `(weekday, start_time, end_time)` triples. Omitted entirely means the
            default Monday-Friday schedule; an explicitly empty list means a
            practitioner who is listed but never bookable, which is a legal state.
        retry_pool_name: Whether a pool-allocated name taken by a concurrent creation
            is retried with the next free one. False for a caller whose intent is to
            seed exactly one practitioner, for which losing the race is the answer
            rather than a problem - see `EnsureSessionProvisioned`.

    Raises:
        IntegrityError: `full_name` was supplied and is already used in this session,
            the supplied ranges overlap on a weekday, or a pool-name collision
            persisted past `MAX_NAME_ATTEMPTS`.

    A name this call chose from the pool is retried on collision rather than reported:
    the caller supplied no name, so "that name is taken" describes a decision they did
    not make and cannot act on. A name they *did* supply is never retried - creating
    them under a different name than they asked for would be worse than the error.

    Creating with nothing supplied yields someone immediately bookable, which is what
    first-visit seeding relies on.
    """
    from_pool = full_name is None

    for attempt in range(1, MAX_NAME_ATTEMPTS + 1):
        name = (
            allocate_name(
                PHYSICIAN_POOL,
                await taken_names(session, session_id),
                entity=NamedEntity.PRACTITIONER,
            )
            if from_pool
            else full_name
        )
        practitioner = Practitioner(
            id=str(ULID()),
            session_id=session_id,
            full_name=name,
            specialty=specialty,
            appointment_duration_minutes=appointment_duration_minutes,
        )
        session.add(practitioner)
        try:
            await session.flush()
            ranges = list(DEFAULT_SCHEDULE) if schedule is None else schedule
            _add_ranges(session, practitioner.id, ranges)
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            # Only a pool name this call chose is retried. A supplied name, or any
            # other constraint (an overlapping range), is the caller's to see - and
            # retrying an overlap would just fail five times over.
            if not retry_pool_name or not from_pool:
                raise
            if NAME_UNIQUE_CONSTRAINT not in str(exc.orig):
                raise
            get_logger().warning(
                "name.collision_retried",
                entity=NamedEntity.PRACTITIONER,
                attempt=attempt,
            )
            continue
        await session.refresh(practitioner)
        return practitioner

    message = "practitioner name allocation exhausted its retries"
    raise IntegrityError(message, None, None)  # type: ignore[arg-type]


async def replace_schedule(
    session: AsyncSession,
    practitioner_id: str,
    schedule: list[tuple[Weekday, time, time]],
) -> None:
    """Replace a practitioner's whole schedule with `schedule`.

    Wholesale replacement rather than a diff: a schedule is a set, and its rows carry no
    identity a caller could address. Existing appointments are deliberately not
    revalidated - they keep the times they were agreed at, and still block overlapping
    bookings.

    Raises: IntegrityError if the supplied ranges overlap on a weekday.

    Flushes rather than commits, so a caller editing a practitioner's fields and their
    schedule together can commit both as one transaction - otherwise a schedule rejected
    here would leave the field edits already persisted behind a rejected request.
    """
    await session.execute(
        sa_delete(WorkingRange).where(WorkingRange.practitioner_id == practitioner_id)
    )
    _add_ranges(session, practitioner_id, schedule)
    await session.flush()


async def delete(session: AsyncSession, practitioner: Practitioner) -> None:
    """Hard-delete `practitioner`.

    Their working ranges and appointments go with them by database cascade, not an
    application-level loop.
    """
    await session.delete(practitioner)
    await session.commit()
