"""Tests for `booking_act_repository`: the write-ahead record of what was attempted.

Every statement carries the session predicate, so an id from another session resolves
to nothing rather than being caught by a check after the fact.
"""

from datetime import datetime

from chat.db.session import session_factory
from chat.domain.models import (
    BookingAct,
    BookingActOperation,
    BookingActOutcome,
    MessageSender,
)
from chat.repositories import booking_act_repository, chat_repository
from sqlalchemy import select
from ulid import ULID

_PRACTITIONER = "01PRACT0000000000000000000"
_OTHER_PRACTITIONER = "01PRACT0000000000000000001"
_STARTS_AT = datetime(2027, 1, 12, 10, 0)


async def _patient_message() -> tuple[str, str, str]:
    """Create a session, a chat in it and a patient message in that chat.

    Returns: the session id, the chat id and the message id.
    """
    async with session_factory() as session:
        owner = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, owner.id)
        message = await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=chat.id,
            session_id=owner.id,
            sender=MessageSender.PATIENT,
            content="book me in",
        )
    assert message is not None
    return owner.id, chat.id, message.id


async def _begin_book(session_id: str, chat_id: str, message_id: str) -> str | None:
    async with session_factory() as session:
        return await booking_act_repository.begin(
            session,
            session_id=session_id,
            chat_id=chat_id,
            message_id=message_id,
            operation=BookingActOperation.BOOK,
            practitioner_id=_PRACTITIONER,
            practitioner_full_name="William Osler",
            starts_at=_STARTS_AT,
        )


async def _row(act_id: str) -> BookingAct:
    async with session_factory() as session:
        found = await session.get(BookingAct, act_id)
    assert found is not None
    return found


async def _settle_done(act_id: str, session_id: str, name: str) -> bool:
    async with session_factory() as session:
        return await booking_act_repository.settle(
            session,
            act_id=act_id,
            session_id=session_id,
            outcome=BookingActOutcome.DONE,
            practitioner_full_name=name,
            ends_at=datetime(2027, 1, 12, 11, 0),
            appointment_id="01APPT00000000000000000000",
        )


# --- begin -----------------------------------------------------------------------


async def test_begin_writes_an_unsettled_act_on_the_patient_message() -> None:
    session_id, chat_id, message_id = await _patient_message()

    act_id = await _begin_book(session_id, chat_id, message_id)

    assert act_id is not None
    row = await _row(act_id)
    assert (row.session_id, row.chat_id, row.message_id) == (
        session_id,
        chat_id,
        message_id,
    )
    assert row.operation == BookingActOperation.BOOK
    assert row.outcome is None
    assert row.settled_at is None
    assert row.practitioner_id == _PRACTITIONER
    assert row.practitioner_full_name == "William Osler"
    assert row.starts_at == _STARTS_AT
    assert row.appointment_id is None
    assert row.ends_at is None


async def test_begin_carries_where_a_reschedule_moved_from() -> None:
    session_id, chat_id, message_id = await _patient_message()

    async with session_factory() as session:
        act_id = await booking_act_repository.begin(
            session,
            session_id=session_id,
            chat_id=chat_id,
            message_id=message_id,
            operation=BookingActOperation.RESCHEDULE,
            practitioner_id=_OTHER_PRACTITIONER,
            practitioner_full_name=None,
            starts_at=_STARTS_AT,
            appointment_id="01APPT00000000000000000000",
            previous_practitioner_id=_PRACTITIONER,
            previous_practitioner_full_name="William Osler",
            previous_starts_at=datetime(2027, 1, 12, 9, 0),
        )

    assert act_id is not None
    row = await _row(act_id)
    assert row.previous_practitioner_id == _PRACTITIONER
    assert row.previous_practitioner_full_name == "William Osler"
    assert row.previous_starts_at == datetime(2027, 1, 12, 9, 0)
    assert row.practitioner_full_name is None


async def test_begin_writes_nothing_for_a_message_of_another_session() -> None:
    _, chat_id, message_id = await _patient_message()
    other_session_id, _, _ = await _patient_message()

    act_id = await _begin_book(other_session_id, chat_id, message_id)

    assert act_id is None
    async with session_factory() as session:
        rows = (await session.execute(select(BookingAct))).scalars().all()
    assert rows == []


async def test_begin_writes_nothing_for_a_message_of_another_chat() -> None:
    session_id, chat_id, _ = await _patient_message()
    _, _, foreign_message_id = await _patient_message()

    assert await _begin_book(session_id, chat_id, foreign_message_id) is None


# --- insert_not_sent ---------------------------------------------------------------


async def test_insert_not_sent_writes_an_already_settled_act() -> None:
    session_id, chat_id, message_id = await _patient_message()

    async with session_factory() as session:
        act_id = await booking_act_repository.insert_not_sent(
            session,
            session_id=session_id,
            chat_id=chat_id,
            message_id=message_id,
            operation=BookingActOperation.CANCEL,
            practitioner_id=_PRACTITIONER,
            practitioner_full_name="William Osler",
            starts_at=_STARTS_AT,
            appointment_id="01APPT00000000000000000000",
        )

    assert act_id is not None
    row = await _row(act_id)
    assert row.outcome == BookingActOutcome.NOT_SENT
    assert row.settled_at is not None
    assert row.refusal_reason is None
    assert row.appointment_id == "01APPT00000000000000000000"


