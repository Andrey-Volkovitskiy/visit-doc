import sqlalchemy as sa
from chat.domain.models import (
    CLEARABLE_MARKS,
    AttentionMark,
    Chat,
    EscalationReason,
    FaqEntry,
    Message,
    MessageSender,
    Session,
)


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


# --- 007: the three closed sets a conversation's state is written from -------------


def test_message_sender_has_exactly_three_members() -> None:
    """A conversation holds messages from three senders and no more (FR-020).

    Pinned as an exact set rather than a membership check: a fourth value added
    without a migration would widen `sender` silently, and `split_into_bursts`
    partitions on patient-or-not, so a new non-patient sender would join the
    clinic's side of the conversation without anyone deciding that it should.
    """
    assert {member.value for member in MessageSender} == {
        "patient",
        "assistant",
        "staff",
    }


def test_attention_mark_has_exactly_the_four_kinds() -> None:
    """FR-027a's four kinds, and nothing else."""
    assert {member.value for member in AttentionMark} == {
        "patient_asked_for_person",
        "corpus_could_not_answer",
        "assistant_failed",
        "unanswered",
    }


def test_escalation_reason_has_exactly_the_three_triggers() -> None:
    """FR-007a: the reasons are the triggers, and there is no fourth value."""
    assert {member.value for member in EscalationReason} == {
        "patient_asked_for_person",
        "corpus_could_not_answer",
        "assistant_failed",
    }


def test_escalation_reasons_are_a_subset_of_mark_kinds() -> None:
    """FR-027b: the first three mark kinds correspond exactly to the three reasons.

    Pinned member-by-member because the two enums are declared separately and could
    drift into disagreeing about a value's spelling - at which point a call to staff
    would set a mark nothing recognizes.
    """
    for reason in EscalationReason:
        assert reason.value in {member.value for member in AttentionMark}


def test_clearable_marks_are_exactly_the_two_a_staff_message_clears() -> None:
    """FR-027c's lifetime column, as one constant.

    This is the `IN` list of the clearing statement. A permanent mark appearing here
    would erase a diagnostic record; a clearable one missing would leave an answered
    request outstanding forever - and both are invisible in any test that only checks
    that *something* was cleared.
    """
    assert CLEARABLE_MARKS == frozenset(
        {AttentionMark.PATIENT_ASKED_FOR_PERSON, AttentionMark.UNANSWERED}
    )


def test_permanent_marks_are_the_complement_and_record_a_system_gap() -> None:
    """The other two kinds never clear: a staff member answering the patient does not
    mean the corpus gained the entry it was missing, or that the failure did not
    happen (FR-027c).
    """
    permanent = set(AttentionMark) - CLEARABLE_MARKS
    assert permanent == {
        AttentionMark.CORPUS_COULD_NOT_ANSWER,
        AttentionMark.ASSISTANT_FAILED,
    }
