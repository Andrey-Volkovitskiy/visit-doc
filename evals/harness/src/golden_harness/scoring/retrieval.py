"""Retrieval metrics: where each stage ranked the entries a request's label cites.

Two stages, scored separately and never pooled, because one number over "retrieval"
could not say which stage lost a labelled chunk. The similarity stage's ranked list is
`faq.retrieval_completed.candidates` in the order logged; the rerank stage's is
`faq.reranking_completed.scores` sorted by descending `rerank_score`. Both are read from
the turn's own log events, joined to a request by their `segment` field - never by the
order the lines appear in, since concurrent requests interleave.

Only aligned `faq_question` requests labelled answerable are scored: a gap cites
nothing, so there is no rank to look for. A labelled slug is compared through the run's
own slug-to-entry-id map, and a chunk belongs to a cited entry when its `entry_id` does.

A request is set aside from a stage, with one reason, in pipeline order:

- its turn was handed off (`handed_off_turn`), or a case-scoped reason excluded it -
  both stages;
- the classifier produced it under another intent, so it never reached the FAQ half
  (`not_routed_to_faq`) - both stages;
- its turn's corpus was empty (`no_search`) - both stages. The turn logs
  `turn.retrieval_skipped_empty_corpus`, and that is the only situation this names;
- none of its cited chunks was kept by the similarity gate (`not_reached_reranker`) -
  the rerank stage only, since the reranker is handed exactly what the gate kept;
- the reranker was unavailable (`reranker_unavailable`) - the rerank stage only.

A record the log contract cannot describe - a FAQ request with neither a retrieval event
nor a skip event, a segment carrying two of one event, a kept cited chunk with no rerank
event of either kind - is refused rather than scored as a miss.
"""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Final

from chat.domain.schemas import IntentLabel
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from golden_harness.cases import Case, LabelledRequest
from golden_harness.record import CaseRun, ExclusionReason
from golden_harness.scoring.alignment import AlignmentState, RunAlignment
from golden_harness.scoring.metric import Exclusions, Metric

SIMILARITY_HIT_AT_1 = "similarity_hit_at_1"
SIMILARITY_HIT_AT_3 = "similarity_hit_at_3"
SIMILARITY_HIT_AT_5 = "similarity_hit_at_5"
SIMILARITY_MRR = "similarity_mrr"
RERANK_HIT_AT_1 = "rerank_hit_at_1"
RERANK_HIT_AT_3 = "rerank_hit_at_3"
RERANK_HIT_AT_5 = "rerank_hit_at_5"
RERANK_MRR = "rerank_mrr"
SIMILARITY_GATE_SURVIVAL = "similarity_gate_survival"
RETRIEVAL_METRICS = (
    SIMILARITY_HIT_AT_1,
    SIMILARITY_HIT_AT_3,
    SIMILARITY_HIT_AT_5,
    SIMILARITY_MRR,
    RERANK_HIT_AT_1,
    RERANK_HIT_AT_3,
    RERANK_HIT_AT_5,
    RERANK_MRR,
    SIMILARITY_GATE_SURVIVAL,
)

_RETRIEVAL_COMPLETED: Final = "faq.retrieval_completed"
_SIMILARITY_GATE: Final = "faq.similarity_gate"
_RERANKING_COMPLETED: Final = "faq.reranking_completed"
_RERANKING_UNAVAILABLE: Final = "faq.reranking_unavailable"
_SKIPPED_EMPTY_CORPUS: Final = "turn.retrieval_skipped_empty_corpus"
_SEGMENT_EVENTS: Final = (
    _RETRIEVAL_COMPLETED,
    _SIMILARITY_GATE,
    _RERANKING_COMPLETED,
    _RERANKING_UNAVAILABLE,
    _SKIPPED_EMPTY_CORPUS,
)

# The similarity cap at or below which rerank-stage hit@5 cannot be anything but 1.
_HIT_AT_5_CAP: Final = 5


