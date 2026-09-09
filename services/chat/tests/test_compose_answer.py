"""Tests for the merge step: what survives it, and what it is forbidden to invent."""

import asyncio
from collections.abc import AsyncIterator
from typing import Self
from unittest.mock import MagicMock

import pytest
from chat.agent.compose_answer import (
    FaqResult,
    FaqSegmentAnswer,
    TurnCompletion,
    compose_answer,
    record_single_specialist_completion,
)
from chat.agent.handle_booking import BookingOutcome
from chat.core.correlation import bind_turn_id
from chat.core.errors import TurnPipelineError
from chat.domain.schemas import (
    MAX_SEGMENTS,
    AnswerSource,
    ChatDoneEvent,
    ChatTokenEvent,
    Citation,
    FaqVerdict,
    IntentLabel,
    RequestSegment,
)
from chat.rag.pipeline import ScoredChunk
from structlog.testing import capture_logs

from .conftest import FakeAnthropicStream, FakeTextEvent

_CITATION = Citation(entry_id=1, chunk_index=0, chunk_text="Visiting hours are 8-5.")
_SEGMENTS = [
    RequestSegment(intent=IntentLabel.FAQ_QUESTION, text="when can I visit?"),
    RequestSegment(intent=IntentLabel.BOOKING, text="book me Friday"),
]
_REPLY_IDS = ["01TURN"]


def _client(tokens: list[str], stop_reason: str = "end_turn") -> MagicMock:
    client = MagicMock()
    client.messages.stream.return_value = FakeAnthropicStream(
        tokens, stop_reason=stop_reason
    )
    return client


def _answered_faq() -> FaqResult:
    return FaqResult.from_segments(
        [
            FaqSegmentAnswer(
                position=0,
                question="when can I visit?",
                answer_text="Visiting hours are 8am to 5pm.",
                verdict=FaqVerdict.ANSWERED,
                citations=[_CITATION],
                scored_chunks=[
                    ScoredChunk(
                        faq_entry_id=_CITATION.entry_id,
                        chunk_index=_CITATION.chunk_index,
                        chunk_text=_CITATION.chunk_text,
                        similarity_score=0.9,
                        rerank_score=0.8,
                    )
                ],
            )
        ],
        abstention_message="unused",
    )


def _abstaining_faq() -> FaqResult:
    return FaqResult.from_segments(
        [
            FaqSegmentAnswer(
                position=0,
                question="when can I visit?",
                answer_text="",
                verdict=FaqVerdict.ABSTAINED_RERANK_FLOOR,
                citations=[],
                scored_chunks=[],
            )
        ],
        abstention_message="I don't have a confident answer to that.",
    )


async def _compose(
    client: MagicMock,
    *,
    faq_result: FaqResult | None,
    booking_reply: str | None,
    booking_outcome: str | None,
) -> tuple[list[ChatTokenEvent], ChatDoneEvent]:
    tokens: list[ChatTokenEvent] = []
    done: ChatDoneEvent | None = None
    completion = TurnCompletion()
    async for event in compose_answer(
        client,
        segments=_SEGMENTS,
        faq_result=faq_result,
        booking_reply=booking_reply,
        booking_outcome=booking_outcome,
        reply_to_message_ids=_REPLY_IDS,
        completion=completion,
    ):
        if isinstance(event, ChatDoneEvent):
            done = event
        else:
            tokens.append(event)
    # The node emits the recorded completion once its span has closed; these tests
    # stand in for that caller so the event is still observable here.
    completion.emit()
    assert done is not None
    return tokens, done


async def test_a_merged_turn_streams_one_reply_and_one_terminal_event() -> None:
    client = _client(["Hours are 8-5, ", "and you're booked for Friday."])

    tokens, done = await _compose(
        client,
        faq_result=_answered_faq(),
        booking_reply="You're booked for Friday.",
        booking_outcome=str(BookingOutcome.BOOKED),
    )

    assert "".join(t.text for t in tokens) == (
        "Hours are 8-5, and you're booked for Friday."
    )
    assert done.answer_source == AnswerSource.MERGED


