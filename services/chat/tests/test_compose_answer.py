"""Tests for the merge step: what survives it, and what it is forbidden to invent."""

import asyncio
from collections.abc import AsyncIterator
from typing import Self
from unittest.mock import MagicMock

import pytest
from chat.agent.compose_answer import (
    _SYSTEM_PROMPT,
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
    RequestOutcome,
    RequestSegment,
)
from chat.rag.pipeline import ScoredChunk
from pydantic import ValidationError
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

    assert done.request_outcomes is not None
    assert [c for o in done.request_outcomes for c in o.citations] == [_CITATION]
    assert [o.verdict for o in done.request_outcomes] == [FaqVerdict.ANSWERED]


async def test_an_abstaining_faq_half_is_reported_as_an_abstention() -> None:
    client = _client(["merged"])

    _, done = await _compose(
        client,
        faq_result=_abstaining_faq(),
        booking_reply="Booked.",
        booking_outcome=str(BookingOutcome.BOOKED),
    )

    assert done.request_outcomes is not None
    assert [o.verdict for o in done.request_outcomes] == [
        FaqVerdict.ABSTAINED_RERANK_FLOOR
    ]
    assert [o.answer for o in done.request_outcomes] == [None]
    prompt = client.messages.stream.call_args.kwargs["messages"][0]["content"]
    assert "NO CONFIDENT ANSWER" in prompt
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
    assert completions[0]["outcome"] == "merged"
    assert completions[0]["booking_outcome"] == "booked"
    # Both scores per citation now, under the request whose answer stood on them: a
    # disagreement between the two stages is the phase's whole thesis, so the record
    # carries the pair - and carries it per request, since a turn may now have two
    # verdicts and neither describes the other's evidence.
    cited = completions[0]["request_outcomes"][0]["citations"][0]
    assert cited["similarity_score"] == 0.9
    assert cited["rerank_score"] == 0.8


# --- the single-specialist no-op path ----------------------------------------


def test_a_single_specialist_turn_emits_only_its_completion() -> None:
    with capture_logs() as logs:
        completion = TurnCompletion()
        record_single_specialist_completion(
            completion,
            answer_source=AnswerSource.FAQ,
            booking_outcome=None,
            answer_text="Visiting hours are 8am to 5pm.",
            reply_to_message_ids=_REPLY_IDS,
            segments=_SEGMENTS,
            faq_result=_answered_faq(),
        )
        completion.emit()

    assert [e["event"] for e in logs] == ["turn.completed"]
    # The turn's shape, never a verdict: which gate stopped which request is each
    # request's own outcome to report.
    assert logs[0]["outcome"] == "faq"


def test_a_completion_emitted_within_a_turn_reports_the_turn_duration() -> None:
    with capture_logs() as logs, bind_turn_id():
        completion = TurnCompletion()
        record_single_specialist_completion(
            completion,
            answer_source=AnswerSource.FAQ,
            booking_outcome=None,
            answer_text="Visiting hours are 8am to 5pm.",
            reply_to_message_ids=_REPLY_IDS,
            segments=_SEGMENTS,
            faq_result=_answered_faq(),
        )
        completion.emit()

    assert logs[0]["duration_ms"] >= 0


def test_a_completion_emitted_outside_a_turn_reports_no_duration() -> None:
    with capture_logs() as logs:
        completion = TurnCompletion()
        record_single_specialist_completion(
            completion,
            answer_source=AnswerSource.FAQ,
            booking_outcome=None,
            answer_text="Visiting hours are 8am to 5pm.",
            reply_to_message_ids=_REPLY_IDS,
            segments=_SEGMENTS,
            faq_result=_answered_faq(),
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
            booking_outcome=None,
            answer_text="I don't have a confident answer to that.",
            reply_to_message_ids=_REPLY_IDS,
            segments=_SEGMENTS,
            faq_result=_abstaining_faq(),
        )
        completion.emit()

    assert logs[0]["outcome"] == "faq"
    assert logs[0]["request_outcomes"] == [
        {
            "position": 0,
            "verdict": "abstained_rerank_floor",
            "citations": [],
        }
    ]
    assert logs[0]["abstention_message"] == "I don't have a confident answer to that."


