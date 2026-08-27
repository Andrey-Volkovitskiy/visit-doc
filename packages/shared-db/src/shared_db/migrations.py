"""URL handling for Alembic, which runs synchronously against async-configured apps."""

import os


def sync_database_url(env_var: str) -> str:
    """Read `env_var` and swap its async driver for psycopg's sync one.

    Raises: RuntimeError if `env_var` is not set.

    Alembic runs its migrations synchronously while the services configure asyncpg, so
    the rewrite is what lets both read one URL. Shared rather than copied per service:
    changing the async driver later has to reach every migration environment at once, or
    the ones left behind fail only when someone next runs `alembic upgrade`.
    """
    url = os.environ.get(env_var)
    if url is None:
        raise RuntimeError(f"{env_var} is not set")
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