async def test_the_faq_halfs_citations_are_carried_through_structurally() -> None:
    """The composing model never re-reports citations - they come from what was
    retrieved, so a merged answer cites exactly what a single-specialist one would.
    """
    client = _client(["merged"])

    _, done = await _compose(
        client,
        faq_result=_answered_faq(),
        booking_reply="Booked.",
        booking_outcome=str(BookingOutcome.BOOKED),
    )

    assert done.citations == [_CITATION]
    assert done.faq_verdict is FaqVerdict.ANSWERED


async def test_an_abstaining_faq_half_is_reported_as_an_abstention() -> None:
    client = _client(["merged"])

    _, done = await _compose(
        client,
        faq_result=_abstaining_faq(),
        booking_reply="Booked.",
        booking_outcome=str(BookingOutcome.BOOKED),
    )

    assert done.faq_verdict is FaqVerdict.ABSTAINED_RERANK_FLOOR
    assert done.citations == []
    prompt = client.messages.stream.call_args.kwargs["messages"][0]["content"]
    assert "no confident answer" in prompt
    # The abstaining half's own text is deliberately not offered as an answer to
    # rephrase - only the instruction to say plainly that there isn't one.
    assert "Visiting hours" not in prompt


@pytest.mark.parametrize(
    "outcome",
    [
        BookingOutcome.REFUSED,
        BookingOutcome.UNAVAILABLE,
        BookingOutcome.AWAITING_CONFIRMATION,
    ],
)
async def test_a_booking_that_did_not_happen_is_never_composed_into_a_success(
    outcome: BookingOutcome,
) -> None:
    """The composing model is constrained by the machine-derived outcome, not by how
    the booking half phrased itself - so the constraint reaches the prompt verbatim.
    """
    client = _client(["merged"])

    await _compose(
        client,
        faq_result=_answered_faq(),
        booking_reply="That time was taken.",
        booking_outcome=str(outcome),
    )

    prompt = client.messages.stream.call_args.kwargs["messages"][0]["content"]
    system = client.messages.stream.call_args.kwargs["system"]
    assert f"outcome: {outcome.value}" in prompt
    assert "never write anything that\n  suggests one exists" in system


async def test_the_composing_prompt_carries_both_halves() -> None:
    client = _client(["merged"])

    await _compose(
        client,
        faq_result=_answered_faq(),
        booking_reply="You're booked for Friday.",
        booking_outcome=str(BookingOutcome.BOOKED),
    )

    prompt = client.messages.stream.call_args.kwargs["messages"][0]["content"]
    assert "Visiting hours are 8am to 5pm." in prompt
    assert "You're booked for Friday." in prompt


async def test_a_merged_turn_logs_completion_once_with_scored_citations() -> None:
    client = _client(["merged"])

    with capture_logs() as logs:
        await _compose(
            client,
            faq_result=_answered_faq(),
            booking_reply="Booked.",
            booking_outcome=str(BookingOutcome.BOOKED),
        )

    completions = [e for e in logs if e["event"] == "turn.completed"]
    assert len(completions) == 1
    assert completions[0]["answer_source"] == AnswerSource.MERGED
    assert completions[0]["booking_outcome"] == "booked"
    # Both scores per citation now: a disagreement between the two stages is the
    # phase's whole thesis, so the completion record carries the pair.
    assert completions[0]["citations"][0]["similarity_score"] == 0.9
    assert completions[0]["citations"][0]["rerank_score"] == 0.8


# --- the single-specialist no-op path ----------------------------------------


def test_a_single_specialist_turn_emits_only_its_completion() -> None:
    with capture_logs() as logs:
        completion = TurnCompletion()
        record_single_specialist_completion(
            completion,
            answer_source=AnswerSource.FAQ,
            verdict=FaqVerdict.ANSWERED,
            booking_outcome=None,
            answer_text="Visiting hours are 8am to 5pm.",
            citations=[{**_CITATION.model_dump(), "score": 0.9}],
            reply_to_message_ids=_REPLY_IDS,
            segments=_SEGMENTS,
        )
        completion.emit()

    assert [e["event"] for e in logs] == ["turn.completed"]
    assert logs[0]["outcome"] == "answered"
    assert logs[0]["answer_source"] == AnswerSource.FAQ


