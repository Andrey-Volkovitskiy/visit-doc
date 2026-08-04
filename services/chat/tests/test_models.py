from chat.domain.models import FaqEntry


def test_faq_entry_table_name() -> None:
    assert FaqEntry.__tablename__ == "faq_entries"


def test_faq_entry_id_is_integer_primary_key() -> None:
    id_column = FaqEntry.__table__.c.id
    assert id_column.primary_key
    assert id_column.type.python_type is int


def test_faq_entry_has_no_title_column() -> None:
    assert "title" not in FaqEntry.__table__.c
