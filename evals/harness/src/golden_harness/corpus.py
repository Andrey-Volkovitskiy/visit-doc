"""The corpus pin: the digest `corpus.json` records, and checking a live corpus by it.

Every `cites` label names an entry of the pinned corpus by slug. A run is only a
measurement of those labels if the session it drives answers from exactly those texts,
so the live corpus is verified against the pin before any turn is taken, and each slug
is resolved to the session's own entry id by exact text.
"""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from chat.domain.schemas import FaqEntry
from pydantic import BaseModel, ConfigDict, model_validator


class UnmappedCorpusEntryError(ValueError):
    """Some pinned text has no single live entry carrying it; names those slugs."""


class PinnedEntry(BaseModel):
    """One entry of the pinned corpus: its slug, its position and its exact text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    index: int
    question: str
    text: str


class CorpusPin(BaseModel):
    """The contents of `corpus.json`, with its entries in index order.

    A pin whose `sha256` is not the digest of its own texts is refused on load: a check
    whose expected value cannot be recomputed is not a check.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    entry_count: int
    algorithm: str
    sha256: str
    note: str
    entries: list[PinnedEntry]

    @model_validator(mode="after")
    def _entries_are_what_the_pin_says(self) -> "CorpusPin":
        """Refuse a pin whose entry list disagrees with its count, order or digest."""
        if [entry.index for entry in self.entries] != list(range(len(self.entries))):
            raise ValueError("pinned entries must be listed in index order from 0")
        if len(self.entries) != self.entry_count:
            raise ValueError("entry_count does not match the pinned entries")
        if corpus_digest([entry.text for entry in self.entries]) != self.sha256:
            raise ValueError("sha256 is not the digest of the pinned entries' texts")
        return self


class CorpusCheck(BaseModel):
    """The outcome of verifying a live corpus against the pin.

    `moved_entry_ids` names the pinned slugs no live entry carries the exact text of;
    `unpinned_live_entry_ids` names the live entries whose text no pinned entry has.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    matched: bool
    live_sha256: str
    pinned_sha256: str
    moved_entry_ids: list[str]
    unpinned_live_entry_ids: list[int]


def corpus_digest(texts: Sequence[str]) -> str:
    """Return the corpus digest: hex sha256 over each text, UTF-8, terminated by "\\n".

    The texts are digested in the order given, and nothing but them enters the digest.
    """
    digest = hashlib.sha256()
    for text in texts:
        digest.update(text.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def load_pin(path: Path) -> CorpusPin:
    """Load and validate `corpus.json`.

    Raises: pydantic.ValidationError (a ValueError) when the file's shape is wrong or
        its `sha256` is not the digest of its own entries.
    """
    return CorpusPin.model_validate(json.loads(path.read_text(encoding="utf-8")))


def verify(live_entries: Sequence[FaqEntry], pin: CorpusPin) -> CorpusCheck:
    """Check a session's live corpus, in the order the service lists it, against a pin.

    The digest comparison decides `matched`; the two name lists say which texts moved.
    """
    live_sha256 = corpus_digest([entry.content for entry in live_entries])
    live_texts = {entry.content for entry in live_entries}
    pinned_texts = {entry.text for entry in pin.entries}
    return CorpusCheck(
        matched=live_sha256 == pin.sha256,
        live_sha256=live_sha256,
        pinned_sha256=pin.sha256,
        moved_entry_ids=[
            entry.id for entry in pin.entries if entry.text not in live_texts
        ],
        unpinned_live_entry_ids=[
            entry.id for entry in live_entries if entry.content not in pinned_texts
        ],
    )


def map_entry_ids(live_entries: Sequence[FaqEntry], pin: CorpusPin) -> dict[str, int]:
    """Resolve every pinned slug to the live entry carrying its exact text.

    Returns: each pinned slug mapped to the session's own FAQ entry id.

    Raises: UnmappedCorpusEntryError when a pinned entry has no live counterpart, or
        several - a partial or ambiguous mapping is never returned.
    """
    ids_by_text: dict[str, list[int]] = {}
    for entry in live_entries:
        ids_by_text.setdefault(entry.content, []).append(entry.id)

    missing = [e.id for e in pin.entries if len(ids_by_text.get(e.text, [])) == 0]
    if missing:
        raise UnmappedCorpusEntryError(
            f"pinned entries with no live counterpart: {', '.join(missing)}"
        )
    repeated = [e.id for e in pin.entries if len(ids_by_text[e.text]) > 1]
    if repeated:
        raise UnmappedCorpusEntryError(
            f"pinned entries with several live counterparts: {', '.join(repeated)}"
        )
    return {entry.id: ids_by_text[entry.text][0] for entry in pin.entries}