class LogContractError(ValueError):
    """A scored request's stored events are not what the log contract describes.

    Names the case and segment. Raised rather than scored as a miss: a record the
    contract cannot describe is a finding about the log or the contract.
    """


class Stage(StrEnum):
    """The two ranked lists a request is scored against."""

    SIMILARITY = "similarity"
    RERANK = "rerank"


class StageResult(BaseModel):
    """One request at one stage: set aside with a reason, or scored at a rank.

    When `excluded` is None the request is scored, and `rank` is the 1-based position of
    its first cited chunk, or None when no cited chunk was in that stage's list at all.
    When `excluded` is set, `rank` is None and means nothing further.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    excluded: ExclusionReason | None = None
    rank: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _an_excluded_request_has_no_rank(self) -> "StageResult":
        """Refuse a rank on a request set aside from the stage."""
        if self.excluded is not None and self.rank is not None:
            raise ValueError("a request excluded from a stage carries no rank")
        return self


class RetrievalRequest(BaseModel):
    """One labelled-answerable FAQ request, at both stages.

    `survived_similarity_gate` is None exactly when the similarity stage set the
    request aside, and otherwise says whether a cited chunk was among the gate's kept.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    position: int
    similarity: StageResult
    rerank: StageResult
    survived_similarity_gate: bool | None

    @model_validator(mode="after")
    def _survival_follows_the_similarity_stage(self) -> "RetrievalRequest":
        """Refuse a survival flag that disagrees with the similarity stage."""
        if (self.similarity.excluded is None) != (
            self.survived_similarity_gate is not None
        ):
            raise ValueError(
                "gate survival is set exactly when the similarity stage scored"
            )
        return self


class RetrievalScores(BaseModel):
    """The retrieval metrics over a run, and every request behind them.

    `rerank_hit_at_5_statement` is the text published beside rerank-stage hit@5 while
    the run's similarity cap makes that value 1 by construction, and None otherwise.
    `unaligned_requests` counts the labelled-answerable FAQ requests of unaligned cases,
    which no stage scores and no exclusion describes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    similarity_hit_at_1: Metric
    similarity_hit_at_3: Metric
    similarity_hit_at_5: Metric
    similarity_mrr: Metric
    rerank_hit_at_1: Metric
    rerank_hit_at_3: Metric
    rerank_hit_at_5: Metric
    rerank_mrr: Metric
    similarity_gate_survival: Metric
    rerank_hit_at_5_statement: str | None
    unaligned_requests: int
    requests: list[RetrievalRequest]

    def stage_metrics(self, stage: Stage) -> list[Metric]:
        """Return one stage's hit@1, hit@3, hit@5 and MRR, in that order."""
        if stage is Stage.SIMILARITY:
            return [
                self.similarity_hit_at_1,
                self.similarity_hit_at_3,
                self.similarity_hit_at_5,
                self.similarity_mrr,
            ]
        return [
            self.rerank_hit_at_1,
            self.rerank_hit_at_3,
            self.rerank_hit_at_5,
            self.rerank_mrr,
        ]

    def metrics(self) -> list[Metric]:
        """Return every retrieval metric in the order a report lists them."""
        return [
            *self.stage_metrics(Stage.SIMILARITY),
            *self.stage_metrics(Stage.RERANK),
            self.similarity_gate_survival,
        ]


