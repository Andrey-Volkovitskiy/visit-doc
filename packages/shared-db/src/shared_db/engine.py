"""Async engine and session-factory construction, shared by every service."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_POOL_RECYCLE_SECONDS = 300


def create_engine(database_url: str) -> AsyncEngine:
    """Build the async engine for `database_url`.

    Pooled connections are both pre-pinged and recycled by age. The two settings cover
    different halves of the same failure, and neither covers it alone.

    A connection the far side closed politely - a FIN we received - is dead in a way
    the pool can see, and `pool_pre_ping` is what looks: the ping fails on checkout,
    that connection is discarded, and a fresh one is opened before the caller's
    statement ever runs. Without it the caller gets an `InterfaceError` ("connection is
    closed") raised from whatever statement happened to be next, which is the request
    that pays for an idle period it had nothing to do with.

    A connection dropped *silently* is the other half. Nothing arrives - the socket
    simply stops carrying traffic, which is what a NAT or port-proxy hop in front of
    Postgres does to a connection it has decided is idle - so from here it still looks
    open, and the pre-ping is itself a statement over that socket: it hangs until the
    OS TCP timeout, tens of seconds later, and the request hangs with it. Pre-pinging
    cannot detect this; `pool_recycle` sidesteps it, by refusing to hand out any
    connection older than `_POOL_RECYCLE_SECONDS` however healthy it looks, so a
    connection is never kept long enough to reach the idle window in which it gets
    dropped. Five minutes sits comfortably under the shortest such window observed, and
    costs one extra handshake per connection per five minutes.
    """
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_recycle=_POOL_RECYCLE_SECONDS,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build a session factory bound to `engine`.

    `expire_on_commit` is off, so objects loaded before a commit stay usable after it
    without a refresh - which callers rely on to render a row they just wrote.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
