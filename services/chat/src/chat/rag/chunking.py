"""Chunking: one chunk per heading section, fixed-size inside one or a headless entry.

An entry written with markdown headings is split at them, and every chunk carries the
path of headings it sits under, so a chunk names its document and section however far
into the document it was cut from. Before this, a 1,000-character window ran across
sections: a chunk could end on a bare heading, begin mid-sentence, and set one section's
sentence beside another's - which is how "do you take Delta Dental PPO?" was read as a
question about taking a medicine, the blood-thinner paragraph sharing its chunk. An
entry with no heading is chunked exactly as it always was.
"""

import re
from dataclasses import dataclass

from chat.domain.validation import is_meaningless

_CHUNK_SIZE = 1000
_CHUNK_OVERLAP = 150
_BOUNDARY_WINDOW = 200
# A markdown ATX heading: one to six `#`, then a space. "#1 priority" is not one.
_HEADING = re.compile(r"^(#{1,6}) \S")


@dataclass(frozen=True)
class ChunkedText:
    """Pre-embedding, pre-entry-association shape of a `FaqChunk`."""

    chunk_index: int
    chunk_text: str


def chunk_content(content: str) -> list[ChunkedText]:
    """Split `content` into chunks, dropping degenerate ones.

    An entry with headings gives one chunk per section that has text of its own, each
    prefixed with the section's heading path; a section longer than a chunk is split
    further, every piece carrying the same prefix. An entry with no heading is split
    into overlapping ~1,000-character windows that prefer paragraph and sentence
    boundaries. A chunk whose own text is meaningless is dropped - judged before its
    prefix is added, so a heading cannot make a divider worth retrieving.
    `chunk_index` is assigned after filtering, so it is contiguous over the surviving,
    retrievable chunks.
    """
    sections = _sections(content)
    if sections is None:
        texts = [
            text for text in _split(content, _CHUNK_SIZE) if not is_meaningless(text)
        ]
    else:
        texts = [
            f"{header}\n\n{piece}" if header else piece
            for header, body in sections
            for piece in _split(body, _CHUNK_SIZE - len(header) - 2)
            if not is_meaningless(piece)
        ]
    return [ChunkedText(chunk_index=i, chunk_text=text) for i, text in enumerate(texts)]


def _sections(content: str) -> list[tuple[str, str]] | None:
    """Return each heading section as its heading path and its own text.

    Returns: one (header, body) pair per section with text of its own, in document
        order - the header is the headings above and including the section's own, one
        per line, and is empty for text before the first heading - or None when the
        content has no heading at all.

    A deeper heading nests under the one before it; a heading at the same level or
    shallower closes every section at or below its level.
    """
    path: list[tuple[int, str]] = []
    sections: list[tuple[str, str]] = []
    body: list[str] = []
    seen_heading = False

    def flush() -> None:
        text = "\n".join(body).strip()
        if text:
            sections.append(("\n".join(line for _, line in path), text))
        body.clear()

    for line in content.splitlines():
        match = _HEADING.match(line)
        if match is None:
            body.append(line)
            continue
        seen_heading = True
        flush()
        level = len(match.group(1))
        while path and path[-1][0] >= level:
            path.pop()
        path.append((level, line.strip()))
    flush()
    return sections if seen_heading else None


def _split(content: str, size: int) -> list[str]:
    """Slice `content` into overlapping pieces of at most `size` characters."""
    length = len(content)
    if length <= size:
        return [content]

    pieces: list[str] = []
    start = 0
    while start < length:
        end = min(start + size, length)
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
