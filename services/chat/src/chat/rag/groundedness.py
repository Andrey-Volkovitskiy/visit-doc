"""Similarity-threshold groundedness gate (research.md #4)."""

from chat.repositories.qdrant_repository import RetrievedChunk

# Tunable heuristic cutoff, not a spec-level requirement (spec.md Clarifications:
# "sufficiently relevant" is deliberately left unquantified at that level).
GROUNDEDNESS_THRESHOLD = 0.5


def is_grounded(chunks: list[RetrievedChunk]) -> bool:
    """Return True if the best-matching chunk clears the groundedness threshold.

    Below threshold (or no chunks at all), the agent MUST abstain without calling Claude
    (FR-005, Constitution Principle V). `chunks` is assumed sorted best-match first, as
    `qdrant_repository.search` returns it.
    """
    if not chunks:
        return False
    return chunks[0].score >= GROUNDEDNESS_THRESHOLD
