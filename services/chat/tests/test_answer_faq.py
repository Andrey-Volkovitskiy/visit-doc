"""What the FAQ node stands its answer on, and when it refuses to answer at all.

Retrieval and reranking are patched at this node's own seams, so every case here is
about the node's decisions - what reaches the prompt, what becomes a citation, which
verdict is recorded, and which calls are never made - rather than about a provider.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Self
from unittest.mock import AsyncMock, patch

import pytest
import structlog
from chat.agent.answer_faq import answer_faq
from chat.agent.compose_answer import (
    FaqResult,
    FaqSegmentAnswer,
    deduplicate_chunks,
)
from chat.agent.escalation import EscalationRequests
from chat.core.config import get_settings
from chat.core.errors import TurnPipelineError
from chat.domain.models import EscalationReason, Message, MessageSender
from chat.domain.schemas import (
    ChatDoneEvent,
    ChatTokenEvent,
    Citation,
    FaqVerdict,
    IntentLabel,
    RequestSegment,
)
from chat.rag.pipeline import ScoredChunk
from chat.rag.reranking import rerank_chunks as real_rerank_chunks
from structlog.testing import capture_logs

from .conftest import FakeFinalMessage

_SESSION = "01JQ0000000000000000000000"
_REVISIONS = ["01JQ1111111111111111111111"]


def _only(result: FaqResult) -> FaqSegmentAnswer:
    """Return the one request a single-request turn answered.

    There is no turn-level verdict and no turn-level citation list to read: a turn may
    answer one request and abstain on another, so both live on the request. Unpacking
    is the assertion that this turn carried exactly one.
    """
    (answer,) = result.segment_answers
    return answer


def _verdict(result: FaqResult) -> FaqVerdict:
    """Return the verdict of a single-request turn's only request."""
    return _only(result).verdict


def _citations(result: FaqResult) -> list[Citation]:
    """Return the citations of a single-request turn's only request."""
    return _only(result).citations


def _bursts(question: str = "what should I bring?") -> list[list[Message]]:
    return [[Message(sender=MessageSender.PATIENT, content=question, id="p1")]]


def _chunk(
    index: int, similarity: float = 0.9, rerank: float | None = None
) -> ScoredChunk:
    return ScoredChunk(
        faq_entry_id=index + 1,
        chunk_index=index,
        chunk_text=f"chunk text {index}",
        similarity_score=similarity,
        rerank_score=rerank,
    )


class _Stream:
    """Minimal stand-in for `anthropic.messages.stream(...)`."""

    def __init__(
        self, text: str, recorder: dict[str, object], stop_reason: str = "end_turn"
    ) -> None:
        self._text = text
        self._recorder = recorder
        self._stop_reason = stop_reason

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def __aiter__(self) -> AsyncIterator[object]:
        from types import SimpleNamespace

        yield SimpleNamespace(type="text", text=self._text)

    async def get_final_message(self) -> FakeFinalMessage:
        return FakeFinalMessage(self._stop_reason)


def _anthropic(recorder: dict[str, object], stop_reason: str = "end_turn") -> AsyncMock:
    client = AsyncMock()

    def stream(**kwargs: object) -> _Stream:
        recorder["calls"] = int(recorder.get("calls", 0)) + 1
        recorder["messages"] = kwargs.get("messages")
        recorder.setdefault("prompts", []).append(str(kwargs.get("messages")))
        recorder["max_tokens"] = kwargs.get("max_tokens")
        return _Stream("an answer", recorder, stop_reason=stop_reason)

    client.messages.stream = stream
    return client


async def _run(
    *,
    pool: list[ScoredChunk],
    reranked: list[ScoredChunk] | None,
    live_revisions: list[str] | None = None,
    question: str = "what should I bring?",
    stop_reason: str = "end_turn",
) -> tuple[FaqResult, dict[str, object], AsyncMock, EscalationRequests]:
    """Drive one FAQ turn. Returns its result, a call recorder, the rerank mock, and
    the turn's escalation collector."""
    recorder: dict[str, object] = {}
    rerank = AsyncMock(return_value=reranked)
    search = AsyncMock(return_value=pool)
    escalation = EscalationRequests()
    result: FaqResult | None = None
    with (
        patch("chat.agent.answer_faq.search_faq", search),
        patch("chat.agent.answer_faq.rerank_chunks", rerank),
    ):
        async for event in answer_faq(
            AsyncMock(),
            AsyncMock(),
            AsyncMock(),
            _anthropic(recorder, stop_reason=stop_reason),
            _bursts(question),
            ["p1"],
            _SESSION,
            _REVISIONS if live_revisions is None else live_revisions,
            segments=[RequestSegment(intent=IntentLabel.FAQ_QUESTION, text=question)],
            escalation=escalation,
            stream=False,
        ):
            if isinstance(event, FaqResult):
                result = event
    assert result is not None
    recorder["search"] = search
    return result, recorder, rerank, escalation


# --- US1: an answer stands only on the reranked survivors ---------------------------


async def test_only_reranked_survivors_reach_the_context_and_the_citations() -> None:
    pool = [_chunk(i) for i in range(5)]
    survivors = [_chunk(0, rerank=0.9), _chunk(3, rerank=0.7)]

    result, recorder, _, _ = await _run(pool=pool, reranked=survivors)

    assert _verdict(result) is FaqVerdict.ANSWERED
    assert [c.chunk_index for c in _citations(result)] == [0, 3]
    prompt = str(recorder["messages"])
    assert "chunk text 0" in prompt
    assert "chunk text 3" in prompt


async def test_a_chunk_the_reranker_rejected_is_in_neither_context_nor_citations() -> (
    None
):
    pool = [_chunk(0), _chunk(1)]
    survivors = [_chunk(0, rerank=0.9)]

    result, recorder, _, _ = await _run(pool=pool, reranked=survivors)

    assert [c.chunk_index for c in _citations(result)] == [0]
    assert "chunk text 1" not in str(recorder["messages"])


async def test_the_cited_set_and_the_context_set_are_identical() -> None:
    survivors = [_chunk(0, rerank=0.9), _chunk(1, rerank=0.8)]

    result, recorder, _, _ = await _run(pool=[_chunk(0), _chunk(1)], reranked=survivors)

    prompt = str(recorder["messages"])
    cited = {c.chunk_text for c in _citations(result)}
    assert cited == {"chunk text 0", "chunk text 1"}
    assert all(text in prompt for text in cited)


