"""The two retrieval gates and the verdict they produce.

Pure functions and frozen records - no client, no clock, no I/O. Everything this phase
decides about which chunks an answer may stand on is decided here, which is what lets
the whole decision table be tested without a network call.

The reranking call itself lives in `rag/reranking.py`; scoring and thresholding are
deliberately apart, so "no scores were obtained" and "scores were obtained and none
cleared the floor" can stay two different answers.
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from chat.domain.schemas import FaqVerdict


@dataclass(frozen=True)
class ScoredChunk:
    """A retrieved chunk carried through both gates, accumulating scores.

    `rerank_score` is None when no rerank score was obtained - the reranker did not
    run, or failed. It never means "scored zero": a chunk the cross-encoder scored at
    0.0 was judged irrelevant, and a chunk it never saw was not judged at all.
    """

    faq_entry_id: int
    chunk_index: int
    chunk_text: str
    similarity_score: float
    rerank_score: float | None = None

    def with_rerank_score(self, score: float) -> "ScoredChunk":
        """Return a copy carrying `score`, leaving this instance untouched."""
        return replace(self, rerank_score=score)


@dataclass(frozen=True)
class GateResult:
    """One gate's outcome: what it kept, and what it dropped and why.

    The two rejection lists are separate rather than one list with a reason field
    because a reader tuning a threshold only ever looks at one of them: dropped by the
    floor says the bar is too high, dropped by the cap says it is too low.
    """

    kept: list[ScoredChunk] = field(default_factory=list)
    dropped_by_floor: list[ScoredChunk] = field(default_factory=list)
    dropped_by_cap: list[ScoredChunk] = field(default_factory=list)


@dataclass(frozen=True)
class PipelineOutcome:
    """What the gates produced, and the only thing the FAQ node branches on.

    Invariant: `survivors` is non-empty if and only if `verdict.answered`. Nothing has
    to consult both fields to know whether the turn answered.
    """

    verdict: FaqVerdict
    survivors: list[ScoredChunk] = field(default_factory=list)
    considered: list[ScoredChunk] = field(default_factory=list)
    observed: list[ScoredChunk] = field(default_factory=list)


def _split(
    ordered: list[ScoredChunk], clears_floor: Callable[[ScoredChunk], bool], cap: int
) -> GateResult:
    """Split chunks already in the gate's own score order into the three lists.

    Args:
        ordered: the gate's candidates, best first by whichever score that gate reads.
        clears_floor: whether one chunk is at or above that gate's floor.

    Both gates share this so the floor-then-cap ordering, and which rejection list each
    loser lands in, are decided once rather than in two places free to drift.
    """
    above: list[ScoredChunk] = []
    below: list[ScoredChunk] = []
    for chunk in ordered:
        if clears_floor(chunk):
            above.append(chunk)
        else:
            below.append(chunk)
    return GateResult(
        kept=above[:cap], dropped_by_floor=below, dropped_by_cap=above[cap:]
    )


def apply_similarity_gate(
    pool: list[ScoredChunk], *, floor: float, cap: int
) -> GateResult:
    """Keep the `cap` highest-scoring chunks at or above `floor`.

    The floor is applied per chunk, not to the best chunk on behalf of the rest: a
    chunk below it is not admitted because a stronger one cleared it.

    Returns: the survivors in descending similarity order, plus the two rejection
        lists - those the floor dropped and those the cap dropped.
    """
    ordered = sorted(pool, key=lambda c: c.similarity_score, reverse=True)
    return _split(ordered, lambda c: c.similarity_score >= floor, cap)


def apply_rerank_gate(
    scored: list[ScoredChunk], *, floor: float, cap: int
) -> GateResult:
    """Keep the `cap` highest-reranked chunks at or above `floor`.

    Args:
        scored: chunks the reranker scored, each carrying a `rerank_score`. A chunk
            without one is treated as unscored and dropped by the floor, since a
            missing score is not a passing one.

    Returns: the survivors in descending *rerank* order - the reranked order wins,
        since re-ordering is the stage's whole purpose - plus the two rejection lists.
    """
    ordered = sorted(
        scored,
        key=lambda c: c.rerank_score if c.rerank_score is not None else -1.0,
        reverse=True,
    )
    return _split(
        ordered, lambda c: c.rerank_score is not None and c.rerank_score >= floor, cap
    )


def decide(
    observed: list[ScoredChunk],
    considered: list[ScoredChunk],
    reranked: list[ScoredChunk] | None,
    *,
    corpus_empty: bool,
) -> PipelineOutcome:
    """Assign the turn's verdict from what each stage produced.

    Args:
        reranked: the rerank gate's survivors, or None when no rerank score was
            obtained at all. The distinction is load-bearing: None means the reranker
            did not answer, so the turn falls back and answers unreranked; an empty
            list means it answered "none of these", so the turn abstains. A single
            empty list standing for both would make a fallback and an abstention
            indistinguishable, and the difference between them is whether the patient
            gets an answer.
        corpus_empty: True when the session publishes no live revisions, so no search
            was issued. Recorded separately from a search that returned nothing usable,
            because the two call for different fixes.
    """
    if corpus_empty:
        return PipelineOutcome(
            verdict=FaqVerdict.ABSTAINED_EMPTY_CORPUS, observed=observed
        )
    if not considered:
        return PipelineOutcome(
            verdict=FaqVerdict.ABSTAINED_SIMILARITY_FLOOR, observed=observed
        )
    if reranked is None:
        return PipelineOutcome(
            verdict=FaqVerdict.ANSWERED_UNRERANKED,
            survivors=considered,
            considered=considered,
            observed=observed,
        )
    if not reranked:
        return PipelineOutcome(
            verdict=FaqVerdict.ABSTAINED_RERANK_FLOOR,
            considered=considered,
            observed=observed,
        )
    return PipelineOutcome(
        verdict=FaqVerdict.ANSWERED,
        survivors=reranked,
        considered=considered,
        observed=observed,
    )
