"""What the FAQ node stands its answer on, and when it refuses to answer at all.

Retrieval and reranking are patched at this node's own seams, so every case here is
about the node's decisions - what reaches the prompt, what becomes a citation, which
verdict is recorded, and which calls are never made - rather than about a provider.
"""

from collections.abc import AsyncIterator
from typing import Self
from unittest.mock import AsyncMock, patch

import pytest
from chat.agent.answer_faq import answer_faq
from chat.agent.compose_answer import FaqResult
from chat.agent.escalation import EscalationRequests
from chat.core.config import get_settings
from chat.domain.models import EscalationReason, Message, MessageSender
from chat.domain.schemas import ChatDoneEvent, ChatTokenEvent, FaqVerdict
from chat.rag.pipeline import ScoredChunk
from structlog.testing import capture_logs

_SESSION = "01JQ0000000000000000000000"
_REVISIONS = ["01JQ1111111111111111111111"]


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

    def __init__(self, text: str, recorder: dict[str, object]) -> None:
        self._text = text
        self._recorder = recorder

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def __aiter__(self) -> AsyncIterator[object]:
        from types import SimpleNamespace

        yield SimpleNamespace(type="text", text=self._text)


def _anthropic(recorder: dict[str, object]) -> AsyncMock:
    client = AsyncMock()

    def stream(**kwargs: object) -> _Stream:
        recorder["calls"] = int(recorder.get("calls", 0)) + 1
        recorder["messages"] = kwargs.get("messages")
        return _Stream("an answer", recorder)

    client.messages.stream = stream
    return client