async def test_citations_carry_both_scores_for_the_turn_record() -> None:
    survivors = [_chunk(0, similarity=0.61, rerank=0.93)]

    result, _, _, _ = await _run(pool=[_chunk(0)], reranked=survivors)

    assert result.scored_chunks[0].similarity_score == 0.61
    assert result.scored_chunks[0].rerank_score == 0.93


# --- FR-002b: the pool costs no extra model work -----------------------------------


async def test_the_reranker_sees_only_the_similarity_survivors() -> None:
    # A 25-candidate pool, a cap of 5: the reranker must never be handed the tail. The
    # pool exists to be recorded, not to be scored.
    pool = [_chunk(i, similarity=0.9 - i / 100) for i in range(25)]
    survivors = [_chunk(0, rerank=0.9)]

    _, _, rerank, _ = await _run(pool=pool, reranked=survivors)

    sent = rerank.await_args.args[2]
    assert len(sent) == 5
    assert [c.chunk_index for c in sent] == [0, 1, 2, 3, 4]


async def test_sub_floor_candidates_never_reach_the_reranker() -> None:
    pool = [_chunk(0, similarity=0.9), _chunk(1, similarity=0.05)]
    survivors = [_chunk(0, rerank=0.9)]

    _, _, rerank, _ = await _run(pool=pool, reranked=survivors)

    sent = rerank.await_args.args[2]
    assert [c.chunk_index for c in sent] == [0]


# --- FR-035: the retrieval query is still the trailing patient message --------------


async def test_retrieval_is_given_the_trailing_patient_message_unchanged() -> None:
    # Sub-query extraction is Phase 1f. Until then the FAQ half retrieves against the
    # message as sent, and this test is what stops that drifting by accident.
    question = "what should I bring to a first cardiology visit?"

    _, recorder, _, _ = await _run(
        pool=[_chunk(0)], reranked=[_chunk(0, rerank=0.9)], question=question
    )

    search: AsyncMock = recorder["search"]  # type: ignore[assignment]
    assert search.await_args.args[2] == question


# --- US2: the four abstentions -----------------------------------------------------


async def test_an_empty_corpus_abstains_without_embedding_or_searching() -> None:
    result, recorder, rerank, escalation = await _run(
        pool=[], reranked=None, live_revisions=[]
    )

    assert _verdict(result) is FaqVerdict.ABSTAINED_EMPTY_CORPUS
    assert _citations(result) == []
    assert recorder.get("calls") is None
    rerank.assert_not_awaited()
    assert EscalationReason.CORPUS_COULD_NOT_ANSWER in escalation.recorded


async def test_a_search_matching_nothing_abstains_at_the_pool_not_the_floor() -> None:
    # Live revisions the search returned no chunk of: the index is behind the rows, so
    # no floor rejected anything and lowering one would not help.
    result, recorder, rerank, escalation = await _run(pool=[], reranked=None)

    assert _verdict(result) is FaqVerdict.ABSTAINED_EMPTY_POOL
    assert recorder.get("calls") is None
    rerank.assert_not_awaited()
    assert EscalationReason.CORPUS_COULD_NOT_ANSWER in escalation.recorded


async def test_a_below_floor_pool_abstains_with_no_rerank_and_no_generation() -> None:
    result, recorder, rerank, _ = await _run(
        pool=[_chunk(0, similarity=0.05)], reranked=None
    )

    assert _verdict(result) is FaqVerdict.ABSTAINED_SIMILARITY_FLOOR
    assert recorder.get("calls") is None
    rerank.assert_not_awaited()


async def test_an_all_rejected_rerank_abstains_with_no_generation() -> None:
    result, recorder, _, _ = await _run(pool=[_chunk(0)], reranked=[])

    assert _verdict(result) is FaqVerdict.ABSTAINED_RERANK_FLOOR
    assert _citations(result) == []
    assert recorder.get("calls") is None


@pytest.mark.parametrize(
    ("pool", "reranked", "revisions"),
    [
        ([], None, []),
        ([], None, None),
        ([_chunk(0, similarity=0.05)], None, None),
        ([_chunk(0)], [], None),
    ],
)
async def test_all_four_abstentions_are_identical_to_the_patient(
    pool: list[ScoredChunk],
    reranked: list[ScoredChunk] | None,
    revisions: list[str] | None,
) -> None:
    result, _, _, escalation = await _run(
        pool=pool, reranked=reranked, live_revisions=revisions
    )

    assert not _verdict(result).answered
    assert _citations(result) == []
    assert "knowledge base" in result.answer_text
    assert EscalationReason.CORPUS_COULD_NOT_ANSWER in escalation.recorded


# --- US3: the reranker is unavailable ----------------------------------------------


async def test_a_reranker_failure_answers_from_the_similarity_survivors() -> None:
    pool = [_chunk(i) for i in range(8)]

    result, recorder, _, _ = await _run(pool=pool, reranked=None)

    assert _verdict(result) is FaqVerdict.ANSWERED_UNRERANKED
    assert len(_citations(result)) == 5
    assert recorder.get("calls") == 1


async def test_a_reranker_failure_calls_no_staff() -> None:
    # A dependency outage is not a corpus gap, and the patient received an answer.
    _, _, _, escalation = await _run(pool=[_chunk(0)], reranked=None)

    assert escalation.recorded == ()


async def test_a_reranker_failure_does_not_rescue_a_similarity_abstention() -> None:
    result, recorder, _, _ = await _run(
        pool=[_chunk(0, similarity=0.01)], reranked=None
    )

    assert _verdict(result) is FaqVerdict.ABSTAINED_SIMILARITY_FLOOR
    assert recorder.get("calls") is None


async def test_an_unreranked_answer_carries_no_rerank_scores() -> None:
    result, _, _, _ = await _run(pool=[_chunk(0)], reranked=None)

    assert all(c.rerank_score is None for c in result.scored_chunks)


# --- streaming mode ----------------------------------------------------------------