class _SegmentEvents(BaseModel):
    """The retrieval events one request's segment raised, each at most once."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    retrieval: dict[str, JsonValue] | None = None
    gate: dict[str, JsonValue] | None = None
    reranked: dict[str, JsonValue] | None = None
    unavailable: dict[str, JsonValue] | None = None
    skipped: dict[str, JsonValue] | None = None


_FIELD_BY_EVENT: Final = {
    _RETRIEVAL_COMPLETED: "retrieval",
    _SIMILARITY_GATE: "gate",
    _RERANKING_COMPLETED: "reranked",
    _RERANKING_UNAVAILABLE: "unavailable",
    _SKIPPED_EMPTY_CORPUS: "skipped",
}


def rerank_hit_at_5_statement(similarity_cap: int) -> str | None:
    """Return what must be said beside rerank-stage hit@5 under `similarity_cap`.

    Returns: the statement while the cap is 5 or less, and None above it.
    """
    if similarity_cap > _HIT_AT_5_CAP:
        return None
    return (
        f"rerank_hit_at_5 is 1 by construction while similarity_cap "
        f"({similarity_cap}) is 5 or less: the reranker is handed at most that many "
        "chunks, and the rerank stage scores only requests with a cited chunk among "
        "them - so it is not a finding."
    )


def score_retrieval(
    cases: Sequence[Case],
    alignment: RunAlignment,
    case_runs: Sequence[CaseRun],
    *,
    entry_ids: Mapping[str, int],
    similarity_cap: int,
) -> RetrievalScores:
    """Score a run's retrieval, per stage, from its alignment and its log events.

    Args:
        cases: the labels `alignment` was taken against.
        case_runs: the recorded cases `alignment` was taken over.
        entry_ids: the run's map from a labelled slug to the session's entry id.
        similarity_cap: the run's similarity cap, as its conditions recorded it.

    Raises: ValueError when an alignment has no recorded case or no label, or when a
        cited slug is not in `entry_ids`; LogContractError when a scored request's
        events are not what the log contract says a turn raises. Both name the case.
    """
    labels = {case.id: case for case in cases}
    runs = {run.case_id: run for run in case_runs}
    requests: list[RetrievalRequest] = []
    unaligned = 0

    for case_alignment in alignment.alignments:
        case_id = case_alignment.case_id
        label, case_run = labels.get(case_id), runs.get(case_id)
        if label is None or case_run is None:
            raise ValueError(f"{case_id} is aligned but not recorded and labelled")
        answerable = [
            position
            for position, request in enumerate(label.requests)
            if _is_answerable_faq(request)
        ]
        if not answerable:
            continue

        # A case file carries only a turn-level reason, and every one of them - the
        # case-scoped ones and `handed_off_turn` alike - sets a request aside from both
        # stages.
        reason = case_alignment.turn_exclusion
        if reason is not None:
            requests.extend(
                _excluded_from_both(case_id, position, reason)
                for position in answerable
            )
            continue
        if case_alignment.state is AlignmentState.UNALIGNED:
            unaligned += len(answerable)
            continue

        for pair in case_alignment.pairs:
            if pair.position not in answerable:
                continue
            cited = _cited_entry_ids(case_id, pair.labelled, entry_ids)
            if pair.produced.intent is not IntentLabel.FAQ_QUESTION:
                requests.append(
                    _excluded_from_both(
                        case_id, pair.position, ExclusionReason.NOT_ROUTED_TO_FAQ
                    )
                )
                continue
            segment = _segment_events(case_run, pair.position)
            requests.append(_score_request(case_id, pair.position, cited, segment))

    return _scores(requests, unaligned, similarity_cap)


def _is_answerable_faq(request: LabelledRequest) -> bool:
    """Return True for a `faq_question` label the corpus answers."""
    return request.intent is IntentLabel.FAQ_QUESTION and request.answerable is True


def _excluded_from_both(
    case_id: str, position: int, reason: ExclusionReason
) -> RetrievalRequest:
    """Build a request set aside from both stages for `reason`."""
    return RetrievalRequest(
        case_id=case_id,
        position=position,
        similarity=StageResult(excluded=reason),
        rerank=StageResult(excluded=reason),
        survived_similarity_gate=None,
    )


def _cited_entry_ids(
    case_id: str, request: LabelledRequest, entry_ids: Mapping[str, int]
) -> frozenset[int]:
    """Resolve a label's cited slugs to the run session's entry ids.

    Raises: ValueError naming the case and the slug when the map has no such slug.
    """
    missing = sorted(slug for slug in request.cites or () if slug not in entry_ids)
    if missing:
        raise ValueError(
            f"{case_id} cites slugs the run's entry_ids does not map: {missing}"
        )
    return frozenset(entry_ids[slug] for slug in request.cites or ())


def _segment_events(case_run: CaseRun, position: int) -> _SegmentEvents:
    """Collect the retrieval events carrying `segment == position`.

    Raises: LogContractError naming the case when the case has no events, when a
        retrieval event carries no integer `segment`, or when the segment raised one
        event twice.
    """
    case_id = case_run.case_id
    if case_run.events is None:
        raise LogContractError(f"{case_id} is scored but has no log events")
    found: dict[str, dict[str, JsonValue]] = {}
    for event in case_run.events:
        name = event.get("event")
        if name not in _SEGMENT_EVENTS:
            continue
        segment = event.get("segment")
        if not isinstance(segment, int) or isinstance(segment, bool):
            raise LogContractError(f"{case_id}: {name} carries no integer segment")
        if segment != position:
            continue
        field = _FIELD_BY_EVENT[str(name)]
        if field in found:
            raise LogContractError(f"{case_id}: segment {position} raised {name} twice")
        found[field] = event
    return _SegmentEvents.model_validate(found)


def _score_request(
    case_id: str, position: int, cited: frozenset[int], segment: _SegmentEvents
) -> RetrievalRequest:
    """Score one request from its segment's events.

    Raises: LogContractError naming the case when the events are not a combination
        the log contract describes.
    """
    where = f"{case_id} segment {position}"
    if segment.skipped is not None:
        if any(
            event is not None
            for event in (
                segment.retrieval,
                segment.gate,
                segment.reranked,
                segment.unavailable,
            )
        ):
            raise LogContractError(f"{where}: skipped retrieval but logged a search")
        return _excluded_from_both(case_id, position, ExclusionReason.NO_SEARCH)
    if segment.retrieval is None or segment.gate is None:
        raise LogContractError(
            f"{where}: a FAQ request with no {_RETRIEVAL_COMPLETED} and "
            f"{_SIMILARITY_GATE}, and no {_SKIPPED_EMPTY_CORPUS}"
        )
    if segment.reranked is not None and segment.unavailable is not None:
        raise LogContractError(f"{where}: reranking both completed and was unavailable")

    candidates = _entries(where, segment.retrieval, "candidates")
    similarity = StageResult(rank=_rank([entry for entry, _ in candidates], cited))
    kept = {entry for entry, _ in _entries(where, segment.gate, "kept")}
    survived = bool(cited & kept)

    if not survived:
        rerank = StageResult(excluded=ExclusionReason.NOT_REACHED_RERANKER)
    elif segment.unavailable is not None:
        rerank = StageResult(excluded=ExclusionReason.RERANKER_UNAVAILABLE)
    elif segment.reranked is not None:
        scores = _entries(where, segment.reranked, "scores", score="rerank_score")
        ordered = sorted(scores, key=lambda scored: -scored[1])
        rerank = StageResult(rank=_rank([entry for entry, _ in ordered], cited))
    else:
        raise LogContractError(
            f"{where}: a cited chunk was kept but neither {_RERANKING_COMPLETED} "
            f"nor {_RERANKING_UNAVAILABLE} was logged"
        )
    return RetrievalRequest(
        case_id=case_id,
        position=position,
        similarity=similarity,
        rerank=rerank,
        survived_similarity_gate=survived,
    )


def _entries(
    where: str,
    event: dict[str, JsonValue],
    field: str,
    *,
    score: str | None = None,
) -> list[tuple[int, float]]:
    """Read `field` of `event` as a list of chunks: `(entry_id, score)` in logged order.

    Args:
        score: the numeric field to read beside `entry_id`; 0.0 is returned in its place
            when None, for a list whose order is all that is read.

    Raises: LogContractError naming `where` when the field is not a list of objects
        carrying an integer `entry_id` and, if asked, a numeric `score`.
    """
    value = event.get(field)
    if not isinstance(value, list):
        raise LogContractError(f"{where}: {event.get('event')}.{field} is not a list")
    entries: list[tuple[int, float]] = []
    for item in value:
        entry_id = item.get("entry_id") if isinstance(item, dict) else None
        if not isinstance(entry_id, int) or isinstance(entry_id, bool):
            raise LogContractError(
                f"{where}: {event.get('event')}.{field} has no entry_id"
            )
        if score is None:
            entries.append((entry_id, 0.0))
            continue
        assert isinstance(item, dict)
        value_of_score = item.get(score)
        if not isinstance(value_of_score, int | float) or isinstance(
            value_of_score, bool
        ):
            raise LogContractError(
                f"{where}: {event.get('event')}.{field} has no {score}"
            )
        entries.append((entry_id, float(value_of_score)))
    return entries


def _rank(entries: Sequence[int], cited: frozenset[int]) -> int | None:
    """Return the 1-based position of the first entry id in `cited`, or None."""
    for index, entry_id in enumerate(entries, start=1):
        if entry_id in cited:
            return index
    return None


def _scores(
    requests: list[RetrievalRequest], unaligned: int, similarity_cap: int
) -> RetrievalScores:
    """Compute the published metrics from every request's per-stage result."""
    similarity = [r.similarity for r in requests]
    rerank = [r.rerank for r in requests]
    similarity_excluded = Exclusions.tally(
        r.excluded for r in similarity if r.excluded is not None
    )
    surviving = sum(1 for r in requests if r.survived_similarity_gate is True)
    return RetrievalScores(
        similarity_hit_at_1=_hit_at(SIMILARITY_HIT_AT_1, similarity, 1),
        similarity_hit_at_3=_hit_at(SIMILARITY_HIT_AT_3, similarity, 3),
        similarity_hit_at_5=_hit_at(SIMILARITY_HIT_AT_5, similarity, 5),
        similarity_mrr=_mrr(SIMILARITY_MRR, similarity),
        rerank_hit_at_1=_hit_at(RERANK_HIT_AT_1, rerank, 1),
        rerank_hit_at_3=_hit_at(RERANK_HIT_AT_3, rerank, 3),
        rerank_hit_at_5=_hit_at(RERANK_HIT_AT_5, rerank, 5),
        rerank_mrr=_mrr(RERANK_MRR, rerank),
        similarity_gate_survival=Metric(
            name=SIMILARITY_GATE_SURVIVAL,
            numerator=surviving,
            denominator=sum(1 for r in similarity if r.excluded is None),
            excluded=similarity_excluded,
        ),
        rerank_hit_at_5_statement=rerank_hit_at_5_statement(similarity_cap),
        unaligned_requests=unaligned,
        requests=requests,
    )


def _hit_at(name: str, results: Sequence[StageResult], k: int) -> Metric:
    """Share of the scored results whose first cited chunk ranked within the top `k`."""
    scored = [r for r in results if r.excluded is None]
    return Metric(
        name=name,
        numerator=sum(1 for r in scored if r.rank is not None and r.rank <= k),
        denominator=len(scored),
        excluded=Exclusions.tally(
            r.excluded for r in results if r.excluded is not None
        ),
    )


def _mrr(name: str, results: Sequence[StageResult]) -> Metric:
    """Mean reciprocal rank over the scored results, 0 for a result with no rank."""
    scored = [r for r in results if r.excluded is None]
    return Metric(
        name=name,
        numerator=sum(1 / r.rank for r in scored if r.rank is not None),
        denominator=len(scored),
        excluded=Exclusions.tally(
            r.excluded for r in results if r.excluded is not None
        ),
    )