def test_a_booking_only_turn_reports_no_request_outcomes() -> None:
    with capture_logs() as logs:
        completion = TurnCompletion()
        record_single_specialist_completion(
            completion,
            answer_source=AnswerSource.BOOKING,
            booking_outcome=str(BookingOutcome.BOOKED),
            answer_text="You're booked for Friday.",
            reply_to_message_ids=_REPLY_IDS,
            segments=_SEGMENTS,
        )
        completion.emit()

    # A booking reply was never retrieved against, so it has no request outcome to
    # report at all - the key is absent, not an empty list.
    assert "request_outcomes" not in logs[0]
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

    assert fields["outcome"] == "merged"
    assert fields["notice_included"] is True


async def test_a_genuine_two_specialist_merge_records_no_notice() -> None:
    fields = await _completion_fields(
        _client(["Hours are 8-5, and you're booked for Friday."]),
        faq_result=_answered_faq(),
        booking_reply="Booked for Friday.",
        notice_required=False,
    )

    assert fields["outcome"] == "merged"
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

    assert done.request_outcomes is not None
    assert sorted(c.entry_id for o in done.request_outcomes for c in o.citations) == [
        1,
        2,
    ]


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
    assert prompt.count("NO CONFIDENT ANSWER") == 1
    assert done.request_outcomes is not None
    assert [o.verdict for o in done.request_outcomes] == [
        FaqVerdict.ABSTAINED_RERANK_FLOOR,
        FaqVerdict.ABSTAINED_EMPTY_POOL,
    ]


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


# --- Phase 1h: the verdict, the answer and the citations move onto the request --------


def _abstained(position: int, question: str, verdict: FaqVerdict) -> FaqSegmentAnswer:
    return FaqSegmentAnswer(
        position=position,
        question=question,
        answer_text="",
        verdict=verdict,
        citations=[],
        scored_chunks=[],
    )


def test_an_answered_request_projects_its_own_answer_and_citations() -> None:
    segment = _answer(0, "where are you?", "We are at 5 Oak Street.", 1)
    outcome = segment.request_outcome()

    assert outcome.position == 0
    assert outcome.answer == "We are at 5 Oak Street."
    assert outcome.verdict is FaqVerdict.ANSWERED
    assert [c.entry_id for c in outcome.citations] == [1]


def test_an_outcomes_question_is_the_classifiers_restatement() -> None:
    # Not the patient's own wording: this is the text the pipeline retrieved for, so
    # the record cannot say the evidence was selected for a question nobody asked.
    segment = _answer(0, "where is the clinic located?", "5 Oak Street.", 1)

    assert segment.request_outcome().question == "where is the clinic located?"


def test_an_abstained_request_projects_no_answer_and_no_citation() -> None:
    outcome = _abstained(
        1, "what should I bring?", FaqVerdict.ABSTAINED_RERANK_FLOOR
    ).request_outcome()

    assert outcome.answer is None
    assert outcome.citations == []
    assert outcome.verdict is FaqVerdict.ABSTAINED_RERANK_FLOOR


def test_an_abstained_outcome_may_not_be_built_carrying_an_answer() -> None:
    # An empty string would be a second way of saying "abstained" that a reader could
    # disagree with the verdict about, so the type refuses both it and real text.
    with pytest.raises(ValidationError):
        RequestOutcome(
            position=0,
            question="where are you?",
            answer="We are at 5 Oak Street.",
            verdict=FaqVerdict.ABSTAINED_EMPTY_POOL,
            citations=[],
        )


def test_an_abstained_outcome_may_not_be_built_carrying_a_citation() -> None:
    with pytest.raises(ValidationError):
        RequestOutcome(
            position=0,
            question="where are you?",
            answer=None,
            verdict=FaqVerdict.ABSTAINED_EMPTY_POOL,
            citations=[_CITATION],
        )


def test_an_answered_outcome_may_not_be_built_without_an_answer() -> None:
    with pytest.raises(ValidationError):
        RequestOutcome(
            position=0,
            question="where are you?",
            answer=None,
            verdict=FaqVerdict.ANSWERED,
            citations=[_CITATION],
        )


def test_an_answered_outcome_may_not_be_built_without_a_citation() -> None:
    # An answered request cites the survivors that were placed in its prompt, which is
    # at least one - generation is what produced the verdict.
    with pytest.raises(ValidationError):
        RequestOutcome(
            position=0,
            question="where are you?",
            answer="We are at 5 Oak Street.",
            verdict=FaqVerdict.ANSWERED,
            citations=[],
        )