async def test_streaming_mode_emits_the_verdict_on_its_done_event() -> None:
    recorder: dict[str, object] = {}
    events: list[object] = []
    with (
        patch("chat.agent.answer_faq.search_faq", AsyncMock(return_value=[_chunk(0)])),
        patch(
            "chat.agent.answer_faq.rerank_chunks",
            AsyncMock(return_value=[_chunk(0, rerank=0.9)]),
        ),
    ):
        async for event in answer_faq(
            AsyncMock(),
            AsyncMock(),
            AsyncMock(),
            _anthropic(recorder),
            _bursts(),
            ["p1"],
            _SESSION,
            _REVISIONS,
            segments=_segments("what should I bring?"),
            escalation=EscalationRequests(),
            stream=True,
        ):
            events.append(event)

    done = [e for e in events if isinstance(e, ChatDoneEvent)]
    assert len(done) == 1
    assert done[0].request_outcomes is not None
    assert [o.verdict for o in done[0].request_outcomes] == [FaqVerdict.ANSWERED]
    assert any(isinstance(e, ChatTokenEvent) for e in events)


# --- US4: every stage's decision is reconstructable from the logs -------------------
#
# The events are emitted from this node's `_run_pipeline`, not from `rag/pipeline.py`:
# the gates are pure functions and stay that way, so the stage that *knows* what a gate
# decided is the one that called it. `contracts/log-events.md` specifies the fields,
# which is what matters; where they are raised is this module's business.


def _events(logs: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(entry["event"]): entry for entry in logs}


async def _logged(
    *,
    pool: list[ScoredChunk],
    reranked: list[ScoredChunk] | None,
    live_revisions: list[str] | None = None,
) -> dict[str, dict[str, object]]:
    with capture_logs() as logs:
        await _run(pool=pool, reranked=reranked, live_revisions=live_revisions)
    return _events(logs)


async def test_the_retrieval_event_carries_the_whole_pool_in_score_order() -> None:
    pool = [_chunk(i, similarity=0.9 - i / 100) for i in range(12)]

    events = await _logged(pool=pool, reranked=[_chunk(0, rerank=0.9)])

    retrieval = events["faq.retrieval_completed"]
    assert retrieval["pool_returned"] == 12
    assert retrieval["pool_size"] == get_settings().RETRIEVAL_POOL_SIZE
    candidates = retrieval["candidates"]
    assert [c["chunk_index"] for c in candidates] == list(range(12))
    assert all("similarity_score" in c for c in candidates)


async def test_considered_text_is_full_and_excluded_text_is_a_preview() -> None:
    long_text = "x" * 400
    pool = [
        ScoredChunk(
            faq_entry_id=1,
            chunk_index=i,
            chunk_text=long_text,
            similarity_score=0.9 - i / 100,
        )
        for i in range(8)
    ]

    events = await _logged(pool=pool, reranked=[pool[0].with_rerank_score(0.9)])

    candidates = events["faq.retrieval_completed"]["candidates"]
    considered = [c for c in candidates if c["considered"]]
    excluded = [c for c in candidates if not c["considered"]]
    assert len(considered) == 5
    assert all(len(c["chunk_text"]) == 400 for c in considered)
    assert all(len(c["chunk_text"]) == 200 for c in excluded)
    assert all(c["text_truncated"] for c in excluded)


async def test_truncation_is_only_marked_when_the_text_was_actually_cut() -> None:
    # A chunk shorter than the preview length is logged whole. Marking it truncated
    # would have a reader looking for text that was never withheld.
    pool = [_chunk(i, similarity=0.9 - i / 100) for i in range(8)]

    events = await _logged(pool=pool, reranked=[_chunk(0, rerank=0.9)])

    excluded = [
        c
        for c in events["faq.retrieval_completed"]["candidates"]
        if not c["considered"]
    ]
    assert excluded
    assert not any(c["text_truncated"] for c in excluded)


async def test_the_similarity_gate_separates_floor_drops_from_cap_drops() -> None:
    # One says the bar is too high, the other says it is too low. A log that conflates
    # them cannot tune either.
    pool = [_chunk(i, similarity=0.9 - i / 100) for i in range(8)] + [
        _chunk(99, similarity=0.01)
    ]

    events = await _logged(pool=pool, reranked=[_chunk(0, rerank=0.9)])

    gate = events["faq.similarity_gate"]
    assert gate["floor"] == get_settings().SIMILARITY_FLOOR
    assert gate["cap"] == get_settings().SIMILARITY_CAP
    assert gate["pool_returned"] == 9
    assert len(gate["kept"]) == 5
    assert [c["chunk_index"] for c in gate["dropped_by_floor"]] == [99]
    assert [c["chunk_index"] for c in gate["dropped_by_cap"]] == [5, 6, 7]


async def test_the_rerank_gate_reports_its_own_floor_cap_and_drops() -> None:
    pool = [_chunk(i) for i in range(3)]
    scored = [
        _chunk(0, rerank=0.95),
        _chunk(1, rerank=0.90),
        _chunk(2, rerank=0.85),
    ]

    with (
        capture_logs() as logs,
        patch("chat.agent.answer_faq.search_faq", AsyncMock(return_value=pool)),
        patch("chat.agent.answer_faq.rerank_chunks", AsyncMock(return_value=scored)),
    ):
        if True:
            async for _ in answer_faq(
                AsyncMock(),
                AsyncMock(),
                AsyncMock(),
                _anthropic({}),
                _bursts(),
                ["p1"],
                _SESSION,
                _REVISIONS,
                segments=_segments("what should I bring?"),
                escalation=EscalationRequests(),
                stream=False,
            ):
                pass

    gate = _events(logs)["faq.rerank_gate"]
    assert gate["floor"] == get_settings().RERANK_FLOOR
    assert gate["cap"] == get_settings().RERANK_CAP
    assert len(gate["kept"]) == get_settings().RERANK_CAP
    assert all("rerank_score" in c for c in gate["kept"])


async def test_the_verdict_event_names_the_gate_for_each_abstention() -> None:
    empty = await _logged(pool=[], reranked=None, live_revisions=[])
    unmatched = await _logged(pool=[], reranked=None)
    similarity = await _logged(pool=[_chunk(0, similarity=0.01)], reranked=None)
    rerank = await _logged(pool=[_chunk(0)], reranked=[])

    assert empty["faq.verdict"]["blocked_gate"] == "empty_corpus"
    assert unmatched["faq.verdict"]["blocked_gate"] == "empty_pool"
    assert similarity["faq.verdict"]["blocked_gate"] == "similarity_floor"
    assert rerank["faq.verdict"]["blocked_gate"] == "rerank_floor"