async def test_insert_not_sent_writes_nothing_for_a_message_of_another_session() -> (
    None
):
    _, chat_id, message_id = await _patient_message()
    other_session_id, _, _ = await _patient_message()

    async with session_factory() as session:
        act_id = await booking_act_repository.insert_not_sent(
            session,
            session_id=other_session_id,
            chat_id=chat_id,
            message_id=message_id,
            operation=BookingActOperation.BOOK,
            practitioner_id=_PRACTITIONER,
            practitioner_full_name=None,
            starts_at=_STARTS_AT,
        )

    assert act_id is None


# --- settle ------------------------------------------------------------------------


async def test_settle_writes_the_outcome_and_what_the_answer_reported() -> None:
    session_id, chat_id, message_id = await _patient_message()
    act_id = await _begin_book(session_id, chat_id, message_id)
    assert act_id is not None

    settled = await _settle_done(act_id, session_id, "Sir William Osler")

    assert settled is True
    row = await _row(act_id)
    assert row.outcome == BookingActOutcome.DONE
    assert row.settled_at is not None
    assert row.practitioner_full_name == "Sir William Osler"
    assert row.ends_at == datetime(2027, 1, 12, 11, 0)
    assert row.appointment_id == "01APPT00000000000000000000"


async def test_settle_keeps_what_the_answer_did_not_report() -> None:
    session_id, chat_id, message_id = await _patient_message()
    act_id = await _begin_book(session_id, chat_id, message_id)
    assert act_id is not None

    async with session_factory() as session:
        await booking_act_repository.settle(
            session,
            act_id=act_id,
            session_id=session_id,
            outcome=BookingActOutcome.UNKNOWN,
        )

    row = await _row(act_id)
    assert row.outcome == BookingActOutcome.UNKNOWN
    assert row.practitioner_full_name == "William Osler"
    assert row.starts_at == _STARTS_AT


async def test_settle_records_a_refusal_with_its_reason() -> None:
    session_id, chat_id, message_id = await _patient_message()
    act_id = await _begin_book(session_id, chat_id, message_id)
    assert act_id is not None

    async with session_factory() as session:
        await booking_act_repository.settle(
            session,
            act_id=act_id,
            session_id=session_id,
            outcome=BookingActOutcome.REFUSED,
            refusal_reason="practitioner_busy",
        )

    row = await _row(act_id)
    assert row.outcome == BookingActOutcome.REFUSED
    assert row.refusal_reason == "practitioner_busy"


async def test_a_second_settle_changes_nothing() -> None:
    session_id, chat_id, message_id = await _patient_message()
    act_id = await _begin_book(session_id, chat_id, message_id)
    assert act_id is not None
    await _settle_done(act_id, session_id, "Sir William Osler")
    first = await _row(act_id)

    again = await _settle_done(act_id, session_id, "Someone Else")

    assert again is False
    second = await _row(act_id)
    assert second.practitioner_full_name == "Sir William Osler"
    assert second.settled_at == first.settled_at


async def test_settle_from_another_session_changes_nothing() -> None:
    session_id, chat_id, message_id = await _patient_message()
    other_session_id, _, _ = await _patient_message()
    act_id = await _begin_book(session_id, chat_id, message_id)
    assert act_id is not None

    settled = await _settle_done(act_id, other_session_id, "Someone Else")

    assert settled is False
    row = await _row(act_id)
    assert row.outcome is None
    assert row.settled_at is None
    assert row.practitioner_full_name == "William Osler"


# --- list_for_chat -----------------------------------------------------------------


async def test_list_for_chat_returns_the_acts_in_the_order_they_were_attempted() -> (
    None
):
    session_id, chat_id, message_id = await _patient_message()
    first = await _begin_book(session_id, chat_id, message_id)
    second = await _begin_book(session_id, chat_id, message_id)
    third = await _begin_book(session_id, chat_id, message_id)

    async with session_factory() as session:
        acts = await booking_act_repository.list_for_chat(session, chat_id, session_id)

    assert [act.id for act in acts] == [first, second, third]


async def test_list_for_chat_holds_nothing_for_another_session() -> None:
    session_id, chat_id, message_id = await _patient_message()
    other_session_id, other_chat_id, other_message_id = await _patient_message()
    await _begin_book(session_id, chat_id, message_id)
    await _begin_book(other_session_id, other_chat_id, other_message_id)

    async with session_factory() as session:
        foreign = await booking_act_repository.list_for_chat(
            session, chat_id, other_session_id
        )
        own = await booking_act_repository.list_for_chat(session, chat_id, session_id)

    assert foreign == []
    assert [act.chat_id for act in own] == [chat_id]


# --- lifetime ----------------------------------------------------------------------


async def test_deleting_the_chat_removes_its_acts() -> None:
    session_id, chat_id, message_id = await _patient_message()
    await _begin_book(session_id, chat_id, message_id)

    async with session_factory() as session:
        await chat_repository.delete_chat(session, chat_id, session_id)

    async with session_factory() as session:
        rows = (await session.execute(select(BookingAct))).scalars().all()
    assert rows == []
