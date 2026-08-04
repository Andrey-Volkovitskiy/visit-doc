from chat.core.config import Settings
from chat.db.session import create_engine, create_session_factory
from sqlalchemy.ext.asyncio import AsyncSession


def _settings() -> Settings:
    return Settings(
        DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db",
        QDRANT_URL="http://localhost:6333",
        ANTHROPIC_API_KEY="key",
        VOYAGE_API_KEY="key",
    )


async def test_session_factory_builds_session_bound_to_configured_database() -> None:
    engine = create_engine(_settings())
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        assert isinstance(session, AsyncSession)
        assert session.bind is engine

    assert engine.url.drivername == "postgresql+asyncpg"
    assert engine.url.database == "db"

    await engine.dispose()