async def test_the_verdict_event_reports_the_best_similarity_score_seen() -> None:
    # "Nothing was close" and "something was close and the floor was too high" are
    # different problems with different fixes.
    events = await _logged(pool=[_chunk(0, similarity=0.29)], reranked=None)

    verdict = events["faq.verdict"]
    assert verdict["verdict"] == FaqVerdict.ABSTAINED_SIMILARITY_FLOOR.value
    assert verdict["best_similarity_score"] == 0.29
    assert verdict["survivor_count"] == 0


async def test_the_verdict_event_reports_the_best_rerank_score_seen() -> None:
    # The best of everything the reranker scored, not of what its floor kept: an
    # abstention at the rerank floor keeps nothing, and this is the number that says
    # how far under the floor the best candidate landed.
    events = await _logged(
        pool=[_chunk(0), _chunk(1)],
        reranked=[_chunk(0, rerank=0.02), _chunk(1, rerank=0.11)],
    )

    verdict = events["faq.verdict"]
    assert verdict["verdict"] == FaqVerdict.ABSTAINED_RERANK_FLOOR.value
    assert verdict["best_rerank_score"] == 0.11
    assert verdict["survivor_count"] == 0


async def test_an_unreranked_turn_reports_no_best_rerank_score() -> None:
    # The reranker failed, so no chunk was judged. Reporting 0.0 here would read as a
    # cross-encoder that scored everything irrelevant.
    events = await _logged(pool=[_chunk(0)], reranked=None)

    verdict = events["faq.verdict"]
    assert verdict["verdict"] == FaqVerdict.ANSWERED_UNRERANKED.value
    assert verdict["best_rerank_score"] is None
    assert verdict["best_similarity_score"] == 0.9


async def test_an_empty_corpus_reports_no_best_scores_rather_than_zero() -> None:
    events = await _logged(pool=[], reranked=None, live_revisions=[])

    assert events["faq.verdict"]["best_similarity_score"] is None
    assert events["faq.verdict"]["best_rerank_score"] is None


# --- SC-006a / SC-004a: the pool changes the record, never the answer ---------------


@pytest.mark.parametrize("pool_size", [10, 25, 60])
async def test_the_observation_pool_size_changes_no_answer(pool_size: int) -> None:
    """Widening the pool must move only what the turn logged.

    Specified as a by-hand check (SC-006a), pinned here instead: the shipped corpus is
    nine chunks, so a manual run cannot tell a pool of 25 from one of 10 - both return
    everything. Synthesising a pool larger than any real corpus is the only way to
    exercise the property at all, and a test keeps it exercised.
    """
    pool = [_chunk(i, similarity=0.9 - i / 200) for i in range(pool_size)]
    survivors = [_chunk(0, rerank=0.9)]

    result, _, rerank, _ = await _run(pool=pool, reranked=survivors)

    assert _verdict(result) is FaqVerdict.ANSWERED
    assert [c.chunk_index for c in _citations(result)] == [0]
    # The reranker's input is the cap's output, never the pool's size.
    assert len(rerank.await_args.args[2]) == get_settings().SIMILARITY_CAP


async def test_the_reranker_is_asked_to_score_every_chunk_on_the_shortlist() -> None:
    # `top_k` below the shortlist's length leaves a survivor unscored, and the port
    # refuses a partial response - so the turn would answer unreranked on every FAQ
    # question, silently, for as long as the two numbers disagreed.
    pool = [_chunk(i, similarity=0.9 - i / 100) for i in range(8)]

    _, _, rerank, _ = await _run(pool=pool, reranked=[_chunk(0, rerank=0.9)])

    shortlist = rerank.await_args.args[2]
    assert rerank.await_args.kwargs["top_k"] == len(shortlist)


async def test_a_wider_pool_costs_log_lines_not_log_volume() -> None:
    """The 200-char preview is what keeps a 5x wider pool off the log's size (SC-004a).

    Without it, a 25-candidate pool of full chunks is roughly five times the per-turn
    retrieval log this phase inherited.
    """
    long_text = "x" * 1000
    pool = [
        ScoredChunk(
            faq_entry_id=1,
            chunk_index=i,
            chunk_text=long_text,
            similarity_score=0.9 - i / 200,
        )
        for i in range(25)
    ]

    events = await _logged(pool=pool, reranked=[pool[0].with_rerank_score(0.9)])

    candidates = events["faq.retrieval_completed"]["candidates"]
    logged_chars = sum(len(c["chunk_text"]) for c in candidates)
    # 5 considered at full length + 20 previews, against 25 at full length.
    assert logged_chars == 5 * 1000 + 20 * 200
    assert logged_chars < 25 * 1000 / 2


async def test_considered_reflects_the_gate_not_the_candidate_position() -> None:
    """`considered` must mean "the similarity gate kept it", not "it was in the top 5".

    The two coincide only while every candidate in the cap clears the floor. When fewer
    than the cap do, a positional flag reports chunks as considered that the floor threw
    out - and reports their full text as though it had reached the prompt. A reader
    tuning the floor from this log would be reading a fiction about what the floor did.
    """
    pool = [
        _chunk(0, similarity=0.9),
        _chunk(1, similarity=0.8),
        _chunk(2, similarity=0.10),
        _chunk(3, similarity=0.05),
        _chunk(4, similarity=0.04),
    ]

    events = await _logged(pool=pool, reranked=[_chunk(0, rerank=0.9)])

    candidates = events["faq.retrieval_completed"]["candidates"]
    considered = [c["chunk_index"] for c in candidates if c["considered"]]
    assert considered == [0, 1]


async def test_a_below_floor_candidate_inside_the_cap_is_logged_as_a_preview() -> None:
    long_text = "x" * 400
    pool = [
        ScoredChunk(
            faq_entry_id=1, chunk_index=0, chunk_text=long_text, similarity_score=0.9
        ),
        ScoredChunk(
            faq_entry_id=1, chunk_index=1, chunk_text=long_text, similarity_score=0.02
        ),
    ]

    events = await _logged(pool=pool, reranked=[pool[0].with_rerank_score(0.9)])

    by_index = {
        c["chunk_index"]: c for c in events["faq.retrieval_completed"]["candidates"]
    }
    assert len(by_index[0]["chunk_text"]) == 400
    assert len(by_index[1]["chunk_text"]) == 200
    assert by_index[1]["text_truncated"] is True


