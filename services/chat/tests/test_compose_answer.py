"""Tests for the merge step: what survives it, and what it is forbidden to invent."""

import asyncio
from collections.abc import AsyncIterator
from typing import Self
from unittest.mock import MagicMock

import pytest
from chat.agent.compose_answer import (
    FaqResult,
    TurnCompletion,
    compose_answer,
    record_single_specialist_completion,
)
from chat.agent.handle_booking import BookingOutcome
from chat.core.correlation import bind_turn_id
from chat.domain.schemas import AnswerSource, ChatDoneEvent, ChatTokenEvent, Citation
from chat.rag.retriever import TurnPipelineError
from structlog.testing import capture_logs

from .conftest import FakeAnthropicStream, FakeTextEvent

_CITATION = Citation(entry_id=1, chunk_index=0, chunk_text="Visiting hours are 8-5.")
_REPLY_IDS = ["01TURN"]


def _client(tokens: list[str]) -> MagicMock:
    client = MagicMock()
    client.messages.stream.return_value = FakeAnthropicStream(tokens)
    return client


def _grounded_faq() -> FaqResult:
    return FaqResult(
        answer_text="Visiting hours are 8am to 5pm.",
        citations=[_CITATION],
        grounded=True,
        chunk_scores=[0.9],
    )


def _abstaining_faq() -> FaqResult:
    return FaqResult(
        answer_text="I don't have a confident answer to that.",
        citations=[],
        grounded=False,
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
        faq_result=_grounded_faq(),
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
        faq_result=_grounded_faq(),
        booking_reply="Booked.",
        booking_outcome=str(BookingOutcome.BOOKED),
    )

    assert done.citations == [_CITATION]
    assert done.grounded is True


async def test_an_abstaining_faq_half_is_reported_as_an_abstention() -> None:
    client = _client(["merged"])

    _, done = await _compose(
        client,
        faq_result=_abstaining_faq(),
        booking_reply="Booked.",
        booking_outcome=str(BookingOutcome.BOOKED),
    )

    assert done.grounded is False
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
        faq_result=_grounded_faq(),
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
        faq_result=_grounded_faq(),
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
            faq_result=_grounded_faq(),
            booking_reply="Booked.",
            booking_outcome=str(BookingOutcome.BOOKED),
        )

    completions = [e for e in logs if e["event"] == "turn.completed"]
    assert len(completions) == 1
    assert completions[0]["answer_source"] == AnswerSource.MERGED
    assert completions[0]["booking_outcome"] == "booked"
    assert completions[0]["citations"][0]["score"] == 0.9


# --- the single-specialist no-op path ----------------------------------------


def test_a_single_specialist_turn_emits_only_its_completion() -> None:
    with capture_logs() as logs:
        completion = TurnCompletion()
        record_single_specialist_completion(
            completion,
            answer_source=AnswerSource.FAQ,
            grounded=True,
            booking_outcome=None,
            answer_text="Visiting hours are 8am to 5pm.",
            citations=[{**_CITATION.model_dump(), "score": 0.9}],
            reply_to_message_ids=_REPLY_IDS,
        )
        completion.emit()

    assert [e["event"] for e in logs] == ["turn.completed"]
    assert logs[0]["outcome"] == "grounded"
    assert logs[0]["answer_source"] == AnswerSource.FAQ


def test_a_completion_emitted_within_a_turn_reports_the_turn_duration() -> None:
    with capture_logs() as logs, bind_turn_id():
        completion = TurnCompletion()
        record_single_specialist_completion(
            completion,
            answer_source=AnswerSource.FAQ,
            grounded=True,
            booking_outcome=None,
            answer_text="Visiting hours are 8am to 5pm.",
            citations=[],
            reply_to_message_ids=_REPLY_IDS,
        )
        completion.emit()

    assert logs[0]["duration_ms"] >= 0


def test_a_completion_emitted_outside_a_turn_reports_no_duration() -> None:
    with capture_logs() as logs:
        completion = TurnCompletion()
        record_single_specialist_completion(
            completion,
            answer_source=AnswerSource.FAQ,
            grounded=True,
            booking_outcome=None,
            answer_text="Visiting hours are 8am to 5pm.",
            citations=[],
            reply_to_message_ids=_REPLY_IDS,
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
            grounded=False,
            booking_outcome=None,
            answer_text="I don't have a confident answer to that.",
            citations=[],
            reply_to_message_ids=_REPLY_IDS,
        )
        completion.emit()

    assert logs[0]["outcome"] == "abstained"
    assert logs[0]["abstention_message"] == "I don't have a confident answer to that."


def test_a_booking_only_turn_reports_no_groundedness_verdict() -> None:
    with capture_logs() as logs:
        completion = TurnCompletion()
        record_single_specialist_completion(
            completion,
            answer_source=AnswerSource.BOOKING,
            grounded=None,
            booking_outcome=str(BookingOutcome.BOOKED),
            answer_text="You're booked for Friday.",
            citations=[],
            reply_to_message_ids=_REPLY_IDS,
        )
        completion.emit()

    assert logs[0]["grounded"] is None
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
        faq_result=_grounded_faq(),
        booking_reply="Something about an appointment.",
        booking_outcome=str(outcome),
    )

    prompt = client.messages.stream.call_args.kwargs["messages"][0]["content"]
    assert f"outcome: {outcome.value}" in prompt


async def test_the_system_prompt_forbids_claiming_an_unrecorded_change() -> None:
    client = _client(["merged"])

    await _compose(
        client,
        faq_result=_grounded_faq(),
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
        faq_result=_grounded_faq(),
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
            faq_result=_grounded_faq(),
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
            faq_result=_grounded_faq(),
            booking_reply="Friday at 9 it is.",
            booking_outcome=str(BookingOutcome.BOOKED),
        )

    task = asyncio.create_task(consume())
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
