"""Fusing the two retrieval branches, the two gates, and the verdict they produce.

Pure functions and frozen records - no client, no clock, no I/O. Everything retrieval
decides about which chunks an answer may stand on is decided here, which is what lets
the whole decision table be tested without a network call.

The reranking call itself lives in `rag/reranking.py`; scoring and thresholding are
deliberately apart, so "no scores were obtained" and "scores were obtained and none
cleared the floor" can stay two different answers.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from chat.domain.schemas import FaqVerdict

# Reciprocal Rank Fusion's constant: a chunk at rank r in a branch contributes
# 1 / (RRF_K + r). 60 is the value the method was published with; a larger k flattens
# the difference between ranks, a smaller one lets one branch's top hit dominate.
RRF_K = 60


@dataclass(frozen=True)
class ScoredChunk:
    """A retrieved chunk carried through fusion and both gates, accumulating scores.

    Every optional score is None when the stage that gives it did not score this
    chunk, and never means "scored zero":

    - `similarity_score`, the cosine, and `dense_rank` are None when the dense branch
      did not return the chunk; `lexical_score`, its BM25 score, and `lexical_rank`
      when the lexical one did not. The two scores are on different scales and are
      never compared with each other.
    - `fused_score` is None before fusion.
    - `rerank_score` is None when no rerank score was obtained - the reranker did not
      run, or failed. A chunk the cross-encoder scored at 0.0 was judged irrelevant,
      and a chunk it never saw was not judged at all.
    """

    faq_entry_id: int
    chunk_index: int
    chunk_text: str
    similarity_score: float | None
    rerank_score: float | None = None
    lexical_score: float | None = None
    dense_rank: int | None = None
    lexical_rank: int | None = None
    fused_score: float | None = None

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


def fuse_rrf(
    dense: list[ScoredChunk], lexical: list[ScoredChunk], *, k: int
) -> list[ScoredChunk]:
    """Merge the two branches' hits into one list by Reciprocal Rank Fusion.

    Args:
        dense: the dense branch's hits, best first, each carrying its cosine.
        lexical: the lexical branch's hits, best first, each carrying its BM25 score.

    Returns: every chunk either branch returned, once each, best fused score first,
        carrying its rank and score in each branch that returned it and its fused
        score - the sum of `1 / (k + rank)` over those branches.

    Only ranks are fused, never scores: a cosine and a BM25 score are on different
    scales, and RRF exists so they never have to be put on one. A chunk is identified
    by its entry and chunk index, which the search's live-revision filter makes unique.
    A tie goes to the better dense rank, then the better lexical rank, then the entry
    and chunk index, so the order never depends on how a dict happened to iterate.
    """
    merged: dict[tuple[int, int], ScoredChunk] = {}
    for rank, chunk in enumerate(dense, start=1):
        key = (chunk.faq_entry_id, chunk.chunk_index)
        if key not in merged:
            merged[key] = replace(chunk, dense_rank=rank, lexical_score=None)
    for rank, chunk in enumerate(lexical, start=1):
        key = (chunk.faq_entry_id, chunk.chunk_index)
        found = merged.get(key)
        if found is None:
            merged[key] = replace(
                chunk, similarity_score=None, dense_rank=None, lexical_rank=rank
            )
        elif found.lexical_rank is None:
            merged[key] = replace(
                found, lexical_rank=rank, lexical_score=chunk.lexical_score
            )

    def fused(chunk: ScoredChunk) -> float:
        return sum(
            1 / (k + rank)
            for rank in (chunk.dense_rank, chunk.lexical_rank)
            if rank is not None
        )

    scored = [replace(c, fused_score=fused(c)) for c in merged.values()]
    return sorted(
        scored,
        key=lambda c: (
            -(c.fused_score or 0.0),
            c.dense_rank if c.dense_rank is not None else math.inf,
            c.lexical_rank if c.lexical_rank is not None else math.inf,
            c.faq_entry_id,
            c.chunk_index,
        ),
    )


def apply_shortlist_gate(pool: list[ScoredChunk], *, cap: int) -> GateResult:
    """Keep the first `cap` chunks of `pool`, in the order the pool arrived in.

    Args:
        pool: the fused observation pool, best first.

    Returns: the survivors in pool order, plus those the cap dropped. Nothing is
        dropped by a floor: the pool's order is a fused rank, which no cosine bar can
        be read against, and the rerank floor is what decides an abstention.
    """
    return _split(pool, lambda _chunk: True, cap)


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
    unreranked_floor: float,
) -> PipelineOutcome:
    """Assign the turn's verdict from what each stage produced.

    Args:
        observed: every candidate the search returned, before any gate. Empty while
            the corpus is not empty means the search matched no chunk of the live
            revisions at all - the index is behind the rows - which is a different
            situation, and a different fix, from a pool the floor rejected.
        reranked: the rerank gate's survivors, or None when no rerank score was
            obtained at all. The distinction is load-bearing: None means the reranker
            did not answer, so the turn falls back and answers unreranked; an empty
            list means it answered "none of these", so the turn abstains. A single
            empty list standing for both would make a fallback and an abstention
            indistinguishable, and the difference between them is whether the patient
            gets an answer.
        unreranked_floor: the cosine similarity a considered chunk must reach for
            the fallback to answer from it. With no rerank score nothing has judged the
            shortlist, so a fallback answers only from the chunks at or above this, and
            abstains at the similarity floor when none is. A chunk with no cosine -
            one only the lexical branch found - never reaches it.
        corpus_empty: True when the session publishes no live revisions, so no search
            was issued. Recorded separately from a search that returned nothing usable,
            because the two call for different fixes.
    """
    if corpus_empty:
        return PipelineOutcome(
            verdict=FaqVerdict.ABSTAINED_EMPTY_CORPUS, observed=observed
        )
    if not observed:
        return PipelineOutcome(
            verdict=FaqVerdict.ABSTAINED_EMPTY_POOL, observed=observed
        )
    if not considered:
        return PipelineOutcome(
            verdict=FaqVerdict.ABSTAINED_SIMILARITY_FLOOR, observed=observed
        )
    if reranked is None:
        floor_checked = [
            c
            for c in considered
            if c.similarity_score is not None and c.similarity_score >= unreranked_floor
        ]
        if not floor_checked:
            return PipelineOutcome(
                verdict=FaqVerdict.ABSTAINED_SIMILARITY_FLOOR,
                considered=considered,
                observed=observed,
            )
        return PipelineOutcome(
            verdict=FaqVerdict.ANSWERED_UNRERANKED,
            survivors=floor_checked,
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
