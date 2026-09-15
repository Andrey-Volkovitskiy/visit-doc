"""The corpus pin: its digest, verifying a live corpus, mapping slugs to entry ids."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from chat.domain.schemas import FaqEntry
from chat.rag.default_corpus import DEFAULT_FAQ_ENTRIES
from golden_harness.corpus import (
    CorpusPin,
    UnmappedCorpusEntryError,
    corpus_digest,
    load_pin,
    map_entry_ids,
    verify,
)

_PIN = Path(__file__).resolve().parents[2] / "golden" / "corpus.json"
_CREATED = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _live(texts: list[str], first_id: int = 101) -> list[FaqEntry]:
    return [
        FaqEntry(
            id=first_id + i, content=text, created_at=_CREATED, updated_at=_CREATED
        )
        for i, text in enumerate(texts)
    ]


def _pin() -> CorpusPin:
    return load_pin(_PIN)


def test_the_digest_is_sha256_over_each_text_terminated_by_a_newline() -> None:
    texts = ["first entry", "second, with ünïcode"]

    expected = hashlib.sha256(
        "first entry\nsecond, with ünïcode\n".encode()
    ).hexdigest()
    assert corpus_digest(texts) == expected


def test_the_digest_depends_on_order() -> None:
    assert corpus_digest(["a", "b"]) != corpus_digest(["b", "a"])


def test_the_digest_keeps_entry_boundaries() -> None:
    # Terminating each text is what keeps "ab" + "c" apart from "a" + "bc".
    assert corpus_digest(["ab", "c"]) != corpus_digest(["a", "bc"])


def test_the_pinned_sha256_is_the_digest_of_the_service_starter_corpus() -> None:
    raw = json.loads(_PIN.read_text(encoding="utf-8"))

    assert raw["sha256"] == corpus_digest(list(DEFAULT_FAQ_ENTRIES))


def test_the_pin_file_names_its_construction() -> None:
    raw = json.loads(_PIN.read_text(encoding="utf-8"))

    algorithm = raw["algorithm"]
    assert "sha256" in algorithm
    assert "index order" in algorithm
    assert "UTF-8" in algorithm
    assert "\\n" in algorithm


def test_the_pinned_texts_are_the_service_starter_corpus() -> None:
    pin = _pin()

    assert [entry.text for entry in pin.entries] == list(DEFAULT_FAQ_ENTRIES)


def test_a_pin_whose_sha256_disagrees_with_its_own_texts_is_refused(
    tmp_path: Path,
) -> None:
    raw = json.loads(_PIN.read_text(encoding="utf-8"))
    raw["sha256"] = "0" * 64
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="sha256"):
        load_pin(path)


def test_the_live_starter_corpus_verifies() -> None:
    pin = _pin()

    check = verify(_live(list(DEFAULT_FAQ_ENTRIES)), pin)

    assert check.matched is True
    assert check.live_sha256 == check.pinned_sha256 == pin.sha256
    assert check.moved_entry_ids == []


def test_a_one_character_edit_names_exactly_that_entry() -> None:
    pin = _pin()
    texts = list(DEFAULT_FAQ_ENTRIES)
    texts[1] = texts[1].replace("photo ID", "photo IDs")

    check = verify(_live(texts), pin)

    assert check.matched is False
    assert check.live_sha256 != check.pinned_sha256
    assert check.moved_entry_ids == ["what-to-bring"]


def test_a_missing_entry_is_named_as_moved() -> None:
    pin = _pin()
    texts = [text for i, text in enumerate(DEFAULT_FAQ_ENTRIES) if i != 7]

    check = verify(_live(texts), pin)

    assert check.matched is False
    assert check.moved_entry_ids == ["hours-location"]


def test_every_pinned_slug_maps_to_its_live_entry_id_by_exact_text() -> None:
    pin = _pin()
    texts = list(DEFAULT_FAQ_ENTRIES)
    # Listed in another order, so the mapping cannot be positional.
    live = list(reversed(_live(texts)))

    mapping = map_entry_ids(live, pin)

    assert set(mapping) == {entry.id for entry in pin.entries}
    assert mapping["referral"] == 101
    assert mapping["what-to-bring"] == 102
    assert mapping["arrival-time"] == 109


def test_a_pinned_entry_with_no_live_counterpart_fails_the_mapping_by_name() -> None:
    pin = _pin()
    texts = list(DEFAULT_FAQ_ENTRIES)
    texts[2] = texts[2] + " Edited."

    with pytest.raises(UnmappedCorpusEntryError, match="telehealth"):
        map_entry_ids(_live(texts), pin)


def test_a_live_entry_the_pin_does_not_hold_is_named_by_its_live_id() -> None:
    pin = _pin()
    texts = [*DEFAULT_FAQ_ENTRIES, "Question: Is there Wi-Fi?\nAnswer: Yes."]

    check = verify(_live(texts), pin)

    assert check.matched is False
    assert check.moved_entry_ids == []
    assert check.unpinned_live_entry_ids == [110]


def test_a_pinned_text_carried_by_two_live_entries_fails_the_mapping_by_name() -> None:
    pin = _pin()
    texts = [*DEFAULT_FAQ_ENTRIES, DEFAULT_FAQ_ENTRIES[5]]

    with pytest.raises(UnmappedCorpusEntryError, match="out-of-pocket-rates"):
        map_entry_ids(_live(texts), pin)
