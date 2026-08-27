"""Database wiring every service builds its engine and its migrations on.

Deliberately small. Only the pieces that must not differ between services live here:
how an engine is constructed, how a session factory is bound to it, and how an async
database URL is rewritten to the sync driver Alembic runs under. Each service still owns
its own module-level engine, bound to its own settings field.

Engine-level tuning - pool sizing, `pool_pre_ping`, TLS connect-args - belongs in
`create_engine` so it reaches both services at once. Applied to one service's private
copy it would silently give two services different connection behavior against the same
Postgres, which nothing would surface until one of them started dropping connections.
"""

from shared_db.engine import create_engine, create_session_factory
from shared_db.migrations import sync_database_url
from shared_db.testing import isolated_database_url, with_test_suffix

__all__ = [
    "create_engine",
    "create_session_factory",
    "isolated_database_url",
    "sync_database_url",
    "with_test_suffix",
]
