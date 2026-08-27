"""Tests for patient creation: the pool walk as it actually runs against the database.

`test_naming.py` covers the allocation algorithm as a pure function. What is proven
here is the wiring - that the set of already-taken names really comes from this
session's own patients, so the exhaustion behavior holds end to end and not just in
the abstract.
"""

import pytest
from scheduler.domain.name_pools import WRITER_POOL
from scheduler.repositories import patient_repository
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import new_id


async def _create_patients(
    session: AsyncSession, session_id: str, count: int
) -> list[str]:
    """Create `count` patients in one session, returning their names in order."""
    names = []
    for _ in range(count):
        patient, created = await patient_repository.create_if_absent(
            session, session_id, new_id()
        )
        assert created is True
        names.append(patient.full_name)
    return names


async def test_a_chat_from_another_session_never_resolves(
    db_session: AsyncSession,
) -> None:
    """`chat_id` is unique, so no two rows collide on it - which is not permission.

    The scope belongs on the read: a lookup that returns the row and checks afterwards
    has already handed it over.
    """
    chat_id = new_id()
    patient, _ = await patient_repository.create_if_absent(
        db_session, new_id(), chat_id
    )

    assert await patient_repository.get_by_chat_id(db_session, chat_id, new_id()) is (
        None
    )
    assert (
        await patient_repository.get_by_chat_id(db_session, chat_id, patient.session_id)
    ) == patient


async def test_creating_against_another_sessions_chat_is_refused(
    db_session: AsyncSession,
) -> None:
    chat_id = new_id()
    await patient_repository.create_if_absent(db_session, new_id(), chat_id)

    with pytest.raises(patient_repository.ChatSessionMismatchError):
        await patient_repository.create_if_absent(db_session, new_id(), chat_id)


async def test_deleting_another_sessions_chat_removes_nothing(
    db_session: AsyncSession,
) -> None:
    chat_id = new_id()
    await patient_repository.create_if_absent(db_session, new_id(), chat_id)

    assert await patient_repository.delete_for_chat(db_session, new_id(), chat_id) == (
        False,
        0,
    )
    assert (
        await patient_repository.get_by_chat_id(db_session, chat_id, new_id()) is None
    )


async def test_the_first_patients_take_the_pool_in_order(
    db_session: AsyncSession,
) -> None:
    names = await _create_patients(db_session, new_id(), 5)

    assert names == list(WRITER_POOL[:5])


async def test_a_session_can_take_the_whole_pool_then_wrap_with_a_suffix(
    db_session: AsyncSession,
) -> None:
    """One session takes all 100 names; the 101st is the first name plus `" 2"`."""
    session_id = new_id()

    names = await _create_patients(db_session, session_id, len(WRITER_POOL) + 1)

    assert names[: len(WRITER_POOL)] == list(WRITER_POOL)
    assert names[len(WRITER_POOL)] == f"{WRITER_POOL[0]} 2"
    assert len(set(names)) == len(names)


async def test_the_same_creation_sequence_in_a_fresh_session_yields_the_same_names(
    db_session: AsyncSession,
) -> None:
    first = await _create_patients(db_session, new_id(), 30)
    second = await _create_patients(db_session, new_id(), 30)

    assert first == second


async def test_a_renamed_patient_frees_its_name_for_the_next_creation(
    db_session: AsyncSession,
) -> None:
    """A count-based shortcut would skip the freed name; the walk reuses it."""
    session_id = new_id()
    created = await _create_patients(db_session, session_id, 5)
    third = await patient_repository.list_for_session(db_session, session_id)
    await patient_repository.rename(db_session, third[2], "Renamed Entirely")

    next_name = (
        await patient_repository.create_if_absent(db_session, session_id, new_id())
    )[0].full_name

    assert next_name == created[2]


async def test_two_sessions_each_start_at_the_top_of_the_pool(
    db_session: AsyncSession,
) -> None:
    first = await _create_patients(db_session, new_id(), 1)
    second = await _create_patients(db_session, new_id(), 1)

    assert first == second == [WRITER_POOL[0]]
