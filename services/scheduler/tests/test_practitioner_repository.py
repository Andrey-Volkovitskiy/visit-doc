"""Tests for practitioner creation defaults, and for what a bare create yields."""

from datetime import datetime, time
from unittest.mock import patch

import pytest
from scheduler.core.config import Settings
from scheduler.domain import availability
from scheduler.domain.name_pools import PHYSICIAN_POOL
from scheduler.repositories import practitioner_repository
from shared_models.scheduling import Specialty, Weekday
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import new_id

_SETTINGS = Settings(SCHEDULER_DATABASE_URL="postgresql+asyncpg://u:p@localhost/db")
_LOCAL_NOW = datetime(2026, 8, 17, 8, 0)
_TUESDAY = datetime(2026, 8, 18).date()


async def test_a_bare_create_applies_every_default(db_session: AsyncSession) -> None:
    session_id = new_id()

    practitioner = await practitioner_repository.create(db_session, session_id)

    assert practitioner.full_name == PHYSICIAN_POOL[0]
    assert practitioner.specialty == Specialty.GENERAL_PRACTICE
    assert practitioner.appointment_duration_minutes == 60

    schedule = await practitioner_repository.get_schedule(db_session, practitioner.id)
    assert [r.weekday for r in schedule] == [0, 1, 2, 3, 4]
    assert {(r.start_time, r.end_time) for r in schedule} == {(time(9, 0), time(17, 0))}


async def test_a_bare_create_is_immediately_bookable(db_session: AsyncSession) -> None:
    session_id = new_id()
    practitioner = await practitioner_repository.create(db_session, session_id)

    starts, truncated = availability.available_starts(
        schedule=practitioner_repository.to_daily_ranges(
            await practitioner_repository.get_schedule(db_session, practitioner.id)
        ),
        duration_minutes=practitioner.appointment_duration_minutes,
        busy=[],
        from_date=_TUESDAY,
        to_date=_TUESDAY,
        local_now=_LOCAL_NOW,
        horizon_days=90,
        max_window_days=_SETTINGS.AVAILABILITY_MAX_WINDOW_DAYS,
        max_slots=_SETTINGS.AVAILABILITY_MAX_SLOTS,
    )

    assert starts
    assert truncated is False


async def test_successive_bare_creates_walk_the_physician_pool(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()

    first = await practitioner_repository.create(db_session, session_id)
    second = await practitioner_repository.create(db_session, session_id)

    assert [first.full_name, second.full_name] == list(PHYSICIAN_POOL[:2])


async def test_two_sessions_each_start_at_the_top_of_the_pool(
    db_session: AsyncSession,
) -> None:
    first = await practitioner_repository.create(db_session, new_id())
    second = await practitioner_repository.create(db_session, new_id())

    assert first.full_name == second.full_name == PHYSICIAN_POOL[0]


async def test_a_supplied_name_and_specialty_are_used_verbatim(
    db_session: AsyncSession,
) -> None:
    practitioner = await practitioner_repository.create(
        db_session,
        new_id(),
        full_name="Someone Specific",
        specialty=Specialty.DENTISTRY,
        appointment_duration_minutes=30,
    )

    assert practitioner.full_name == "Someone Specific"
    assert practitioner.specialty == Specialty.DENTISTRY
    assert practitioner.appointment_duration_minutes == 30


async def test_an_explicitly_empty_schedule_yields_a_listed_but_unbookable_person(
    db_session: AsyncSession,
) -> None:
    practitioner = await practitioner_repository.create(
        db_session, new_id(), schedule=[]
    )

    assert await practitioner_repository.get_schedule(db_session, practitioner.id) == []


async def test_a_supplied_schedule_replaces_the_default(
    db_session: AsyncSession,
) -> None:
    practitioner = await practitioner_repository.create(
        db_session,
        new_id(),
        schedule=[
            (Weekday.TUESDAY, time(8, 0), time(12, 0)),
            (Weekday.TUESDAY, time(13, 0), time(16, 0)),
        ],
    )

    schedule = await practitioner_repository.get_schedule(db_session, practitioner.id)
    assert [(r.weekday, r.start_time) for r in schedule] == [
        (1, time(8, 0)),
        (1, time(13, 0)),
    ]


async def test_get_never_resolves_another_sessions_practitioner(
    db_session: AsyncSession,
) -> None:
    practitioner = await practitioner_repository.create(db_session, new_id())

    assert await practitioner_repository.get(db_session, practitioner.id, new_id()) is (
        None
    )


async def test_a_pool_name_taken_concurrently_is_retried_not_reported(
    db_session: AsyncSession,
) -> None:
    """The caller supplied no name, so a name conflict is not theirs to see.

    Simulates the race by making the first attempt read a stale (empty) taken-set, so
    it allocates a name another client already holds and the unique constraint fires.
    """
    session_id = new_id()
    first = await practitioner_repository.create(db_session, session_id)
    # Read before the retry: the rollback inside `create` expires every instance in
    # the session, so touching it afterwards would attempt IO from a sync context.
    first_name = first.full_name

    real_taken_names = practitioner_repository.taken_names
    calls = {"n": 0}

    async def _stale_once(session: AsyncSession, sid: str) -> set[str]:
        calls["n"] += 1
        if calls["n"] == 1:
            return set()  # the moment before the other client committed
        return await real_taken_names(session, sid)

    with patch.object(practitioner_repository, "taken_names", new=_stale_once):
        second = await practitioner_repository.create(db_session, session_id)

    assert calls["n"] >= 2, "the collision should have forced a second attempt"
    assert second.full_name != first_name
    assert second.full_name == PHYSICIAN_POOL[1]


async def test_a_supplied_name_that_collides_is_never_retried(
    db_session: AsyncSession,
) -> None:
    # The caller asked for this exact name; creating them under a different one would
    # be a worse answer than the error.
    session_id = new_id()
    await practitioner_repository.create(db_session, session_id, full_name="Dr. Taken")

    with pytest.raises(IntegrityError):
        await practitioner_repository.create(
            db_session, session_id, full_name="Dr. Taken"
        )


async def test_an_overlapping_schedule_is_not_retried(db_session: AsyncSession) -> None:
    # Retrying an overlap would just fail five times over, and the pool-name retry must
    # not swallow a constraint it cannot resolve.
    overlapping = [
        (Weekday.MONDAY, time(9, 0), time(12, 0)),
        (Weekday.MONDAY, time(11, 0), time(15, 0)),
    ]

    with pytest.raises(IntegrityError):
        await practitioner_repository.create(db_session, new_id(), schedule=overlapping)


async def test_seeding_declines_to_retry_so_a_race_yields_one_practitioner(
    db_session: AsyncSession,
) -> None:
    """`retry_pool_name=False` keeps the unique constraint as provisioning's guard.

    Without it the loser of a seeding race would succeed under the next pool name and
    leave the session with two practitioners.
    """
    session_id = new_id()
    await practitioner_repository.create(db_session, session_id)

    async def _stale(session: AsyncSession, sid: str) -> set[str]:
        return set()

    with (
        patch.object(practitioner_repository, "taken_names", new=_stale),
        pytest.raises(IntegrityError),
    ):
        await practitioner_repository.create(
            db_session, session_id, retry_pool_name=False
        )

    await db_session.rollback()
    assert (
        len(await practitioner_repository.list_for_session(db_session, session_id)) == 1
    )
