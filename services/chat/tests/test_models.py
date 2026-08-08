import sqlalchemy as sa
from chat.domain.models import Chat, FaqEntry, Message, Session


def test_faq_entry_table_name() -> None:
    assert FaqEntry.__tablename__ == "faq_entries"


def test_faq_entry_id_is_integer_primary_key() -> None:
    id_column = FaqEntry.__table__.c.id
    assert id_column.primary_key
    assert id_column.type.python_type is int


def test_faq_entry_has_no_title_column() -> None:
    assert "title" not in FaqEntry.__table__.c


def test_session_table_name() -> None:
    assert Session.__tablename__ == "sessions"


def test_session_id_is_string_primary_key() -> None:
    id_column = Session.__table__.c.id
    assert id_column.primary_key
    assert id_column.type.python_type is str


def test_session_has_created_at() -> None:
    assert "created_at" in Session.__table__.c


def test_chat_table_name() -> None:
    assert Chat.__tablename__ == "chats"


def test_chat_id_is_string_primary_key() -> None:
    id_column = Chat.__table__.c.id
    assert id_column.primary_key
    assert id_column.type.python_type is str


def test_chat_session_id_foreign_key_cascades_on_delete() -> None:
    session_id_column = Chat.__table__.c.session_id
    fk = next(iter(session_id_column.foreign_keys))
    assert fk.column.table.name == "sessions"
    assert fk.ondelete == "CASCADE"
    assert session_id_column.nullable is False


def test_chat_session_id_has_no_uniqueness_constraint() -> None:
    # Exactly-one-active-chat-per-session (FR-009) is enforced in application logic, not
    # a DB constraint, so a later Patient layer can allow multiple chats per session
    # without dropping a constraint (data-model.md, research.md #1).
    session_id_column = Chat.__table__.c.session_id
    assert session_id_column.unique is not True


def test_message_table_name() -> None:
    assert Message.__tablename__ == "messages"


def test_message_id_is_string_primary_key() -> None:
    id_column = Message.__table__.c.id
    assert id_column.primary_key
    assert id_column.type.python_type is str


def test_message_chat_id_foreign_key_cascades_on_delete() -> None:
    chat_id_column = Message.__table__.c.chat_id
    fk = next(iter(chat_id_column.foreign_keys))
    assert fk.column.table.name == "chats"
    assert fk.ondelete == "CASCADE"
    assert chat_id_column.nullable is False


def test_message_sender_is_open_set_not_db_enum() -> None:
    # A plain string column, not a DB-level ENUM, so a third value ("staff", ROADMAP
    # Phase 1d) can be added later with no schema migration (FR-013).
    sender_column = Message.__table__.c.sender
    assert sender_column.type.python_type is str
    assert not isinstance(sender_column.type, sa.Enum)
    assert sender_column.nullable is False


def test_message_content_is_required() -> None:
    assert Message.__table__.c.content.nullable is False


def test_message_grounded_and_citations_are_nullable() -> None:
    assert Message.__table__.c.grounded.nullable is True
    assert Message.__table__.c.citations.nullable is True


def test_message_has_created_at() -> None:
    assert "created_at" in Message.__table__.c
