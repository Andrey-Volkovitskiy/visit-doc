"""Tests for patient creation: the pool walk as it actually runs against the database.

`test_naming.py` covers the allocation algorithm as a pure function. What is proven
here is the wiring - that the set of already-taken names really comes from this
session's own patients, so the exhaustion behavior holds end to end and not just in
the abstract.
"""

from datetime import datetime

import pytest
from scheduler.domain.models import Patient
from scheduler.domain.name_pools import WRITER_POOL
from scheduler.repositories import patient_repository
from sqlalchemy import text, update
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


async def _stored_updated_at(
    session: AsyncSession, patient_id: str, session_id: str
) -> datetime:
    """Return `updated_at` as the database now holds it for `patient_id`.

    Expires the identity map first, so the value is a fresh read rather than whatever
    the session already had in memory - an `onupdate` is evaluated by the database
    inside the `UPDATE`, so the in-memory attribute is stale by definition.
    """
    session.expire_all()
    patient = await patient_repository.get(session, patient_id, session_id)
    assert patient is not None
    return patient.updated_at


async def test_a_patient_written_through_sqlalchemy_carries_updated_at_forward(
    db_session: AsyncSession,
) -> None:
    """`onupdate` is what keeps the audit pair honest if an update path is ever added.

    Nothing updates a patient today, so what runs here is the column declaration
    rather than a caller: an ORM flush and a Core `update()` are the two shapes such a
    path could take, and both must move `updated_at` off `created_at`. Dropping
    `onupdate` from `Patient` fails this test. A raw-SQL write is the documented limit
    of the guarantee - SQLAlchemy emits the timestamp, no trigger does - so that leg
    asserts the column stays put rather than moves.
    """
    session_id = new_id()
    patient, _ = await patient_repository.create_if_absent(
        db_session, session_id, new_id()
    )
    patient_id = patient.id
    created_at = patient.created_at

    patient.full_name = "Renamed By An Orm Flush"
    await db_session.commit()
    after_flush = await _stored_updated_at(db_session, patient_id, session_id)

    await db_session.execute(
        update(Patient)
        .where(Patient.id == patient_id)
        .values(full_name="Renamed By A Core Update")
    )
    await db_session.commit()
    after_core_update = await _stored_updated_at(db_session, patient_id, session_id)

    await db_session.execute(
        text("UPDATE patients SET full_name = :name WHERE id = :id"),
        {"name": "Renamed By Raw Sql", "id": patient_id},
    )
    await db_session.commit()
    after_raw_sql = await _stored_updated_at(db_session, patient_id, session_id)

    assert after_flush > created_at
    assert after_core_update > after_flush
    assert after_raw_sql == after_core_update
