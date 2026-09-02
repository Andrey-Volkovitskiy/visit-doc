from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from chat.core.config import Settings
from sqlalchemy.exc import IntegrityError

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

    assert columns == {
        "id",
        "content",
        "created_at",
        "updated_at",
        # 007: an entry belongs to one session and names the one revision of its
        # indexed chunks that retrieval may search.
        "session_id",
        "live_revision",
    }


def _inspector() -> tuple[sa.Engine, sa.Inspector]:
    """Return an engine on the test database and an inspector over it.

    Returns: the engine (the caller disposes it) and its inspector.
    """
    alembic_cfg = Config(str(_CHAT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(_CHAT_ROOT / "alembic"))
    command.upgrade(alembic_cfg, "head")
    engine = sa.create_engine(_sync_database_url())
    return engine, sa.inspect(engine)


def _check_constraints(engine: sa.Engine, table: str) -> dict[str, str]:
    """Return every CHECK constraint on `table`, as name -> its SQL expression."""
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT con.conname, pg_get_constraintdef(con.oid) "
                "FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "WHERE rel.relname = :table AND con.contype = 'c'"
            ),
            {"table": table},
        ).all()
    return dict(rows)  # type: ignore[arg-type]


# --- 007: the conversation's two axes, and the message mark ------------------------


def test_chats_gains_the_four_conversation_state_columns() -> None:
    engine, inspector = _inspector()
    columns = {col["name"]: col for col in inspector.get_columns("chats")}
    engine.dispose()

    for name in (
        "escalated_at",
        "escalation_reason",
        "assistant_paused_until",
        "attention_since",
    ):
        assert name in columns, f"{name} missing from chats"
        # Nullable by nature, not by omission: an ordinary open conversation is one
        # where every single one of these is NULL.
        assert columns[name]["nullable"] is True


def test_an_escalation_cannot_exist_without_its_reason() -> None:
    # The datastore carries the invariant, not application code: an escalation always
    # carries exactly one reason, and a reason never outlives the escalation.
    engine, _ = _inspector()
    definitions = " ".join(_check_constraints(engine, "chats").values()).lower()
    engine.dispose()

    assert "escalated_at" in definitions
    assert "escalation_reason" in definitions


def test_the_reason_check_rejects_each_half_of_the_pair_alone() -> None:
    engine, _ = _inspector()
    with engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO sessions (id) VALUES ('01MIGRATIONSESSION000000AA')")
        )
        connection.execute(
            sa.text(
                "INSERT INTO chats (id, session_id) "
                "VALUES ('01MIGRATIONCHAT0000000000A', '01MIGRATIONSESSION000000AA')"
            )
        )

    for column, value in (
        ("escalated_at", "now()"),
        ("escalation_reason", "'patient_asked_for_person'"),
    ):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                sa.text(
                    f"UPDATE chats SET {column} = {value} "
                    "WHERE id = '01MIGRATIONCHAT0000000000A'"
                )
            )

    with engine.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM sessions WHERE id = '01MIGRATIONSESSION000000AA'")
        )
    engine.dispose()


def test_the_console_listing_index_exists() -> None:
    engine, inspector = _inspector()
    names = {index["name"] for index in inspector.get_indexes("chats")}
    engine.dispose()

    assert "ix_chats_session_attention" in names


def test_messages_gains_a_partially_indexed_attention_mark() -> None:
    engine, inspector = _inspector()
    columns = {col["name"]: col for col in inspector.get_columns("messages")}
    indexes = {index["name"]: index for index in inspector.get_indexes("messages")}
    engine.dispose()

    assert columns["attention_mark"]["nullable"] is True
    # Partial: the clearing statement and the "does this chat hold a mark" read both
    # address only marked rows, and marked rows are a small minority of a chat's
    # messages.
    index = indexes["ix_messages_chat_attention_mark"]
    assert index["column_names"] == ["chat_id", "attention_mark"]
    assert index.get("dialect_options", {}).get("postgresql_where") is not None


# --- 007: an FAQ entry has an owner and names a live revision ----------------------


def test_faq_entries_ownership_columns_are_both_not_null() -> None:
    # NOT NULL is only reachable because this migration empties the table first. It is
    # what makes "an entry belonging to nobody" a state that cannot be written, rather
    # than one every reader has to remember to filter out.
    engine, inspector = _inspector()
    columns = {col["name"]: col for col in inspector.get_columns("faq_entries")}
    engine.dispose()

    assert columns["session_id"]["nullable"] is False
    assert columns["live_revision"]["nullable"] is False


def test_faq_entries_are_owned_by_a_session_and_die_with_it() -> None:
    engine, inspector = _inspector()
    keys = inspector.get_foreign_keys("faq_entries")
    engine.dispose()

    owner = next(fk for fk in keys if fk["constrained_columns"] == ["session_id"])
    assert owner["referred_table"] == "sessions"
    assert owner["options"]["ondelete"] == "CASCADE"


def test_faq_entries_has_no_check_constraint_on_the_ownership_pair() -> None:
    # An earlier design made both columns nullable and used a two-armed CHECK to stop
    # them disagreeing. NOT NULL says the same thing with nothing left to disagree.
    engine, _ = _inspector()
    definitions = _check_constraints(engine, "faq_entries")
    engine.dispose()

    assert definitions == {}


def test_an_ownerless_faq_entry_is_rejected_by_the_datastore() -> None:
    engine, _ = _inspector()

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO faq_entries (content, session_id, live_revision) "
                "VALUES ('orphan', NULL, '01REVISION0000000000000AA')"
            )
        )
    engine.dispose()


def test_the_session_scoped_faq_index_exists() -> None:
    engine, inspector = _inspector()
    names = {index["name"] for index in inspector.get_indexes("faq_entries")}
    engine.dispose()

    assert "ix_faq_entries_session" in names