def test_a_completion_emitted_within_a_turn_reports_the_turn_duration() -> None:
    with capture_logs() as logs, bind_turn_id():
        completion = TurnCompletion()
        record_single_specialist_completion(
            completion,
            answer_source=AnswerSource.FAQ,
            verdict=FaqVerdict.ANSWERED,
            booking_outcome=None,
            answer_text="Visiting hours are 8am to 5pm.",
            citations=[],
            reply_to_message_ids=_REPLY_IDS,
            segments=_SEGMENTS,
        )
        completion.emit()

    assert logs[0]["duration_ms"] >= 0


def test_a_completion_emitted_outside_a_turn_reports_no_duration() -> None:
    with capture_logs() as logs:
        completion = TurnCompletion()
        record_single_specialist_completion(
            completion,
            answer_source=AnswerSource.FAQ,
            verdict=FaqVerdict.ANSWERED,
            booking_outcome=None,
            answer_text="Visiting hours are 8am to 5pm.",
            citations=[],
            reply_to_message_ids=_REPLY_IDS,
            segments=_SEGMENTS,
        )
        completion.emit()

    # Absent, rather than a null that would read as an instant turn.
    assert "duration_ms" not in logs[0]


def test_an_abstained_single_specialist_turn_keeps_its_abstention_message() -> None:
    with capture_logs() as logs:
        completion = TurnCompletion()
        record_single_specialist_completion(
            completion,
            answer_source=AnswerSource.FAQ,
            verdict=FaqVerdict.ABSTAINED_SIMILARITY_FLOOR,
            booking_outcome=None,
            answer_text="I don't have a confident answer to that.",
            citations=[],
            reply_to_message_ids=_REPLY_IDS,
            segments=_SEGMENTS,
        )
        completion.emit()

    assert logs[0]["outcome"] == "abstained_similarity_floor"
    assert logs[0]["abstention_message"] == "I don't have a confident answer to that."


def test_a_booking_only_turn_reports_no_faq_verdict() -> None:
    with capture_logs() as logs:
        completion = TurnCompletion()
        record_single_specialist_completion(
            completion,
            answer_source=AnswerSource.BOOKING,
            verdict=None,
            booking_outcome=str(BookingOutcome.BOOKED),
            answer_text="You're booked for Friday.",
            citations=[],
            reply_to_message_ids=_REPLY_IDS,
            segments=_SEGMENTS,
        )
        completion.emit()

    assert logs[0]["faq_verdict"] is None
    assert logs[0]["booking_outcome"] == "booked"
    assert "abstention_message" not in logs[0]


@pytest.mark.parametrize(
    "outcome",
    [
        BookingOutcome.CANCELLED,
        BookingOutcome.UNCHANGED,
        BookingOutcome.OUTCOME_UNKNOWN,
    ],
)
async def test_the_merged_prompt_states_which_change_actually_completed(
    outcome: BookingOutcome,
) -> None:
    """A merged reply cannot claim a change the outcome does not record.

    The outcome is machine-derived from the tool results, so putting it in the prompt
    verbatim is what stops the composing model inferring a cancellation from how the
    booking half happened to phrase itself.
    """
    client = _client(["merged"])

    await _compose(
        client,
        faq_result=_answered_faq(),
        booking_reply="Something about an appointment.",
        booking_outcome=str(outcome),
    )

    prompt = client.messages.stream.call_args.kwargs["messages"][0]["content"]
    assert f"outcome: {outcome.value}" in prompt


async def test_the_system_prompt_forbids_claiming_an_unrecorded_change() -> None:
    client = _client(["merged"])

    await _compose(
        client,
        faq_result=_answered_faq(),
        booking_reply="Something about an appointment.",
        booking_outcome=str(BookingOutcome.OUTCOME_UNKNOWN),
    )

    system = client.messages.stream.call_args.kwargs["system"]
    assert "cancelled" in system
    assert "rescheduled" in system
    assert "outcome_unknown" in system