async def test_an_empty_corpus_raises_no_retrieval_or_gate_event() -> None:
    """No search was issued and no gate decided anything, so neither may claim it did.

    A reader counting `faq.similarity_gate` events with no survivors has to be able to
    tell "the floor rejected everything" from "there was nothing to search" - which is
    the same distinction the empty-corpus verdict exists to keep. Emitting both events
    with empty payloads would put the two back together in the log after the verdict
    had separated them.
    """
    events = await _logged(pool=[], reranked=None, live_revisions=[])

    assert "faq.retrieval_completed" not in events
    assert "faq.similarity_gate" not in events
    # The verdict still records the turn, and names the gate it stopped at.
    assert events["faq.verdict"]["blocked_gate"] == "empty_corpus"


async def test_a_pool_rejected_by_the_floor_still_raises_both_events() -> None:
    # The other side of the same rule: a search that ran and found nothing usable is a
    # decision the gate made, and it has to be visible as one.
    events = await _logged(pool=[_chunk(0, similarity=0.01)], reranked=None)

    assert events["faq.retrieval_completed"]["pool_returned"] == 1
    assert events["faq.similarity_gate"]["kept"] == []
    assert events["faq.verdict"]["blocked_gate"] == "similarity_floor"


# --- Phase 1g: one run per request ---------------------------------------------------


def _segments(*questions: str) -> list[RequestSegment]:
    return [RequestSegment(intent=IntentLabel.FAQ_QUESTION, text=q) for q in questions]


async def _run_many(
    *,
    pools: dict[str, list[ScoredChunk]],
    reranked: dict[str, list[ScoredChunk] | None],
    gate: asyncio.Event | None = None,
    expected_arrivals: int = 0,
) -> tuple[FaqResult, dict[str, object]]:
    """Drive one FAQ turn over several segments, each with its own pool and shortlist.

    `pools`/`reranked` are keyed by the segment's own text, so a run that retrieved for
    the wrong question gets the wrong chunks rather than quietly passing. `gate` (with
    `expected_arrivals`) holds every search until they have all started, which only
    completes if the runs really do overlap.
    """
    recorder: dict[str, object] = {}
    arrivals: list[str] = []

    async def _search(
        _qdrant: object,
        _voyage: object,
        query: str,
        _session: str,
        _revisions: list[str],
    ) -> list[ScoredChunk]:
        arrivals.append(query)
        if gate is not None:
            if len(arrivals) >= expected_arrivals:
                gate.set()
            await gate.wait()
        return pools[query]

    async def _rerank(
        _client: object, query: str, _chunks: list[ScoredChunk], **_kw: object
    ) -> list[ScoredChunk] | None:
        return reranked[query]

    result: FaqResult | None = None
    with (
        patch("chat.agent.answer_faq.search_faq", _search),
        patch("chat.agent.answer_faq.rerank_chunks", _rerank),
    ):
        async for event in answer_faq(
            AsyncMock(),
            AsyncMock(),
            AsyncMock(),
            _anthropic(recorder),
            _bursts(" ".join(pools)),
            ["p1"],
            _SESSION,
            _REVISIONS,
            segments=_segments(*pools),
            escalation=EscalationRequests(),
            stream=False,
        ):
            if isinstance(event, FaqResult):
                result = event
    assert result is not None
    recorder["arrivals"] = arrivals
    return result, recorder


async def test_each_request_retrieves_for_its_own_text() -> None:
    # The whole of the fix: a question is searched for on its own, not as part of a
    # sentence that also carried something else.
    pools = {"where are you?": [_chunk(0)], "what should I bring?": [_chunk(1)]}
    reranked = {
        "where are you?": [_chunk(0, rerank=0.9)],
        "what should I bring?": [_chunk(1, rerank=0.9)],
    }

    _, recorder = await _run_many(pools=pools, reranked=reranked)

    assert sorted(recorder["arrivals"]) == ["what should I bring?", "where are you?"]


async def test_a_request_answers_only_from_its_own_chunks() -> None:
    pools = {"where are you?": [_chunk(0)], "what should I bring?": [_chunk(1)]}
    reranked = {
        "where are you?": [_chunk(0, rerank=0.9)],
        "what should I bring?": [_chunk(1, rerank=0.9)],
    }

    result, _ = await _run_many(pools=pools, reranked=reranked)

    by_question = {a.question: a for a in result.segment_answers}
    assert [c.chunk_index for c in by_question["where are you?"].citations] == [0]
    assert [c.chunk_index for c in by_question["what should I bring?"].citations] == [1]


async def test_no_request_sees_another_requests_chunks_in_its_prompt() -> None:
    # Provenance is structural: another question's chunks are not in the prompt to be
    # cross-wired, because each generation call is given one shortlist.
    pools = {"where are you?": [_chunk(0)], "what should I bring?": [_chunk(1)]}
    reranked = {
        "where are you?": [_chunk(0, rerank=0.9)],
        "what should I bring?": [_chunk(1, rerank=0.9)],
    }

    _, recorder = await _run_many(pools=pools, reranked=reranked)

    for prompt in recorder["prompts"]:
        assert not ("chunk text 0" in prompt and "chunk text 1" in prompt)


async def test_the_runs_overlap_rather_than_queueing() -> None:
    # A sequential loop never reaches the second search, so the gate never opens and
    # this times out - which is the assertion. No wall-clock threshold is involved.
    pools = {"where are you?": [_chunk(0)], "what should I bring?": [_chunk(1)]}
    reranked = {
        "where are you?": [_chunk(0, rerank=0.9)],
        "what should I bring?": [_chunk(1, rerank=0.9)],
    }

    result, _ = await asyncio.wait_for(
        _run_many(
            pools=pools,
            reranked=reranked,
            gate=asyncio.Event(),
            expected_arrivals=2,
        ),
        timeout=5,
    )

    assert [a.verdict for a in result.segment_answers] == [
        FaqVerdict.ANSWERED,
        FaqVerdict.ANSWERED,
    ]


