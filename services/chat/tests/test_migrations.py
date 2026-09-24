from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from chat.core.config import Settings
from sqlalchemy.dialects.postgresql import JSONB
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


# --- 011: the verdict and the citations move onto the request ---------------------


def test_messages_carries_request_outcomes_instead_of_a_turn_level_verdict() -> None:
    engine, inspector = _inspector()
    columns = {col["name"]: col for col in inspector.get_columns("messages")}
    engine.dispose()

    # 008's typed verdict and its citation list were both properties of the turn. A
    # turn may now answer one request and abstain on another, so there is no turn-wide
    # value left for either to hold - and no code path may read the old shape (FR-044a).
    assert "grounded" not in columns
    assert "faq_verdict" not in columns
    assert "citations" not in columns
    assert columns["request_outcomes"]["nullable"] is True


def test_request_outcomes_is_a_jsonb_column() -> None:
    # Read only with the message that carries it and never joined on - the profile
    # `citations` and `reply_to_message_ids` already have on this table.
    engine, inspector = _inspector()
    columns = {col["name"]: col for col in inspector.get_columns("messages")}
    engine.dispose()

    assert isinstance(columns["request_outcomes"]["type"], JSONB)


def test_no_message_carries_an_outcome_this_migration_invented() -> None:
    # The migration backfills nothing (FR-044a): every session was deleted first, so
    # there was no stored message to translate. A row here would mean a backfill was
    # written after all, against rows nobody can check.
    engine, _ = _inspector()
    with engine.connect() as conn:
        found = conn.execute(
            sa.text("select count(*) from messages where request_outcomes is not null")
        ).scalar_one()
    engine.dispose()

    assert found == 0


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


# --- a thread's order is the order of its writes ------------------------------------


def test_messages_carry_a_write_order_sequence() -> None:
    engine, inspector = _inspector()
    columns = {col["name"]: col for col in inspector.get_columns("messages")}
    indexes = {index["name"]: index for index in inspector.get_indexes("messages")}
    engine.dispose()

    # An identity column, so the database assigns it at insert and nothing else can:
    # a thread's order comes from the order of its writes, not from any clock.
    assert columns["seq"]["nullable"] is False
    assert columns["seq"].get("identity") is not None
    assert indexes["ix_messages_chat_seq"]["column_names"] == ["chat_id", "seq"]


# --- 016: what the assistant did to the schedule ----------------------------------

_ACT_SESSION = "01ACTSESS10N00000000000000"
_ACT_CHAT = "01ACTCHAT00000000000000000"
_ACT_MESSAGE = "01ACTMESSAGE00000000000000"
_ACT_PRACTITIONER = "01ACTPRACT0000000000000000"


def test_booking_acts_has_the_columns_of_one_attempt() -> None:
    engine, inspector = _inspector()
    columns = {col["name"]: col for col in inspector.get_columns("booking_acts")}
    engine.dispose()

    assert set(columns) == {
        "id",
        "seq",
        "session_id",
        "chat_id",
        "message_id",
        "operation",
        "outcome",
        "refusal_reason",
        "appointment_id",
        "practitioner_id",
        "practitioner_full_name",
        "starts_at",
        "ends_at",
        "previous_practitioner_id",
        "previous_practitioner_full_name",
        "previous_starts_at",
        "created_at",
        "settled_at",
    }
    required = {
        "id",
        "seq",
        "session_id",
        "chat_id",
        "message_id",
        "operation",
        "practitioner_id",
        "starts_at",
        "created_at",
    }
    for name, column in columns.items():
        assert column["nullable"] is (name not in required), name
    # Ordered by the order of the writes, like `messages.seq`, never by a clock.
    assert columns["seq"].get("identity") is not None
    # The appointment's times are the clinic's local wall clock, offset-free; the two
    # diagnostic stamps are instants.
    for name in ("starts_at", "ends_at", "previous_starts_at"):
        assert columns[name]["type"].timezone is False, name
    for name in ("created_at", "settled_at"):
        assert columns[name]["type"].timezone is True, name


def test_booking_acts_die_with_their_chat_and_their_message() -> None:
    engine, inspector = _inspector()
    keys = inspector.get_foreign_keys("booking_acts")
    engine.dispose()

    by_column = {tuple(fk["constrained_columns"]): fk for fk in keys}
    assert set(by_column) == {("chat_id",), ("message_id",)}
    assert by_column[("chat_id",)]["referred_table"] == "chats"
    assert by_column[("message_id",)]["referred_table"] == "messages"
    for fk in by_column.values():
        assert fk["options"]["ondelete"] == "CASCADE"


def test_booking_acts_are_indexed_for_the_thread_read_and_by_message() -> None:
    engine, inspector = _inspector()
    indexes = {index["name"]: index for index in inspector.get_indexes("booking_acts")}
    engine.dispose()

    assert indexes["ix_booking_acts_chat_seq"]["column_names"] == ["chat_id", "seq"]
    assert indexes["ix_booking_acts_message"]["column_names"] == ["message_id"]


def _plant_act_owner(engine: sa.Engine) -> None:
    """Insert the session, chat and patient message an act row hangs off."""
    with engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO sessions (id) VALUES (:s)"), {"s": _ACT_SESSION}
        )
        connection.execute(
            sa.text("INSERT INTO chats (id, session_id) VALUES (:c, :s)"),
            {"c": _ACT_CHAT, "s": _ACT_SESSION},
        )
        connection.execute(
            sa.text(
                "INSERT INTO messages (id, chat_id, sender, content) "
                "VALUES (:m, :c, 'patient', 'book me in')"
            ),
            {"m": _ACT_MESSAGE, "c": _ACT_CHAT},
        )