def test_the_turns_outcomes_are_in_ascending_position_order() -> None:
    result = FaqResult.from_segments(
        [
            _answer(0, "where are you?", "5 Oak Street.", 1),
            _abstained(1, "what should I bring?", FaqVerdict.ABSTAINED_EMPTY_POOL),
            _answer(2, "when are you open?", "8 to 5.", 2),
        ],
        abstention_message="unused",
    )

    assert [o.position for o in result.request_outcomes] == [0, 1, 2]


def test_a_half_built_out_of_order_is_refused() -> None:
    # Order is the message's order, and it is what the reply, the console and the
    # derived unserved list all read - so it is structural, not a caller's promise.
    with pytest.raises(ValueError, match="position"):
        FaqResult.from_segments(
            [
                _answer(1, "what should I bring?", "Your ID.", 2),
                _answer(0, "where are you?", "5 Oak Street.", 1),
            ],
            abstention_message="unused",
        )


def test_a_half_carrying_one_position_twice_is_refused() -> None:
    with pytest.raises(ValueError, match="position"):
        FaqResult.from_segments(
            [
                _answer(0, "where are you?", "5 Oak Street.", 1),
                _answer(0, "what should I bring?", "Your ID.", 2),
            ],
            abstention_message="unused",
        )


def test_the_retrieval_scores_never_reach_the_wire_type() -> None:
    # `scored_chunks` are the log's, and `Citation` deliberately carries no number:
    # putting them on the wire would either leak them or need stripping at every call
    # site.
    outcome = _answer(0, "where are you?", "5 Oak Street.", 1).request_outcome()

    dumped = outcome.model_dump()
    assert "scored_chunks" not in dumped
    assert set(dumped["citations"][0]) == {"entry_id", "chunk_index", "chunk_text"}


# --- US1: the turn answers what it can and names what it cannot ----------------------


def _partly_answered() -> FaqResult:
    """Two requests: the first answered, the second stopped at the rerank floor."""
    return FaqResult.from_segments(
        [
            _answer(0, "where are you?", "We are at 5 Oak Street.", 1),
            _abstained(1, "what does a scan cost?", FaqVerdict.ABSTAINED_RERANK_FLOOR),
        ],
        abstention_message="unused: this half answered one request",
    )


def test_an_answered_request_survives_a_siblings_abstention() -> None:
    result = _partly_answered()

    outcomes = result.request_outcomes
    assert outcomes[0].answer == "We are at 5 Oak Street."
    assert [c.entry_id for c in outcomes[0].citations] == [1]
    assert outcomes[1].answer is None


def test_the_gap_is_one_part_however_many_requests_fell_into_it() -> None:
    partly = _partly_answered()
    two_gaps = FaqResult.from_segments(
        [
            _answer(0, "where are you?", "We are at 5 Oak Street.", 1),
            _abstained(1, "what does a scan cost?", FaqVerdict.ABSTAINED_EMPTY_POOL),
            _abstained(2, "do you bulk bill?", FaqVerdict.ABSTAINED_RERANK_FLOOR),
        ],
        abstention_message="unused",
    )

    assert partly.part_count == 2
    assert two_gaps.part_count == 2


def test_a_fully_answered_half_contributes_one_part_per_request() -> None:
    assert _two_answered_requests().part_count == 2


def test_an_all_abstained_half_contributes_exactly_one_part() -> None:
    result = FaqResult.from_segments(
        [
            _abstained(0, "where are you?", FaqVerdict.ABSTAINED_EMPTY_POOL),
            _abstained(1, "what does a scan cost?", FaqVerdict.ABSTAINED_RERANK_FLOOR),
        ],
        abstention_message="I don't have that in the knowledge base.",
    )

    assert result.part_count == 1
    assert result.answer_text == "I don't have that in the knowledge base."


def test_the_half_has_one_text_only_when_it_contributes_one_part() -> None:
    # Several answers have no one text, and joining them would put a reply nobody wrote
    # into the record as the turn's answer.
    assert _partly_answered().answer_text is None
    assert _two_answered_requests().answer_text is None
    assert _answered_faq().answer_text == "Visiting hours are 8am to 5pm."


async def test_the_gap_block_lists_every_unanswered_question_in_order() -> None:
    client = _client(["merged"])
    faq = FaqResult.from_segments(
        [
            _answer(0, "where are you?", "We are at 5 Oak Street.", 1),
            _abstained(1, "what does a scan cost?", FaqVerdict.ABSTAINED_EMPTY_POOL),
            _abstained(2, "do you bulk bill?", FaqVerdict.ABSTAINED_RERANK_FLOOR),
        ],
        abstention_message="unused",
    )

    await _compose(client, faq_result=faq, booking_reply=None, booking_outcome=None)

    prompt = str(client.messages.stream.call_args.kwargs["messages"])
    assert prompt.count("NO CONFIDENT ANSWER") == 1
    assert prompt.index("what does a scan cost?") < prompt.index("do you bulk bill?")
    assert "We are at 5 Oak Street." in prompt