async def test_a_turn_issues_one_search_and_one_generation_per_request() -> None:
    pools = {f"q{i}?": [_chunk(i)] for i in range(3)}
    reranked = {f"q{i}?": [_chunk(i, rerank=0.9)] for i in range(3)}

    _, recorder = await _run_many(pools=pools, reranked=reranked)

    assert len(recorder["arrivals"]) == 3
    assert recorder["calls"] == 3


async def test_one_requests_reranking_outage_degrades_only_that_request() -> None:
    # A dependency outage is not a corpus gap: the turn still answers, and says the
    # evidence was weaker than a reranked turn's.
    pools = {"a?": [_chunk(0)], "b?": [_chunk(1)]}
    reranked: dict[str, list[ScoredChunk] | None] = {
        "a?": [_chunk(0, rerank=0.9)],
        "b?": None,
    }

    result, _ = await _run_many(pools=pools, reranked=reranked)

    # Only that request's: a dependency outage on one question says nothing about the
    # evidence the other one's answer rests on.
    assert {a.question: a.verdict for a in result.segment_answers} == {
        "a?": FaqVerdict.ANSWERED,
        "b?": FaqVerdict.ANSWERED_UNRERANKED,
    }


async def test_one_requests_search_failure_fails_the_whole_turn() -> None:
    # A failure is not an abstention, and the half that worked is not delivered on its
    # own - that is partial serving, and it is not this phase's.
    async def _search(
        _q: object, _v: object, query: str, _s: str, _r: list[str]
    ) -> list[ScoredChunk]:
        if query == "b?":
            raise TurnPipelineError("retrieval", RuntimeError("qdrant is down"))
        return [_chunk(0)]

    with (
        patch("chat.agent.answer_faq.search_faq", _search),
        patch("chat.agent.answer_faq.rerank_chunks", AsyncMock(return_value=None)),
        pytest.raises(TurnPipelineError),
    ):
        async for _ in answer_faq(
            AsyncMock(),
            AsyncMock(),
            AsyncMock(),
            _anthropic({}),
            _bursts("a? b?"),
            ["p1"],
            _SESSION,
            _REVISIONS,
            segments=_segments("a?", "b?"),
            escalation=EscalationRequests(),
            stream=False,
        ):
            pass


async def test_a_failing_request_stops_the_ones_running_beside_it() -> None:
    # `asyncio.gather` alone leaves the siblings running: the turn is already over, and
    # they would keep spending generation calls on an answer nobody reads, then raise
    # into nothing. Both assertions below are needed - `retrieved` says the siblings
    # were stopped where they stood, `recorder` says neither went on to generate, which
    # is the call that costs.
    retrieved: list[str] = []
    recorder: dict[str, object] = {}

    async def _search(
        _q: object, _v: object, query: str, _s: str, _r: list[str]
    ) -> list[ScoredChunk]:
        if query == "fails?":
            await asyncio.sleep(0)
            raise TurnPipelineError("retrieval", RuntimeError("qdrant is down"))
        await asyncio.sleep(0.05)
        retrieved.append(query)
        return [_chunk(0)]

    with (
        patch("chat.agent.answer_faq.search_faq", _search),
        patch("chat.agent.answer_faq.rerank_chunks", AsyncMock(return_value=None)),
        pytest.raises(TurnPipelineError),
    ):
        async for _ in answer_faq(
            AsyncMock(),
            AsyncMock(),
            AsyncMock(),
            _anthropic(recorder),
            _bursts("fails? slow? also slow?"),
            ["p1"],
            _SESSION,
            _REVISIONS,
            segments=_segments("fails?", "slow?", "also slow?"),
            escalation=EscalationRequests(),
            stream=False,
        ):
            pass

    await asyncio.sleep(0.1)
    assert retrieved == []
    assert recorder.get("calls", 0) == 0


async def _drive(
    segments: tuple[str, ...],
    search: object,
) -> None:
    """Run one FAQ turn over `segments` to exhaustion, discarding its events."""
    with (
        patch("chat.agent.answer_faq.search_faq", search),
        patch("chat.agent.answer_faq.rerank_chunks", AsyncMock(return_value=None)),
    ):
        async for _ in answer_faq(
            AsyncMock(),
            AsyncMock(),
            AsyncMock(),
            _anthropic({}),
            _bursts(" ".join(segments)),
            ["p1"],
            _SESSION,
            _REVISIONS,
            segments=_segments(*segments),
            escalation=EscalationRequests(),
            stream=False,
        ):
            pass


async def test_a_cancellation_mid_drain_does_not_replace_the_failure() -> None:
    # The turn already has an account of itself - which question failed, and why. A
    # cancellation landing while the siblings are being drained must not overwrite it
    # with a bare `CancelledError`, which names nothing and reads as a supersede.
    unwinding = asyncio.Event()

    async def _search(
        _q: object, _v: object, query: str, _s: str, _r: list[str]
    ) -> list[ScoredChunk]:
        if query == "fails?":
            await asyncio.sleep(0)
            raise TurnPipelineError("retrieval", RuntimeError("qdrant is down"))
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            # Slow to unwind, so the drain is still waiting when the cancel lands.
            unwinding.set()
            await asyncio.sleep(0.05)
            raise
        return [_chunk(0)]

    turn = asyncio.create_task(_drive(("fails?", "slow?"), _search))
    await asyncio.wait_for(unwinding.wait(), timeout=5)
    turn.cancel()
    await asyncio.wait([turn])

    assert not turn.cancelled()
    assert isinstance(turn.exception(), TurnPipelineError)


async def test_a_turn_cancelled_with_nothing_else_to_report_ends_cancelled() -> None:
    # Nothing failed, so the cancellation is the whole account of the turn: it leaves
    # as itself, and a superseded turn keeps reading as cancelled rather than broken.
    started = asyncio.Event()

    async def _search(
        _q: object, _v: object, _query: str, _s: str, _r: list[str]
    ) -> list[ScoredChunk]:
        started.set()
        await asyncio.sleep(10)
        return [_chunk(0)]

    turn = asyncio.create_task(_drive(("a?", "b?"), _search))
    await asyncio.wait_for(started.wait(), timeout=5)
    turn.cancel()
    await asyncio.wait([turn])

    assert turn.cancelled()


