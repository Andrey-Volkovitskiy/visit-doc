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


def test_message_request_outcomes_is_nullable() -> None:
    assert Message.__table__.c.request_outcomes.nullable is True


def test_a_message_carries_no_turn_level_verdict_or_citation_list() -> None:
    # 011: both were message-level because the verdict was. A turn may now answer one
    # request and abstain on another, so neither has a turn-wide value left to hold.
    assert "faq_verdict" not in Message.__table__.c
    assert "citations" not in Message.__table__.c


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


def test_attention_mark_has_exactly_the_eight_kinds() -> None:
    """007's FR-027a four, plus 009's four causes - and nothing else."""
    assert {member.value for member in AttentionMark} == {
        "urgent_condition",
        "distress",
        "patient_asked_for_person",
        "booking_for_another_person",
        "not_authorized",
        "corpus_could_not_answer",
        "assistant_failed",
        "unanswered",
    }


def test_escalation_reason_has_exactly_the_seven_triggers() -> None:
    """FR-007a as spec 009 FR-020 restates it: the reasons are the triggers.

    Seven values for four situations - asked for a person, asked for something the
    assistant cannot provide, needs a person on safety or authority grounds, something
    failed - because the middle two are recorded by what would fix them.
    """
    assert {member.value for member in EscalationReason} == {
        "urgent_condition",
        "distress",
        "patient_asked_for_person",
        "booking_for_another_person",
        "not_authorized",
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


def test_clearable_marks_are_exactly_the_six_a_staff_message_clears() -> None:
    """FR-027c's lifetime column, as one constant, widened by spec 009 FR-048.

    This is the `IN` list of the clearing statement. A permanent mark appearing here
    would erase a diagnostic record; a clearable one missing would leave an answered
    request outstanding forever - and both are invisible in any test that only checks
    that *something* was cleared.
    """
    assert CLEARABLE_MARKS == frozenset(
        {
            AttentionMark.PATIENT_ASKED_FOR_PERSON,
            AttentionMark.UNANSWERED,
            AttentionMark.URGENT_CONDITION,
            AttentionMark.DISTRESS,
            AttentionMark.BOOKING_FOR_ANOTHER_PERSON,
            AttentionMark.NOT_AUTHORIZED,
        }
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


# --- Phase 1f: the escalation vocabulary -------------------------------------


def test_escalation_reason_carries_the_four_added_causes() -> None:
    assert {
        EscalationReason.URGENT_CONDITION,
        EscalationReason.DISTRESS,
        EscalationReason.BOOKING_FOR_ANOTHER_PERSON,
        EscalationReason.NOT_AUTHORIZED,
    } <= set(EscalationReason)


def test_attention_mark_carries_the_four_added_kinds() -> None:
    assert {
        AttentionMark.URGENT_CONDITION,
        AttentionMark.DISTRESS,
        AttentionMark.BOOKING_FOR_ANOTHER_PERSON,
        AttentionMark.NOT_AUTHORIZED,
    } <= set(AttentionMark)


def test_every_reason_has_a_mark_of_the_same_name() -> None:
    # The pair is what makes a call to staff and the mark on the message that caused it
    # impossible to disagree about why.
    assert {reason.value for reason in EscalationReason} <= {
        mark.value for mark in AttentionMark
    }


def test_every_cause_fits_the_columns_that_store_it() -> None:
    # This is why Phase 1f needs no migration: both columns are String(32) and
    # deliberately not database enums, so a new value is a code change alone.
    reason_length = Chat.__table__.c.escalation_reason.type.length
    mark_length = Message.__table__.c.attention_mark.type.length
    longest = max(len(value) for value in EscalationReason)
    assert longest <= reason_length
    assert max(len(mark.value) for mark in AttentionMark) <= mark_length


def test_clearable_marks_are_exactly_the_ones_a_staff_reply_answers() -> None:
    # A staff reply *is* the whole of what these six asked for. A corpus gap survives
    # its answer because the document is still missing; a failure because it happened.
    assert CLEARABLE_MARKS == frozenset(
        {
            AttentionMark.PATIENT_ASKED_FOR_PERSON,
            AttentionMark.UNANSWERED,
            AttentionMark.URGENT_CONDITION,
            AttentionMark.DISTRESS,
            AttentionMark.BOOKING_FOR_ANOTHER_PERSON,
            AttentionMark.NOT_AUTHORIZED,
        }
    )


def test_the_permanent_marks_stay_permanent() -> None:
    assert AttentionMark.CORPUS_COULD_NOT_ANSWER not in CLEARABLE_MARKS
    assert AttentionMark.ASSISTANT_FAILED not in CLEARABLE_MARKS
