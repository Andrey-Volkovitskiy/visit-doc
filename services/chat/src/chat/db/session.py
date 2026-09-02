"""This service's engine and session factory, over the shared constructors."""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from shared_db import create_engine, create_session_factory
from sqlalchemy.ext.asyncio import AsyncSession

from chat.core.config import get_settings

engine = create_engine(get_settings().DATABASE_URL)
session_factory = create_session_factory(engine)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an `AsyncSession`."""
    async with session_factory() as session:
        yield session


@asynccontextmanager
async def pinned_session() -> AsyncGenerator[AsyncSession]:
    """Yield a session holding one connection for the whole block, commits included.

    An ordinary `session_factory()` session borrows a connection per transaction: it
    checks one out when a statement needs it and hands it straight back to the pool at
    the end of that transaction. That is right for almost everything here, and wrong
    for the one thing that outlives a transaction - a connection-scoped Postgres
    advisory lock. Taken on a borrowed connection, the lock stays on that connection
    when the next `commit()` returns it to the pool, and the release then runs on
    whichever connection the pool hands out next. Usually that is the same one and
    nothing looks wrong; when a sibling session checks a connection out in between it
    is a different one, `pg_advisory_unlock` reports it held nothing, and the lock is
    stranded on a pooled connection for the lifetime of the process - after which the
    chat it keys can never be locked again and every later attempt waits on it forever,
    `pg_advisory_lock` having no timeout.

    Checking the connection out here, and holding it until the block ends, is what
    makes "this connection" mean one connection for the whole locked section. The cost
    is that the connection is unavailable to anyone else for that whole time, so this
    is for sections that genuinely need it - see `chat_repository.lock_chat`, which
    refuses any other kind of session - not a general replacement for
    `session_factory`.
    """
    # The session is built through the shared factory rather than `AsyncSession(...)`
    # directly, so a pinned session keeps whatever options every other session in this
    # service has (today `expire_on_commit=False`); only the bind differs.
    async with (
        engine.connect() as connection,
        session_factory(bind=connection) as session,
    ):
        yield session