async def _run(
    *,
    pool: list[ScoredChunk],
    reranked: list[ScoredChunk] | None,
    live_revisions: list[str] | None = None,
    question: str = "what should I bring?",
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
            _anthropic(recorder),
            _bursts(question),
            ["p1"],
            _SESSION,
            _REVISIONS if live_revisions is None else live_revisions,
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

    assert result.verdict is FaqVerdict.ANSWERED
    assert [c.chunk_index for c in result.citations] == [0, 3]
    prompt = str(recorder["messages"])
    assert "chunk text 0" in prompt
    assert "chunk text 3" in prompt


async def test_a_chunk_the_reranker_rejected_is_in_neither_context_nor_citations() -> (
    None
):
    pool = [_chunk(0), _chunk(1)]
    survivors = [_chunk(0, rerank=0.9)]

    result, recorder, _, _ = await _run(pool=pool, reranked=survivors)

    assert [c.chunk_index for c in result.citations] == [0]
    assert "chunk text 1" not in str(recorder["messages"])


async def test_the_cited_set_and_the_context_set_are_identical() -> None:
    survivors = [_chunk(0, rerank=0.9), _chunk(1, rerank=0.8)]

    result, recorder, _, _ = await _run(pool=[_chunk(0), _chunk(1)], reranked=survivors)

    prompt = str(recorder["messages"])
    cited = {c.chunk_text for c in result.citations}
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


# --- US2: the three abstentions ----------------------------------------------------


async def test_an_empty_corpus_abstains_without_embedding_or_searching() -> None:
    result, recorder, rerank, escalation = await _run(
        pool=[], reranked=None, live_revisions=[]
    )

    assert result.verdict is FaqVerdict.ABSTAINED_EMPTY_CORPUS
    assert result.citations == []
    assert recorder.get("calls") is None
    rerank.assert_not_awaited()
    assert EscalationReason.CORPUS_COULD_NOT_ANSWER in escalation.recorded


async def test_a_below_floor_pool_abstains_with_no_rerank_and_no_generation() -> None:
    result, recorder, rerank, _ = await _run(
        pool=[_chunk(0, similarity=0.05)], reranked=None
    )

    assert result.verdict is FaqVerdict.ABSTAINED_SIMILARITY_FLOOR
    assert recorder.get("calls") is None
    rerank.assert_not_awaited()


async def test_an_all_rejected_rerank_abstains_with_no_generation() -> None:
    result, recorder, _, _ = await _run(pool=[_chunk(0)], reranked=[])

    assert result.verdict is FaqVerdict.ABSTAINED_RERANK_FLOOR
    assert result.citations == []
    assert recorder.get("calls") is None


@pytest.mark.parametrize(
    ("pool", "reranked", "revisions"),
    [
        ([], None, []),
        ([_chunk(0, similarity=0.05)], None, None),
        ([_chunk(0)], [], None),
    ],
)
async def test_all_three_abstentions_are_identical_to_the_patient(
    pool: list[ScoredChunk],
    reranked: list[ScoredChunk] | None,
    revisions: list[str] | None,
) -> None:
    result, _, _, escalation = await _run(
        pool=pool, reranked=reranked, live_revisions=revisions
    )

    assert not result.verdict.answered
    assert result.citations == []
    assert "knowledge base" in result.answer_text
    assert EscalationReason.CORPUS_COULD_NOT_ANSWER in escalation.recorded


# --- US3: the reranker is unavailable ----------------------------------------------


async def test_a_reranker_failure_answers_from_the_similarity_survivors() -> None:
    pool = [_chunk(i) for i in range(8)]

    result, recorder, _, _ = await _run(pool=pool, reranked=None)

    assert result.verdict is FaqVerdict.ANSWERED_UNRERANKED
    assert len(result.citations) == 5
    assert recorder.get("calls") == 1


async def test_a_reranker_failure_calls_no_staff() -> None:
    # A dependency outage is not a corpus gap, and the patient received an answer.
    _, _, _, escalation = await _run(pool=[_chunk(0)], reranked=None)

    assert escalation.recorded == ()


async def test_a_reranker_failure_does_not_rescue_a_similarity_abstention() -> None:
    result, recorder, _, _ = await _run(
        pool=[_chunk(0, similarity=0.01)], reranked=None
    )

    assert result.verdict is FaqVerdict.ABSTAINED_SIMILARITY_FLOOR
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
            escalation=EscalationRequests(),
            stream=True,
        ):
            events.append(event)

    done = [e for e in events if isinstance(e, ChatDoneEvent)]
    assert len(done) == 1
    assert done[0].faq_verdict is FaqVerdict.ANSWERED
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
    similarity = await _logged(pool=[_chunk(0, similarity=0.01)], reranked=None)
    rerank = await _logged(pool=[_chunk(0)], reranked=[])

    assert empty["faq.verdict"]["gate"] == "empty_corpus"
    assert similarity["faq.verdict"]["gate"] == "similarity_floor"
    assert rerank["faq.verdict"]["gate"] == "rerank_floor"


async def test_the_verdict_event_reports_the_best_score_seen() -> None:
    # "Nothing was close" and "something was close and the floor was too high" are
    # different problems with different fixes.
    events = await _logged(pool=[_chunk(0, similarity=0.29)], reranked=None)

    verdict = events["faq.verdict"]
    assert verdict["verdict"] == FaqVerdict.ABSTAINED_SIMILARITY_FLOOR.value
    assert verdict["best_score_seen"] == 0.29
    assert verdict["survivor_count"] == 0


async def test_an_empty_corpus_reports_no_best_score_rather_than_zero() -> None:
    events = await _logged(pool=[], reranked=None, live_revisions=[])

    assert events["faq.verdict"]["best_score_seen"] is None


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

    assert result.verdict is FaqVerdict.ANSWERED
    assert [c.chunk_index for c in result.citations] == [0]
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
    assert events["faq.verdict"]["gate"] == "empty_corpus"


async def test_a_searched_corpus_matching_nothing_still_raises_both_events() -> None:
    # The other side of the same rule: a search that ran and found nothing usable is a
    # decision the gate made, and it has to be visible as one.
    events = await _logged(pool=[_chunk(0, similarity=0.01)], reranked=None)

    assert events["faq.retrieval_completed"]["pool_returned"] == 1
    assert events["faq.similarity_gate"]["kept"] == []
    assert events["faq.verdict"]["gate"] == "similarity_floor"