async def test_an_unknown_outcome_may_not_be_composed_as_nothing_having_happened() -> (
    None
):
    # The one sentence the unknown path forbids: a lost answer is not evidence that
    # the change did not land.
    client = _client(["merged"])

    await _compose(
        client,
        faq_result=_answered_faq(),
        booking_reply="I could not confirm that.",
        booking_outcome=str(BookingOutcome.OUTCOME_UNKNOWN),
    )

    system = client.messages.stream.call_args.kwargs["system"]
    assert "not known" in system.lower()
    assert "did not happen" in system.lower() or "nothing happened" in system.lower()


async def test_a_failing_composing_call_is_tagged_as_a_generation_failure() -> None:
    # Untagged, this reached the turn's catch-all as `pipeline_step="unknown"` and
    # raised no `critical.dependency_unreachable` - so the same Anthropic outage
    # alerted on a FAQ-only turn and stayed silent on a merged one.
    client = MagicMock()
    client.messages.stream.side_effect = RuntimeError("overloaded")

    with pytest.raises(TurnPipelineError) as raised:
        await _compose(
            client,
            faq_result=_answered_faq(),
            booking_reply="Friday at 9 it is.",
            booking_outcome=str(BookingOutcome.BOOKED),
        )

    assert raised.value.pipeline_step == "generation"
    assert "overloaded" in str(raised.value.cause)


class _StallingStream:
    """A stream that yields one token and then never produces another."""

    def __init__(self, first_token: str, started: asyncio.Event) -> None:
        self._first_token = first_token
        self._started = started

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[FakeTextEvent]:
        return self._generate()

    async def _generate(self) -> AsyncIterator[FakeTextEvent]:
        yield FakeTextEvent(self._first_token)
        self._started.set()
        await asyncio.Event().wait()


async def test_cancelling_a_merged_turn_is_still_a_cancellation() -> None:
    # A staff member taking the conversation cancels the turn's task while the merge
    # is mid-stream. `except Exception` must not see that - `CancelledError` is a
    # `BaseException` - or every takeover would be logged as a model outage.
    started = asyncio.Event()
    client = MagicMock()
    client.messages.stream.return_value = _StallingStream("Visiting ", started)

    async def consume() -> None:
        await _compose(
            client,
            faq_result=_answered_faq(),
            booking_reply="Friday at 9 it is.",
            booking_outcome=str(BookingOutcome.BOOKED),
        )

    task = asyncio.create_task(consume())
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


# --- Phase 1f: the notice owed alongside an answer -----------------------------------


def test_the_composer_is_told_when_a_notice_is_owed() -> None:
    from chat.agent.compose_answer import _build_prompt

    prompt = _build_prompt(None, "Monday at 9am is booked.", None, notice_required=True)

    lowered = prompt.lower()
    assert "not authorized" in lowered
    assert "staff" in lowered


def test_no_notice_is_mentioned_when_none_is_owed() -> None:
    from chat.agent.compose_answer import _build_prompt

    prompt = _build_prompt(
        None, "Monday at 9am is booked.", None, notice_required=False
    )

    assert "not authorized" not in prompt.lower()


def test_the_composing_prompt_forbids_claiming_the_request_was_served() -> None:
    from chat.agent.compose_answer import _SYSTEM_PROMPT

    lowered = _SYSTEM_PROMPT.lower()
    assert "not authorized" in lowered
    # The three obligations, and the two things it must never do (FR-022c1).
    assert "never promise" in lowered or "never say when" in lowered
    assert "never claim" in lowered or "never suggest" in lowered


def test_the_prompt_describes_the_parts_it_is_actually_given() -> None:
    """FR-022d's path supplies one specialist plus a notice, not two halves.

    The prompt used to open by telling the model its input "had two parts: a question,
    and something about an appointment" and that "two specialists have already handled
    them". On a not-authorized merge neither is true, and a model told something false
    about its own input is free to invent the half it was promised.
    """
    from chat.agent.compose_answer import _SYSTEM_PROMPT

    # Whitespace-normalized: the prompt wraps at the line length the linter enforces,
    # and where it happens to break is not part of the contract.
    lowered = " ".join(_SYSTEM_PROMPT.lower().split())
    assert "two parts" not in lowered
    assert "two specialists" not in lowered
    # It must still say the parts are labelled and that only the labelled ones were in
    # the message - otherwise "more than one part" invites the same invention.
    assert "labelled" in lowered
    assert "only the parts labelled below were in the message" in lowered


