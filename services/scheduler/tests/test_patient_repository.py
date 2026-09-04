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
) -> tuple[list[str], list[str]]:
    """Create `count` patients in one session.

    Returns: their names in creation order, and the chat id each was created for, in
    that same order.
    """
    names = []
    chat_ids = []
    for _ in range(count):
        chat_id = new_id()
        patient, created = await patient_repository.create_if_absent(
            session, session_id, chat_id
        )
        assert created is True
        names.append(patient.full_name)
        chat_ids.append(chat_id)
    return names, chat_ids


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
    names, _ = await _create_patients(db_session, new_id(), 5)

    assert names == list(WRITER_POOL[:5])


async def test_a_session_can_take_the_whole_pool_then_wrap_with_a_suffix(
    db_session: AsyncSession,
) -> None:
    """One session takes all 100 names; the 101st is the first name plus `" 2"`."""
    session_id = new_id()

    names, _ = await _create_patients(db_session, session_id, len(WRITER_POOL) + 1)

    assert names[: len(WRITER_POOL)] == list(WRITER_POOL)
    assert names[len(WRITER_POOL)] == f"{WRITER_POOL[0]} 2"
    assert len(set(names)) == len(names)


async def test_the_same_creation_sequence_in_a_fresh_session_yields_the_same_names(
    db_session: AsyncSession,
) -> None:
    first, _ = await _create_patients(db_session, new_id(), 30)
    second, _ = await _create_patients(db_session, new_id(), 30)

    assert first == second


async def test_a_deleted_patient_frees_its_name_for_the_next_creation(
    db_session: AsyncSession,
) -> None:
    """A count-based shortcut would collide with a held name; the walk does not."""
    session_id = new_id()
    created, chat_ids = await _create_patients(db_session, session_id, 5)
    await patient_repository.delete_for_chat(db_session, session_id, chat_ids[2])

    next_name = (
        await patient_repository.create_if_absent(db_session, session_id, new_id())
    )[0].full_name

    assert next_name == created[2]


async def test_two_sessions_each_start_at_the_top_of_the_pool(
    db_session: AsyncSession,
) -> None:
    first, _ = await _create_patients(db_session, new_id(), 1)
    second, _ = await _create_patients(db_session, new_id(), 1)

    assert first == second == [WRITER_POOL[0]]


async def test_a_patients_updated_at_never_moves_off_created_at(
    db_session: AsyncSession,
) -> None:
    """Nothing updates a patient, so the audit pair stays equal for the row's life.

    Every operation the repository offers on an existing patient runs here, and the
    row is re-read from the database afterwards rather than trusted from the session's
    identity map. An update path added later without an answer for this fails here.
    """
    session_id = new_id()
    chat_id = new_id()
    patient, _ = await patient_repository.create_if_absent(
        db_session, session_id, chat_id
    )
    patient_id = patient.id
    created_at = patient.created_at

    await patient_repository.create_if_absent(db_session, session_id, chat_id)
    await patient_repository.get(db_session, patient_id, session_id)
    await patient_repository.list_for_session(db_session, session_id)
    await patient_repository.taken_names(db_session, session_id)
    await db_session.commit()

    db_session.expire_all()
    reread = await patient_repository.get(db_session, patient_id, session_id)
    assert reread is not None
    assert reread.created_at == created_at
    assert reread.updated_at == created_at