async def test_a_sibling_that_refuses_to_unwind_cannot_hold_the_turn_open() -> None:
    # The drain is not shielded, so it cannot outlive a cancellation: this sibling
    # never finishes unwinding, and the turn still leaves at once, carrying its
    # failure. A shielded drain would wait here instead, which is the worse defect.
    stuck = asyncio.Event()
    released = asyncio.Event()

    async def _search(
        _q: object, _v: object, query: str, _s: str, _r: list[str]
    ) -> list[ScoredChunk]:
        if query == "fails?":
            await asyncio.sleep(0)
            raise TurnPipelineError("retrieval", RuntimeError("qdrant is down"))
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            stuck.set()
            await released.wait()
            raise
        return [_chunk(0)]

    turn = asyncio.create_task(_drive(("fails?", "stuck?"), _search))
    try:
        await asyncio.wait_for(stuck.wait(), timeout=5)
        turn.cancel()
        await asyncio.wait_for(asyncio.wait([turn]), timeout=1)

        assert not turn.cancelled()
        assert isinstance(turn.exception(), TurnPipelineError)
    finally:
        released.set()
        await asyncio.sleep(0)


async def test_an_answer_cut_off_at_the_cap_is_still_delivered() -> None:
    # It rests on evidence that cleared both gates, so abstaining over it would be a
    # lie about the corpus. The turn answers, and the cut is recorded instead.
    result, _, _, escalation = await _run(
        pool=[_chunk(0)],
        reranked=[_chunk(0, rerank=0.9)],
        stop_reason="max_tokens",
    )

    assert _verdict(result) is FaqVerdict.ANSWERED
    assert result.answer_text == "an answer"
    # Nobody is called: the answer was grounded, it just stopped early.
    assert escalation.recorded == ()


async def test_an_answer_cut_off_at_the_cap_says_so_in_the_log() -> None:
    # A clipped answer otherwise looks exactly like a short complete one.
    with capture_logs() as logs:
        await _run(
            pool=[_chunk(0)],
            reranked=[_chunk(0, rerank=0.9)],
            stop_reason="max_tokens",
        )

    truncated = _events(logs)["faq.truncated"]
    assert truncated["answer_chars"] == len("an answer")
    assert truncated["max_tokens"] > 0


async def test_a_generation_that_returned_no_text_fails_the_request() -> None:
    # A request's outcome pairs its answer with its verdict, so "answered, with
    # nothing" is the one pair it may not hold. That outcome is built after the half has
    # already streamed or been merged, so an empty generation discovered there would
    # reach the patient behind their own reply - it is caught where the text was not
    # produced instead, as the generation failure it is.
    recorder: dict[str, object] = {}
    client = AsyncMock()
    client.messages.stream = lambda **_kwargs: _Stream("", recorder)

    with (
        patch("chat.agent.answer_faq.search_faq", AsyncMock(return_value=[_chunk(0)])),
        patch(
            "chat.agent.answer_faq.rerank_chunks",
            AsyncMock(return_value=[_chunk(0, rerank=0.9)]),
        ),
        pytest.raises(TurnPipelineError) as raised,
    ):
        async for _ in answer_faq(
            AsyncMock(),
            AsyncMock(),
            AsyncMock(),
            client,
            _bursts("what should I bring?"),
            ["p1"],
            _SESSION,
            _REVISIONS,
            segments=[
                RequestSegment(
                    intent=IntentLabel.FAQ_QUESTION, text="what should I bring?"
                )
            ],
            escalation=EscalationRequests(),
            stream=False,
        ):
            pass

    assert raised.value.pipeline_step == "generation"


async def test_an_answer_that_finished_on_its_own_records_no_truncation() -> None:
    with capture_logs() as logs:
        await _run(pool=[_chunk(0)], reranked=[_chunk(0, rerank=0.9)])

    assert "faq.truncated" not in _events(logs)


async def test_an_unanswerable_request_leaves_its_siblings_answer_standing() -> None:
    # Phase 1h: what one request's gap costs is that request, not the turn.
    pools = {"a?": [_chunk(0)], "b?": [_chunk(1)]}
    reranked: dict[str, list[ScoredChunk] | None] = {
        "a?": [_chunk(0, rerank=0.9)],
        "b?": [],
    }

    result, _ = await _run_many(pools=pools, reranked=reranked)

    assert {a.question: a.verdict for a in result.segment_answers} == {
        "a?": FaqVerdict.ANSWERED,
        "b?": FaqVerdict.ABSTAINED_RERANK_FLOOR,
    }
    # The half no longer abstains as a whole: the answered request keeps its answer and
    # its citations, and the gap is one further part for the composer to name.
    assert result.answer_text is None
    assert result.part_count == 2
    assert result.request_outcomes[0].answer == "an answer"
    assert result.request_outcomes[1].answer is None


async def test_one_call_to_staff_however_many_requests_abstained() -> None:
    escalation = EscalationRequests()
    with (
        patch("chat.agent.answer_faq.search_faq", AsyncMock(return_value=[_chunk(0)])),
        patch("chat.agent.answer_faq.rerank_chunks", AsyncMock(return_value=[])),
    ):
        async for _ in answer_faq(
            AsyncMock(),
            AsyncMock(),
            AsyncMock(),
            _anthropic({}),
            _bursts("a? b?"),
            ["p1"],
            _SESSION,
            _REVISIONS,
            segments=_segments("a?", "b?"),
            escalation=escalation,
            stream=False,
        ):
            pass

    assert escalation.recorded == (EscalationReason.CORPUS_COULD_NOT_ANSWER,)


async def test_one_chunk_answering_two_requests_is_cited_under_each() -> None:
    # Two provenances, not a duplicate: it supported two answers, and a reader auditing
    # either one has to see what that one stood on. Deduplication is within a request.
    shared = _chunk(0, rerank=0.9)
    pools = {"a?": [_chunk(0)], "b?": [_chunk(0)]}
    reranked = {"a?": [shared], "b?": [shared]}

    result, _ = await _run_many(pools=pools, reranked=reranked)

    assert [
        [(c.entry_id, c.chunk_index) for c in answer.citations]
        for answer in result.segment_answers
    ] == [[(1, 0)], [(1, 0)]]


# --- Phase 1g: the collapse rule that survives, as a pure function --------------------
#
# `summarize_verdict` is gone with the value it produced: a turn has no single verdict
# to reduce to (FR-002), and keeping the reduction "for the log" would put the value
# with two meanings back one layer down.


def test_deduplication_keeps_the_first_appearance_and_its_order() -> None:
    first, second = _chunk(0), _chunk(1)

    assert deduplicate_chunks([first, second, _chunk(0)]) == [first, second]


