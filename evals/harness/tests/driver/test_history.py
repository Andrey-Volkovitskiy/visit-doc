"""Planting a case's history straight into the chat database, before its turn."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from itertools import pairwise

import pytest
import pytest_asyncio
from chat.api.session_cookie import COOKIE_NAME
from chat.domain.models import Chat, Message, MessageSender, Session
from golden_harness.cases import HistoryEntry, HistoryRole
from golden_harness.driver.history import plant_history
from golden_harness.driver.session import ChatNotFoundError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from ulid import ULID

_SESSION_ID = "01K5SESSHISTORY00000000000"
_CHAT_ID = "01K5CHATHISTORY00000000000"

_HISTORY = [
    HistoryEntry(role=HistoryRole.USER, text="Can I book Monday at 9am?"),
    HistoryEntry(
        role=HistoryRole.ASSISTANT,
        text="Our clinic is open from 8am to 5pm on weekdays.\nAnything else?",
    ),
    HistoryEntry(role=HistoryRole.USER, text="  and on Saturdays?  "),
]


@pytest_asyncio.fixture
async def chat_in_session(
    chat_db: async_sessionmaker[AsyncSession],
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with chat_db() as session:
        session.add(Session(id=_SESSION_ID, created_at=datetime.now(UTC)))
        await session.flush()
        session.add(Chat(id=_CHAT_ID, session_id=_SESSION_ID))
        await session.commit()
    yield chat_db


async def _rows(db: async_sessionmaker[AsyncSession]) -> list[Message]:
    async with db() as session:
        result = await session.execute(
            select(Message)
            .where(Message.chat_id == _CHAT_ID)
            .order_by(Message.created_at.asc())
        )
        return list(result.scalars().all())


async def test_each_entry_becomes_one_message_row_in_order(
    chat_in_session: async_sessionmaker[AsyncSession],
) -> None:
    before = datetime.now(UTC)

    async with chat_in_session() as session:
        planted = await plant_history(
            session, _SESSION_ID, _CHAT_ID, _HISTORY, before=before
        )

    rows = await _rows(chat_in_session)
    assert [row.id for row in rows] == planted
    assert len(set(planted)) == len(_HISTORY)
    for planted_id in planted:
        ULID.from_str(planted_id)
    assert [row.sender for row in rows] == [
        MessageSender.PATIENT,
        MessageSender.ASSISTANT,
        MessageSender.PATIENT,
    ]
    assert [row.content for row in rows] == [entry.text for entry in _HISTORY]


async def test_planted_rows_are_strictly_ascending_and_all_before_the_turn(
    chat_in_session: async_sessionmaker[AsyncSession],
) -> None:
    before = datetime.now(UTC)

    async with chat_in_session() as session:
        await plant_history(session, _SESSION_ID, _CHAT_ID, _HISTORY, before=before)

    times = [row.created_at for row in await _rows(chat_in_session)]
    assert all(earlier < later for earlier, later in pairwise(times))
    assert all(time < before for time in times)


async def test_planted_rows_carry_no_outcomes_no_reply_links_and_no_mark(
    chat_in_session: async_sessionmaker[AsyncSession],
) -> None:
    async with chat_in_session() as session:
        await plant_history(
            session, _SESSION_ID, _CHAT_ID, _HISTORY, before=datetime.now(UTC)
        )

    for row in await _rows(chat_in_session):
        assert row.request_outcomes is None
        assert row.reply_to_message_ids is None
        assert row.attention_mark is None


async def test_no_history_plants_nothing(
    chat_in_session: async_sessionmaker[AsyncSession],
) -> None:
    async with chat_in_session() as session:
        planted = await plant_history(
            session, _SESSION_ID, _CHAT_ID, [], before=datetime.now(UTC)
        )

    assert planted == []
    assert await _rows(chat_in_session) == []


async def test_a_chat_of_another_session_is_not_written_into(
    chat_in_session: async_sessionmaker[AsyncSession],
) -> None:
    async with chat_in_session() as session:
        with pytest.raises(ChatNotFoundError):
            await plant_history(
                session,
                "01K5SOMEOTHERSESSION000000",
                _CHAT_ID,
                _HISTORY,
                before=datetime.now(UTC),
            )

    assert await _rows(chat_in_session) == []


async def test_a_naive_turn_time_is_refused(
    chat_in_session: async_sessionmaker[AsyncSession],
) -> None:
    naive = datetime.now(UTC).replace(tzinfo=None)

    async with chat_in_session() as session:
        with pytest.raises(ValueError, match="timezone"):
            await plant_history(session, _SESSION_ID, _CHAT_ID, _HISTORY, before=naive)


async def test_the_chat_service_lists_the_planted_history_in_order(
    chat_in_session: async_sessionmaker[AsyncSession],
) -> None:
    from chat.db.session import engine
    from chat.main import app
    from fastapi.testclient import TestClient

    async with chat_in_session() as session:
        await plant_history(
            session, _SESSION_ID, _CHAT_ID, _HISTORY, before=datetime.now(UTC)
        )

    try:
        client = TestClient(app, cookies={COOKIE_NAME: _SESSION_ID})
        response = client.get(f"/chats/{_CHAT_ID}/messages")
    finally:
        # The service's own engine binds to the loop the TestClient ran the request
        # on; dispose it so nothing later reuses a connection from that loop.
        await engine.dispose()

    assert response.status_code == 200
    messages = response.json()["messages"]
    assert [(m["sender"], m["content"]) for m in messages] == [
        ("patient", _HISTORY[0].text),
        ("assistant", _HISTORY[1].text),
        ("patient", _HISTORY[2].text),
    ]