# --- what the composer merged, not just that it merged -------------------------------


async def _completion_fields(
    client: MagicMock,
    *,
    faq_result: FaqResult | None,
    booking_reply: str | None,
    notice_required: bool,
) -> dict[str, object]:
    """Compose one turn and return the fields its `turn.completed` carried."""
    completion = TurnCompletion()
    with capture_logs() as logs:
        async for _event in compose_answer(
            client,
            segments=_SEGMENTS,
            faq_result=faq_result,
            booking_reply=booking_reply,
            booking_outcome=None,
            reply_to_message_ids=_REPLY_IDS,
            completion=completion,
            notice_required=notice_required,
        ):
            pass
        completion.emit()
    return next(e for e in logs if e["event"] == "turn.completed")


async def test_a_merge_records_whether_a_notice_was_part_of_it() -> None:
    """`merged` says the composer wrote the reply; it does not say what went into it.

    One specialist plus a notice and two specialists are different turns that record
    the same `answer_source`, so a reader counting mixed-intent merges over-counts
    unless the notice is on the record too. It cannot be another `answer_source` value:
    a notice can accompany one specialist *or* two, so the two facts are orthogonal and
    an enum that tried to carry both would need a value per combination.
    """
    fields = await _completion_fields(
        _client(["Hours are 8-5, and the receipt has gone to staff."]),
        faq_result=_answered_faq(),
        booking_reply=None,
        notice_required=True,
    )

    assert fields["answer_source"] == AnswerSource.MERGED
    assert fields["notice_included"] is True


async def test_a_genuine_two_specialist_merge_records_no_notice() -> None:
    fields = await _completion_fields(
        _client(["Hours are 8-5, and you're booked for Friday."]),
        faq_result=_answered_faq(),
        booking_reply="Booked for Friday.",
        notice_required=False,
    )

    assert fields["answer_source"] == AnswerSource.MERGED
    assert fields["notice_included"] is False


# --- Phase 1g: several answers from one specialist -----------------------------------


def _answer(position: int, question: str, text: str, entry_id: int) -> FaqSegmentAnswer:
    citation = Citation(
        entry_id=entry_id, chunk_index=0, chunk_text=f"chunk {entry_id}"
    )
    return FaqSegmentAnswer(
        position=position,
        question=question,
        answer_text=text,
        verdict=FaqVerdict.ANSWERED,
        citations=[citation],
        scored_chunks=[
            ScoredChunk(
                faq_entry_id=entry_id,
                chunk_index=0,
                chunk_text=citation.chunk_text,
                similarity_score=0.9,
                rerank_score=0.8,
            )
        ],
    )


def _two_answered_requests() -> FaqResult:
    return FaqResult.from_segments(
        [
            _answer(0, "where are you?", "We are at 5 Oak Street.", 1),
            _answer(1, "what should I bring?", "Bring your ID.", 2),
        ],
        abstention_message="unused",
    )


async def test_two_answers_from_one_specialist_are_merged_into_one_reply() -> None:
    client = _client(["We are at 5 Oak Street, and bring your ID."])

    tokens, done = await _compose(
        client,
        faq_result=_two_answered_requests(),
        booking_reply=None,
        booking_outcome=None,
    )

    assert "".join(t.text for t in tokens) == (
        "We are at 5 Oak Street, and bring your ID."
    )
    assert done.answer_source == AnswerSource.MERGED


async def test_each_answer_reaches_the_composer_beside_its_own_question() -> None:
    # An answer attached to the wrong question is a wrong answer, not a formatting
    # problem - so the prompt names the question each answer belongs to.
    client = _client(["merged"])

    await _compose(
        client,
        faq_result=_two_answered_requests(),
        booking_reply=None,
        booking_outcome=None,
    )

    prompt = str(client.messages.stream.call_args.kwargs["messages"])
    assert "where are you?" in prompt
    assert "We are at 5 Oak Street." in prompt
    assert "what should I bring?" in prompt
    assert "Bring your ID." in prompt


