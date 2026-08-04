"""Fixed-size chunking with degenerate-chunk filtering (research.md #3, FR-017)."""

from dataclasses import dataclass

from chat.domain.validation import is_meaningless

_CHUNK_SIZE = 1000
_CHUNK_OVERLAP = 150
_BOUNDARY_WINDOW = 200


@dataclass(frozen=True)
class ChunkedText:
    """Pre-embedding, pre-entry-association shape of a `FaqChunk` (data-model.md)."""

    chunk_index: int
    chunk_text: str


def chunk_content(content: str) -> list[ChunkedText]:
    """Split `content` into overlapping ~1,000-char chunks, dropping degenerate ones.

    Chunks prefer paragraph/sentence boundaries over mid-word cuts (research.md #3).
    A chunk that is itself meaningless is dropped (FR-017) — `chunk_index` is assigned
    after filtering, so it's contiguous over the surviving, retrievable chunks.
    """
    texts = [text for text in _split(content) if not is_meaningless(text)]
    return [ChunkedText(chunk_index=i, chunk_text=text) for i, text in enumerate(texts)]


def _split(content: str) -> list[str]:
    """Slice `content` into overlapping fixed-size pieces."""
    length = len(content)
    if length <= _CHUNK_SIZE:
        return [content]

    pieces: list[str] = []
    start = 0
    while start < length:
        end = min(start + _CHUNK_SIZE, length)
        if end < length:
            end = _nearest_boundary(content, start, end)
        pieces.append(content[start:end])
        if end >= length:
            break
        start = max(end - _CHUNK_OVERLAP, start + 1)
    return pieces


def _nearest_boundary(content: str, start: int, target_end: int) -> int:
    """Nudge `target_end` back to a nearby paragraph/sentence boundary, if any."""
    window_start = max(start, target_end - _BOUNDARY_WINDOW)
    window = content[window_start:target_end]
    for boundary in ("\n\n", ". ", "\n"):
        idx = window.rfind(boundary)
        if idx != -1:
            return window_start + idx + len(boundary)
    return target_end