def test_deduplication_tells_chunks_of_one_entry_apart() -> None:
    # Identity is the pair, not the entry: two chunks of one entry are two chunks.
    chunks = [_chunk(0), _chunk(1)]

    assert deduplicate_chunks(chunks) == chunks


# --- Phase 1g: one request pays what it always paid ----------------------------------


async def test_one_request_issues_one_search_one_rerank_and_one_generation() -> None:
    result, recorder, rerank, _ = await _run(
        pool=[_chunk(0)], reranked=[_chunk(0, rerank=0.9)]
    )

    assert recorder["search"].await_count == 1
    assert rerank.await_count == 1
    assert recorder["calls"] == 1
    assert _verdict(result) is FaqVerdict.ANSWERED


async def test_one_requests_prompt_keeps_the_shape_it_has_always_had() -> None:
    _, recorder, _, _ = await _run(
        pool=[_chunk(0)],
        reranked=[_chunk(0, rerank=0.9)],
        question="what should I bring?",
    )

    assert recorder["messages"] == [
        {
            "role": "user",
            "content": "Context:\nchunk text 0\n\nQuestion: what should I bring?",
        }
    ]


# --- Phase 1g: which request an event belongs to -------------------------------------


async def test_every_retrieval_event_names_the_request_it_belongs_to() -> None:
    # Two pipelines interleave in one turn's log; the ordering of lines carries no
    # meaning, so the field is what makes it readable.
    pools = {"a?": [_chunk(0)], "b?": [_chunk(1)]}
    reranked = {"a?": [_chunk(0, rerank=0.9)], "b?": [_chunk(1, rerank=0.9)]}

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        await _run_many(pools=pools, reranked=reranked)

    named = {
        "faq.retrieval_completed",
        "faq.similarity_gate",
        "faq.reranking_completed",
        "faq.rerank_gate",
        "faq.verdict",
    }
    events = [entry for entry in logs if entry["event"] in named]
    assert events
    assert all(entry["segment"] in (0, 1) for entry in events)
    # Each request's own verdict, under its own position.
    verdicts = {
        entry["segment"]: entry["verdict"]
        for entry in logs
        if entry["event"] == "faq.verdict"
    }
    assert verdicts == {0: "answered", 1: "answered"}


async def test_a_degraded_request_is_named_by_position_not_by_the_turn() -> None:
    # Driven through the *real* reranking module: the binding has to reach the events
    # a request logs below this node, not only the ones the node logs itself.
    failing_client = AsyncMock()
    failing_client.rerank = AsyncMock(side_effect=RuntimeError("voyage is down"))
    recorder: dict[str, object] = {}

    async def _search(
        _q: object, _v: object, query: str, _s: str, _r: list[str]
    ) -> list[ScoredChunk]:
        return [_chunk(0 if query == "a?" else 1)]

    with (
        patch("chat.agent.answer_faq.search_faq", _search),
        # The autouse fixture fakes this boundary for every test; here the real one is
        # what is under test, since the event it logs is the thing being attributed.
        patch("chat.agent.answer_faq.rerank_chunks", real_rerank_chunks),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        async for _ in answer_faq(
            AsyncMock(),
            AsyncMock(),
            failing_client,
            _anthropic(recorder),
            _bursts("a? b?"),
            ["p1"],
            _SESSION,
            _REVISIONS,
            segments=_segments("a?", "b?"),
            escalation=EscalationRequests(),
            stream=False,
        ):
            pass

    unavailable = [e for e in logs if e["event"] == "faq.reranking_unavailable"]
    assert sorted(entry["segment"] for entry in unavailable) == [0, 1]


async def test_an_empty_corpus_is_recorded_per_request() -> None:
    # Driven through the real retriever, which returns on the empty revision list
    # before it spends a dependency - so this reaches the event without a corpus.
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        async for _ in answer_faq(
            AsyncMock(),
            AsyncMock(),
            AsyncMock(),
            _anthropic({}),
            _bursts("a? b?"),
            ["p1"],
            _SESSION,
            [],
            segments=_segments("a?", "b?"),
            escalation=EscalationRequests(),
            stream=False,
        ):
            pass

    skipped = [e for e in logs if e["event"] == "turn.retrieval_skipped_empty_corpus"]
    assert sorted(entry["segment"] for entry in skipped) == [0, 1]


async def test_one_requests_binding_does_not_leak_into_another() -> None:
    # `structlog.contextvars` is task-scoped, which is what makes a per-request binding
    # safe to set inside a fan-out at all.
    pools = {f"q{i}?": [_chunk(i)] for i in range(3)}
    reranked = {f"q{i}?": [_chunk(i, rerank=0.9)] for i in range(3)}

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        await _run_many(pools=pools, reranked=reranked)

    by_position: dict[int, set[int]] = {}
    for entry in logs:
        if entry["event"] != "faq.retrieval_completed":
            continue
        by_position.setdefault(entry["segment"], set()).update(
            candidate["chunk_index"] for candidate in entry["candidates"]
        )
    assert by_position == {0: {0}, 1: {1}, 2: {2}}


# --- Phase 1h: a request that abstains costs no generation call ----------------------


@pytest.mark.parametrize("answered", [0, 1, 2, 3])
async def test_a_turn_generates_once_per_answered_request_and_no_more(
    answered: int,
) -> None:
    # Counted, never timed. An abstention is decided by the gates, before generation,
    # so *m* answered requests cost exactly *m* calls however many others abstained -
    # which is what keeps serving the answerable half from costing more than abstaining
    # on the whole turn did.
    questions = [f"q{i}?" for i in range(3)]
    pools = {q: [_chunk(i)] for i, q in enumerate(questions)}
    reranked: dict[str, list[ScoredChunk] | None] = {
        q: ([_chunk(i, rerank=0.9)] if i < answered else [])
        for i, q in enumerate(questions)
    }

    result, recorder = await _run_many(pools=pools, reranked=reranked)

    # Absent rather than zero when nothing generated: the recorder only ever sees a
    # call that happened.
    assert int(recorder.get("calls", 0)) == answered
    assert len(recorder["arrivals"]) == 3
    assert [a.verdict.answered for a in result.segment_answers] == [
        i < answered for i in range(3)
    ]