async def test_every_answered_request_reaches_the_reply() -> None:
    # No servable request may be silently dropped on the way to the merge.
    client = _client(["merged"])

    await _compose(
        client,
        faq_result=_two_answered_requests(),
        booking_reply=None,
        booking_outcome=None,
    )

    prompt = str(client.messages.stream.call_args.kwargs["messages"])
    assert prompt.count("Answer to the question") == 2


async def test_the_citations_of_every_request_are_carried_through() -> None:
    client = _client(["merged"])

    _, done = await _compose(
        client,
        faq_result=_two_answered_requests(),
        booking_reply=None,
        booking_outcome=None,
    )

    assert sorted(c.entry_id for c in done.citations) == [1, 2]


async def test_an_abstaining_half_still_renders_as_one_gap() -> None:
    # The FAQ half abstains whole, so the composer is told about one gap however many
    # requests failed.
    faq = FaqResult.from_segments(
        [
            FaqSegmentAnswer(
                position=0,
                question="where are you?",
                answer_text="",
                verdict=FaqVerdict.ABSTAINED_RERANK_FLOOR,
                citations=[],
                scored_chunks=[],
            ),
            FaqSegmentAnswer(
                position=1,
                question="what should I bring?",
                answer_text="",
                verdict=FaqVerdict.ABSTAINED_EMPTY_POOL,
                citations=[],
                scored_chunks=[],
            ),
        ],
        abstention_message="I don't have a confident answer to that.",
    )
    client = _client(["merged"])

    _, done = await _compose(
        client,
        faq_result=faq,
        booking_reply="Booked.",
        booking_outcome=str(BookingOutcome.BOOKED),
    )

    prompt = str(client.messages.stream.call_args.kwargs["messages"])
    assert prompt.count("no confident answer") == 1
    assert done.faq_verdict is FaqVerdict.ABSTAINED_RERANK_FLOOR


async def test_a_merged_reply_cut_off_at_the_cap_says_so_in_the_log() -> None:
    # The merge is the one step whose halves cannot report a cut that happened here:
    # each of them finished inside its own budget, and only this call ran past one.
    client = _client(["Visiting hours are 8-5, and you're booked for"], "max_tokens")

    with capture_logs() as logs:
        await _compose(
            client,
            faq_result=_answered_faq(),
            booking_reply="Friday at 9 it is.",
            booking_outcome=str(BookingOutcome.BOOKED),
        )

    truncated = next(entry for entry in logs if entry["event"] == "compose.truncated")
    assert (
        truncated["max_tokens"] == client.messages.stream.call_args.kwargs["max_tokens"]
    )


async def test_a_merged_reply_that_finished_on_its_own_records_no_truncation() -> None:
    client = _client(["Visiting hours are 8-5."])

    with capture_logs() as logs:
        await _compose(
            client,
            faq_result=_answered_faq(),
            booking_reply="Friday at 9 it is.",
            booking_outcome=str(BookingOutcome.BOOKED),
        )

    assert not [e for e in logs if e["event"] == "compose.truncated"]


async def test_the_merge_budget_holds_every_part_it_can_be_handed() -> None:
    # A cap below the sum of the parts is one the worst legal merge runs past. Read off
    # the halves' own caps rather than restated, so raising one of theirs without
    # raising this one fails here instead of clipping a reply in production.
    from chat.agent.answer_faq import _MAX_TOKENS as FAQ_MAX_TOKENS
    from chat.agent.handle_booking import _MAX_TOKENS as BOOKING_MAX_TOKENS

    client = _client(["Merged."])

    await _compose(
        client,
        faq_result=_answered_faq(),
        booking_reply="Friday at 9 it is.",
        booking_outcome=str(BookingOutcome.BOOKED),
    )

    sent = client.messages.stream.call_args.kwargs["max_tokens"]
    assert sent >= MAX_SEGMENTS * FAQ_MAX_TOKENS + BOOKING_MAX_TOKENS
