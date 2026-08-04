from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from chat.core.config import Settings

_CHAT_ROOT = Path(__file__).resolve().parents[1]


def _sync_database_url() -> str:
    return Settings().DATABASE_URL.replace(
        "postgresql+asyncpg://", "postgresql+psycopg://"
    )


def test_upgrade_head_creates_faq_entries_with_expected_columns() -> None:
    alembic_cfg = Config(str(_CHAT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(_CHAT_ROOT / "alembic"))
    command.upgrade(alembic_cfg, "head")

    engine = sa.create_engine(_sync_database_url())
    inspector = sa.inspect(engine)
    columns = {col["name"] for col in inspector.get_columns("faq_entries")}
    engine.dispose()

    assert columns == {"id", "content", "created_at", "updated_at"}
