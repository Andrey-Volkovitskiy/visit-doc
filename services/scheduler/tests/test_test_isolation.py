"""Regression tests for the test suite's own database isolation.

`scheduler.db.session`'s `engine` is a module-level singleton built from a *cached*
`get_settings()` call. If it were ever evaluated before `conftest.py`'s
`SCHEDULER_DATABASE_URL` override took effect, the whole suite would silently commit
against the dev database instead of the isolated `_test` one. A fresh `Settings()` call
always reflects the current (already-overridden) env var regardless of that bug, so it
would not catch it - this inspects the live singleton itself, the thing that would
actually break.

Mirrors `services/chat/tests/test_test_isolation.py`, which guards the same failure for
chat's engine and Qdrant collection.
"""

from scheduler.db.session import engine, session_factory
from scheduler.domain.models import all_table_names
from sqlalchemy import func, select, text


def test_database_engine_is_bound_to_the_isolated_test_database() -> None:
    assert engine.url.database == "visitdoc_scheduler_test"


async def test_scheduling_tables_are_empty_at_test_start() -> None:
    # `_clear_scheduling_tables` (conftest.py) truncates before every test - verify it
    # held, for every table the schema declares rather than a hand-listed subset.
    async with session_factory() as session:
        counts = {
            table: await session.scalar(select(func.count()).select_from(text(table)))
            for table in all_table_names()
        }

    assert counts == dict.fromkeys(all_table_names(), 0)