async def test_the_gap_block_says_the_gap_is_named_in_the_composers_own_words() -> None:
    # The input slice and the instruction fail independently, so the rule is in the
    # block as well as in the system prompt.
    client = _client(["merged"])

    await _compose(
        client,
        faq_result=_partly_answered(),
        booking_reply=None,
        booking_outcome=None,
    )

    prompt = str(client.messages.stream.call_args.kwargs["messages"])
    assert "own words" in prompt
    assert "do NOT quote" in prompt


async def test_a_fully_answered_half_reaches_the_composer_with_no_gap_block() -> None:
    client = _client(["merged"])

    await _compose(
        client,
        faq_result=_two_answered_requests(),
        booking_reply=None,
        booking_outcome=None,
    )

    prompt = str(client.messages.stream.call_args.kwargs["messages"])
    assert "NO CONFIDENT ANSWER" not in prompt


async def test_an_abstained_question_is_never_offered_as_an_empty_answer() -> None:
    # Labelled as unanswered, never as an answer block with nothing under it: the
    # prompt requires every labelled claim to be preserved exactly, and a label with an
    # empty body is an invitation to write one.
    client = _client(["merged"])

    await _compose(
        client,
        faq_result=_partly_answered(),
        booking_reply=None,
        booking_outcome=None,
    )

    prompt = str(client.messages.stream.call_args.kwargs["messages"])
    assert 'Answer to the question "what does a scan cost?"' not in prompt
    assert 'Answer to the question "where are you?"' in prompt


# --- US2: the answered half never covers for the gap ---------------------------------


def test_the_system_prompt_carries_the_three_constraints_on_a_gap() -> None:
    # A prompt clause is necessary and not sufficient - its *effect* is measured by the
    # Phase 8 procedure against committed data. What is checkable here is that the
    # clause is present at all, which is what a prompt edit can silently drop.
    # Wrapping is a formatting detail of the prompt, not part of the clause, so the
    # assertion reads it with its line breaks collapsed.
    system = " ".join(_SYSTEM_PROMPT.lower().split())

    # 1. Never soften a gap.
    assert "do not promise to look into it" in system
    assert "do not suggest when staff will reply" in system
    # 2. Never extend an answer to cover a gap.
    assert "answers that question only" in system
    assert "two questions about the same subject are still two questions" in system
    # 3. Name each gap in your own words.
    assert "in your own words" in system
    assert "never by quoting the restatement" in system


def test_every_constraint_the_prompt_already_carried_survives() -> None:
    # The three above are added to the existing list, not written over it: each of
    # these prevents a failure of its own that this phase does not repeal.
    system = _SYSTEM_PROMPT

    # The claim-preservation rule.
    assert "Preserve every factual claim exactly as given." in system
    # The booking outcome wording rules, all nine outcomes.
    for outcome in BookingOutcome:
        assert f'"{outcome.value}"' in system
    assert "never write anything that\n  suggests one exists" in system
    # The not-authorized notice's three requirements.
    assert "NOT AUTHORIZED" in system
    assert "not authorized to handle that request" in system
    assert "forwarded to the clinic's staff who will follow up" in system
    assert "ask you\n  about anything else in the meantime" in system


async def test_an_unanswered_question_reaches_the_composer_labelled_as_unanswered() -> (
    None
):
    # Labelled, not merely absent: the composer needs the question to name the gap at
    # all, and needs it labelled so the claim-preservation rule extends to it.
    client = _client(["merged"])

    await _compose(
        client,
        faq_result=_partly_answered(),
        booking_reply=None,
        booking_outcome=None,
    )

    prompt = client.messages.stream.call_args.kwargs["messages"][0]["content"]
    gap_start = prompt.index("NO CONFIDENT ANSWER")
    assert "what does a scan cost?" in prompt[gap_start:]
    assert "what does a scan cost?" not in prompt[:gap_start]


# --- US4: the log answers the same way the stored record does ------------------------


