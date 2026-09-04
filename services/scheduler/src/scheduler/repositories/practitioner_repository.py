"""Practitioner and working-range reads and writes, scoped to one session.

Module-level functions taking the `AsyncSession` explicitly rather than a class holding
one, so the caller owns the transaction boundary and these stay reusable across the gRPC
and HTTP surfaces alike.
"""

from dataclasses import dataclass
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

_MONDAY_TO_FRIDAY = tuple(
    Weekday(day) for day in range(Weekday.MONDAY, Weekday.FRIDAY + 1)
)
_MONDAY_TO_SATURDAY = (*_MONDAY_TO_FRIDAY, Weekday.SATURDAY)


def _weekly(
    days: tuple[Weekday, ...], start: time, end: time
) -> tuple[tuple[Weekday, time, time], ...]:
    """Build one identical working range per listed weekday."""
    return tuple((day, start, end) for day in days)


DEFAULT_SCHEDULE: tuple[tuple[Weekday, time, time], ...] = _weekly(
    _MONDAY_TO_FRIDAY, time(9, 0), time(17, 0)
)


@dataclass(frozen=True)
class PractitionerSeed:
    """One practitioner a fresh session is provisioned with - everything but the name.

    A seed carries no name on purpose: names are drawn from the pool in allocation
    order at creation time, so a session that already holds some of those names still
    gets its full roster instead of a collision.
    """

    specialty: Specialty
    schedule: tuple[tuple[Weekday, time, time], ...]
    appointment_duration_minutes: int = DEFAULT_DURATION_MINUTES


# What a session is seeded with on its first visit, in creation order. Two
# practitioners rather than one, differing in both specialty and hours, so the first
# booking conversation has something to actually decide - which practitioner, and
# within which window - rather than a single answer the assistant can reach without
# asking. Order is part of the contract: it fixes which pool name each one gets.
SESSION_SEED: tuple[PractitionerSeed, ...] = (
    PractitionerSeed(
        specialty=DEFAULT_SPECIALTY,
        schedule=DEFAULT_SCHEDULE,
    ),
    PractitionerSeed(
        specialty=Specialty.DENTISTRY,
        schedule=_weekly(_MONDAY_TO_SATURDAY, time(9, 0), time(14, 0)),
    ),
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


async def _stage(
    session: AsyncSession,
    session_id: str,
    *,
    full_name: str,
    specialty: Specialty,
    appointment_duration_minutes: int,
    schedule: list[tuple[Weekday, time, time]] | tuple[tuple[Weekday, time, time], ...],
) -> Practitioner:
    """Add one practitioner and their working ranges, flushing but not committing.

    Flushed rather than committed so a caller staging several practitioners commits
    them as one transaction; the flush itself is what makes the row visible to the
    ranges' foreign key and to a later `taken_names` read in the same transaction.
    """
    practitioner = Practitioner(
        id=str(ULID()),
        session_id=session_id,
        full_name=full_name,
        specialty=specialty,
        appointment_duration_minutes=appointment_duration_minutes,
    )
    session.add(practitioner)
    await session.flush()
    _add_ranges(session, practitioner.id, schedule)
    await session.flush()
    return practitioner


async def create(
    session: AsyncSession,
    session_id: str,
    *,
    full_name: str | None = None,
    specialty: Specialty = DEFAULT_SPECIALTY,
    appointment_duration_minutes: int = DEFAULT_DURATION_MINUTES,
    schedule: list[tuple[Weekday, time, time]] | None = None,
) -> Practitioner:
    """Create a practitioner, applying the defaults for anything not supplied.

    Args:
        full_name: Omitted means the next unused physician-pool name.
        schedule: `(weekday, start_time, end_time)` triples. Omitted entirely means the
            default Monday-Friday schedule; an explicitly empty list means a
            practitioner who is listed but never bookable, which is a legal state.

    Raises:
        IntegrityError: `full_name` was supplied and is already used in this session,
            the supplied ranges overlap on a weekday, or a pool-name collision
            persisted past `MAX_NAME_ATTEMPTS`.

    A name this call chose from the pool is retried on collision rather than reported:
    the caller supplied no name, so "that name is taken" describes a decision they did
    not make and cannot act on. A name they *did* supply is never retried - creating
    them under a different name than they asked for would be worse than the error.

    One practitioner at a time, each in its own transaction. Seeding a session's whole
    roster is `seed_session`, which is atomic across the roster and must not be
    expressed as a loop over this.
    """
    from_pool = full_name is None

    for attempt in range(1, MAX_NAME_ATTEMPTS + 1):
        name = (
            allocate_name(
                PHYSICIAN_POOL,
                await taken_names(session, session_id),
                entity=NamedEntity.PRACTITIONER,
            )
            if full_name is None
            else full_name
        )
        try:
            practitioner = await _stage(
                session,
                session_id,
                full_name=name,
                specialty=specialty,
                appointment_duration_minutes=appointment_duration_minutes,
                schedule=DEFAULT_SCHEDULE if schedule is None else schedule,
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            # Only a pool name this call chose is retried. A supplied name, or any
            # other constraint (an overlapping range), is the caller's to see - and
            # retrying an overlap would just fail five times over.
            if not from_pool:
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


async def seed_session(session: AsyncSession, session_id: str) -> list[Practitioner]:
    """Create `SESSION_SEED`'s whole roster for a session that has no practitioners.

    Returns: the created practitioners, in `SESSION_SEED` order.

    Raises: IntegrityError if the session already holds any of the roster's names -
        which, given the precondition, means a concurrent first visit seeded it first.

    All of the roster or none of it, in one transaction. A half-seeded session is
    indistinguishable from one an app user deleted a practitioner from, and nothing
    would ever repair it: seeding is guarded on the session having *no* practitioners,
    so a later visit would leave the gap in place.

    Names are taken from the top of the pool without reading what the session already
    holds - the only place a name is chosen that way. Two reasons, and both matter.
    Nothing is taken yet, by the precondition. And a race is arbitrated entirely by the
    name's UNIQUE constraint, which can only fire if the loser insists on the same
    names: reading the taken set would let a loser whose read landed *after* the
    winner's commit allocate around it and append a second roster, the exact outcome
    the guard exists to prevent.

    A collision is never retried here, unlike `create`. It cannot be raised before the
    winner's transaction committed, so the loser rolls back and re-reads a complete
    roster.
    """
    taken: set[str] = set()
    created: list[Practitioner] = []
    for seed in SESSION_SEED:
        name = allocate_name(PHYSICIAN_POOL, taken, entity=NamedEntity.PRACTITIONER)
        taken.add(name)
        created.append(
            await _stage(
                session,
                session_id,
                full_name=name,
                specialty=seed.specialty,
                appointment_duration_minutes=seed.appointment_duration_minutes,
                schedule=seed.schedule,
            )
        )
    await session.commit()
    for practitioner in created:
        await session.refresh(practitioner)
    return created


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


async def delete_for_session(session: AsyncSession, session_id: str) -> int:
    """Delete every practitioner `session_id` owns. Return how many were removed.

    Scoped on the `DELETE` itself, for the same reason every other statement here is:
    a row belonging to another session must not resolve at all, rather than be caught
    by a check after it has already been read.
    """
    result = await session.execute(
        sa_delete(Practitioner)
        .where(Practitioner.session_id == session_id)
        .returning(Practitioner.id)
    )
    return len(list(result.scalars().all()))
