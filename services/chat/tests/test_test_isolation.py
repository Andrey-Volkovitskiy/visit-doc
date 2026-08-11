"""Regression tests for the test suite's own database isolation.

Guards against the exact bug fixed once already (docs/testing-strategy.md): a
module-level singleton built from a *cached* `get_settings()` call - `chat.db.session`'s
`engine` or `chat.repositories.qdrant_repository`'s `COLLECTION_NAME` - evaluated before
`conftest.py`'s `DATABASE_URL`/`QDRANT_COLLECTION_NAME` env overrides took effect, which
silently pointed the whole test run at the dev database/collection instead of the
isolated `_test`-suffixed ones. A fresh `Settings()` call always reflects the current
(already-overridden) env var regardless of that bug, so it wouldn't have caught it -
these tests inspect the live singletons themselves, the things that actually broke.
"""

from chat.core.config import Settings
from chat.db.session import engine, session_factory
from chat.domain.models import Chat, FaqEntry, Message, Session
from chat.repositories.qdrant_repository import (
    COLLECTION_NAME,
    create_client,
    ensure_collection,
)
from sqlalchemy import func, select


def test_database_engine_is_bound_to_the_isolated_test_database() -> None:
    assert engine.url.database == "visitdoc_test"


def test_qdrant_collection_name_is_the_isolated_test_collection() -> None:
    assert COLLECTION_NAME == "faq_chunks_test"


async def test_database_tables_are_empty_at_test_start() -> None:
    # `_clear_chat_tables` (conftest.py) truncates before every test - verify it held.
    async with session_factory() as session:
        session_count = await session.scalar(select(func.count()).select_from(Session))
        chat_count = await session.scalar(select(func.count()).select_from(Chat))
        message_count = await session.scalar(select(func.count()).select_from(Message))
        faq_entry_count = await session.scalar(
            select(func.count()).select_from(FaqEntry)
        )

    assert (session_count, chat_count, message_count, faq_entry_count) == (
        0,
        0,
        0,
        0,
    )


async def test_qdrant_collection_is_empty_at_test_start() -> None:
    """No test leaves stray points behind (`seeded_entry`/`delete_by_entry` clean up
    their own), so the isolated collection should have zero points between tests.
    """
    client = create_client(Settings())
    try:
        await ensure_collection(client)
        result = await client.count(collection_name=COLLECTION_NAME, exact=True)
        assert result.count == 0
    finally:
        await client.close()