def _degraded_faq() -> FaqResult:
    """One request answered from chunks no cross-encoder ever saw."""
    citation = Citation(entry_id=3, chunk_index=0, chunk_text="chunk 3")
    return FaqResult.from_segments(
        [
            FaqSegmentAnswer(
                position=0,
                question="where are you?",
                answer_text="We are at 5 Oak Street.",
                verdict=FaqVerdict.ANSWERED_UNRERANKED,
                citations=[citation],
                scored_chunks=[
                    ScoredChunk(
                        faq_entry_id=3,
                        chunk_index=0,
                        chunk_text="chunk 3",
                        similarity_score=0.7,
                        rerank_score=None,
                    )
                ],
            )
        ],
        abstention_message="unused",
    )


async def test_a_merged_turn_reports_no_turn_level_verdict_or_source() -> None:
    client = _client(["merged"])

    with capture_logs() as logs:
        await _compose(
            client,
            faq_result=_partly_answered(),
            booking_reply=None,
            booking_outcome=None,
        )

    completed = next(e for e in logs if e["event"] == "turn.completed")
    assert "faq_verdict" not in completed
    assert "citations" not in completed
    # `outcome` names the turn's shape, so `answer_source` would be the same fact in a
    # second field of the same event.
    assert "answer_source" not in completed
    assert completed["outcome"] == "merged"


def test_a_single_request_turn_that_abstained_reports_its_shape_not_its_verdict() -> (
    None
):
    with capture_logs() as logs:
        completion = TurnCompletion()
        record_single_specialist_completion(
            completion,
            answer_source=AnswerSource.FAQ,
            booking_outcome=None,
            answer_text="I don't have that in the knowledge base.",
            reply_to_message_ids=_REPLY_IDS,
            segments=_SEGMENTS,
            faq_result=_abstaining_faq(),
        )
        completion.emit()

    assert logs[0]["outcome"] == "faq"
    assert "faq_verdict" not in logs[0]
    assert "citations" not in logs[0]
    assert "answer_source" not in logs[0]


async def test_a_partly_served_turn_logs_two_verdicts_and_no_abstention_message() -> (
    None
):
    # Filing an answered reply as an abstention is what setting it here would do: the
    # patient was told something, and `abstention_message` means the reply *was* the
    # abstention.
    client = _client(["merged"])

    with capture_logs() as logs:
        await _compose(
            client,
            faq_result=_partly_answered(),
            booking_reply=None,
            booking_outcome=None,
        )

    completed = next(e for e in logs if e["event"] == "turn.completed")
    assert [o["verdict"] for o in completed["request_outcomes"]] == [
        "answered",
        "abstained_rerank_floor",
    ]
    assert "abstention_message" not in completed


def test_an_all_abstained_turn_logs_the_constant_the_patient_saw() -> None:
    with capture_logs() as logs:
        completion = TurnCompletion()
        record_single_specialist_completion(
            completion,
            answer_source=AnswerSource.FAQ,
            booking_outcome=None,
            answer_text="I don't have that in the knowledge base.",
            reply_to_message_ids=_REPLY_IDS,
            segments=_SEGMENTS,
            faq_result=_abstaining_faq(),
        )
        completion.emit()

    assert logs[0]["abstention_message"] == "I don't have that in the knowledge base."


def test_a_booking_only_turn_logs_no_request_outcomes_key() -> None:
    with capture_logs() as logs:
        completion = TurnCompletion()
        record_single_specialist_completion(
            completion,
            answer_source=AnswerSource.BOOKING,
            booking_outcome=str(BookingOutcome.BOOKED),
            answer_text="You're booked for Friday.",
            reply_to_message_ids=_REPLY_IDS,
            segments=_SEGMENTS,
        )
        completion.emit()

    assert "request_outcomes" not in logs[0]


def test_a_degraded_requests_citations_carry_no_rerank_score_at_all() -> None:
    # Absent, not zero and not a copy of the similarity score: no rerank score was ever
    # obtained for it, and a zero would read as one the cross-encoder gave.
    with capture_logs() as logs:
        completion = TurnCompletion()
        record_single_specialist_completion(
            completion,
            answer_source=AnswerSource.FAQ,
            booking_outcome=None,
            answer_text="We are at 5 Oak Street.",
            reply_to_message_ids=_REPLY_IDS,
            segments=_SEGMENTS,
            faq_result=_degraded_faq(),
        )
        completion.emit()

    cited = logs[0]["request_outcomes"][0]["citations"][0]
    assert cited["similarity_score"] == 0.7
    assert cited["rerank_score"] is None