def _remove_act_owner(engine: sa.Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM sessions WHERE id = :s"), {"s": _ACT_SESSION}
        )


# A well-formed act of each shape, which each case below breaks in exactly one way.
_VALID_BOOK: dict[str, object] = {
    "operation": "book",
    "outcome": None,
    "refusal_reason": None,
    "settled_at": None,
    "previous_practitioner_id": None,
    "previous_starts_at": None,
}
_VALID_RESCHEDULE: dict[str, object] = {
    **_VALID_BOOK,
    "operation": "reschedule",
    "previous_practitioner_id": _ACT_PRACTITIONER,
    "previous_starts_at": "2027-01-12 09:00:00",
}
_SETTLED = "2027-01-01 00:00:00+00"


def _insert_act(connection: sa.Connection, id: str, row: dict[str, object]) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO booking_acts (id, session_id, chat_id, message_id, operation, "
            "outcome, refusal_reason, practitioner_id, starts_at, "
            "previous_practitioner_id, previous_starts_at, settled_at) "
            "VALUES (:id, :s, :c, :m, :operation, :outcome, :refusal_reason, :p, "
            "'2027-01-12 10:00:00', :previous_practitioner_id, "
            "CAST(:previous_starts_at AS timestamp), "
            "CAST(:settled_at AS timestamptz))"
        ),
        {
            "id": id,
            "s": _ACT_SESSION,
            "c": _ACT_CHAT,
            "m": _ACT_MESSAGE,
            "p": _ACT_PRACTITIONER,
            **row,
        },
    )


def test_a_well_formed_act_of_each_shape_is_accepted() -> None:
    # The control for the cases below: each of them fails for the one thing it breaks,
    # not because the row it starts from was already unwritable.
    engine, _ = _inspector()
    _plant_act_owner(engine)
    try:
        with engine.begin() as connection:
            _insert_act(connection, "01ACTVA11D0000000000000001", _VALID_BOOK)
            _insert_act(connection, "01ACTVA11D0000000000000002", _VALID_RESCHEDULE)
            _insert_act(
                connection,
                "01ACTVA11D0000000000000003",
                {
                    **_VALID_BOOK,
                    "outcome": "refused",
                    "refusal_reason": "practitioner_busy",
                    "settled_at": _SETTLED,
                },
            )
    finally:
        _remove_act_owner(engine)
        engine.dispose()


@pytest.mark.parametrize(
    ("constraint", "row"),
    [
        ("ck_booking_acts_operation", {**_VALID_BOOK, "operation": "rebook"}),
        (
            "ck_booking_acts_outcome",
            {**_VALID_BOOK, "outcome": "maybe", "settled_at": _SETTLED},
        ),
        (
            # A refusal always names its reason ...
            "ck_booking_acts_refusal_reason_with_refused",
            {**_VALID_BOOK, "outcome": "refused", "settled_at": _SETTLED},
        ),
        (
            # ... and nothing but a refusal carries one - an unsettled act included.
            "ck_booking_acts_refusal_reason_with_refused",
            {**_VALID_BOOK, "refusal_reason": "practitioner_busy"},
        ),
        (
            "ck_booking_acts_refusal_reason_with_refused",
            {
                **_VALID_BOOK,
                "outcome": "done",
                "refusal_reason": "practitioner_busy",
                "settled_at": _SETTLED,
            },
        ),
        (
            # An outcome is written together with the moment it was settled ...
            "ck_booking_acts_settled_with_outcome",
            {**_VALID_BOOK, "outcome": "done"},
        ),
        (
            # ... and a settle moment never stands without its outcome.
            "ck_booking_acts_settled_with_outcome",
            {**_VALID_BOOK, "settled_at": _SETTLED},
        ),
        (
            # Only a reschedule has somewhere it moved from ...
            "ck_booking_acts_previous_with_reschedule",
            {
                **_VALID_BOOK,
                "previous_practitioner_id": _ACT_PRACTITIONER,
                "previous_starts_at": "2027-01-12 09:00:00",
            },
        ),
        (
            # ... and a reschedule always has both halves of it.
            "ck_booking_acts_previous_with_reschedule",
            {**_VALID_RESCHEDULE, "previous_starts_at": None},
        ),
        (
            "ck_booking_acts_previous_with_reschedule",
            {**_VALID_RESCHEDULE, "previous_practitioner_id": None},
        ),
    ],
)
def test_each_booking_act_check_rejects_the_row_it_exists_for(
    constraint: str, row: dict[str, object]
) -> None:
    engine, _ = _inspector()
    _plant_act_owner(engine)
    try:
        with pytest.raises(IntegrityError, match=constraint), engine.begin() as conn:
            _insert_act(conn, "01ACTNVA11D000000000000001", row)
    finally:
        _remove_act_owner(engine)
        engine.dispose()


def test_the_booking_acts_revision_downgrades_and_upgrades_cleanly() -> None:
    alembic_cfg = Config(str(_CHAT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(_CHAT_ROOT / "alembic"))
    engine = sa.create_engine(_sync_database_url())
    try:
        # An explicit revision, not "-1": a later head would otherwise silently change
        # which revision this round trip exercises.
        command.downgrade(alembic_cfg, "b7e2a9c41d05")
        assert "booking_acts" not in sa.inspect(engine).get_table_names()

        command.upgrade(alembic_cfg, "head")
        assert "booking_acts" in sa.inspect(engine).get_table_names()
    finally:
        # Head either way, so a failure mid-round-trip does not leave every later test
        # in this session running against a half-migrated schema.
        command.upgrade(alembic_cfg, "head")
        engine.dispose()
