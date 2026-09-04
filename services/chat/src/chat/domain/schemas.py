"""Pydantic request/response DTOs."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from chat.domain.validation import is_meaningless

_ULID_LENGTH = 26


class ChatRequest(BaseModel):
    """`POST /chat` request body.

    `local_now` is the visitor's own clock, sent on every turn. It resolves relative
    phrasing ("tomorrow", "next Tuesday at 3") and is the only clock any past,
    upcoming, or booking-horizon judgement is made against - so it must carry no
    timezone, because there is none to carry.
    """

    chat_id: str = Field(min_length=_ULID_LENGTH, max_length=_ULID_LENGTH)
    message: str = Field(min_length=1, max_length=2000)
    local_now: datetime

    @field_validator("local_now")
    @classmethod
    def _reject_timezone_aware(cls, value: datetime) -> datetime:
        """Raises: ValueError if `value` carries a timezone offset."""
        if value.tzinfo is not None:
            raise ValueError("local_now must carry no timezone offset")
        return value


class IntentLabel(StrEnum):
    """Legal values for a classified patient-message intent.

    The first four members are the classifier's own closed output set;
    `CLASSIFICATION_FAILED` is assigned only by orchestration code on a failed/invalid
    classification call, never returned by the classifier itself - excluded from its
    request schema's `enum`, so it's structurally unreachable from a model response,
    not just a convention.
    """

    FAQ_QUESTION = "faq_question"
    BOOKING = "booking"
    CALL_STAFF = "call_staff"
    UNKNOWN = "unknown"
    CLASSIFICATION_FAILED = "classification_failed"


class IntentClassificationResult(BaseModel):
    """The parsed, validated result of one `classify_intent()` call.

    Never contains `CLASSIFICATION_FAILED` - that value is assigned by the caller when
    `classify_intent()` raises, not returned in a result.
    """

    intents: list[IntentLabel] = Field(min_length=1)


class Citation(BaseModel):
    """A retrieved chunk cited in a grounded answer, verbatim."""

    entry_id: int
    chunk_index: int
    chunk_text: str


class ChatTokenEvent(BaseModel):
    """An incremental slice of the streamed answer."""

    type: Literal["token"] = "token"
    text: str


class AnswerSource(StrEnum):
    """Which specialist(s) produced the reply a turn ended with.

    `HAND_OFF` is the one that produced no answer at all: the visitor asked for a
    person, so the turn fetched one and told them so, and nothing was retrieved,
    booked or generated.
    """

    FAQ = "faq"
    BOOKING = "booking"
    MERGED = "merged"
    HAND_OFF = "hand_off"


class ChatDoneEvent(BaseModel):
    """Terminal NDJSON event: provenance, groundedness flag, citations, and message.

    `grounded` is None when no FAQ specialist ran, since a booking reply is streamed
    text that was never retrieved against and so is neither grounded nor abstaining.
    `message` keeps its meaning: set only when there is no streamed text to show, which
    today is the FAQ abstention case. A client renders `message` if present, otherwise
    the tokens it accumulated. `citations` are always empty for a booking-only reply.
    """

    type: Literal["done"] = "done"
    grounded: bool | None
    citations: list[Citation]
    message: str | None = None
    answer_source: AnswerSource = AnswerSource.FAQ


class ChatCancelledEvent(BaseModel):
    """Terminal NDJSON event: this request produced no reply, and nothing was stored.

    Emitted instead of `ChatDoneEvent` when a newer message on the same chat arrived
    before this one finished generating, or when a person took the conversation over
    before its reply could be written. Either way no reply was stored for this request;
    any `token` events already received for it should be discarded, not shown as final
    or as an error.
    """

    type: Literal["cancelled"] = "cancelled"


class ChatSilentEvent(BaseModel):
    """Terminal NDJSON event: the assistant may not speak in this conversation.

    Emitted instead of `ChatDoneEvent` when the conversation is escalated or the
    assistant is paused in it. The message was accepted and stored, and it carries the
    mark saying nothing answered it - so a client renders nothing for this and leaves
    the message in the thread.

    A third terminal value rather than a reuse of the other two, because both already
    mean something else: `cancelled` tells a client to discard a message that is in fact
    being kept, and an empty `done` announces a reply that does not exist.
    """

    type: Literal["silent"] = "silent"


class MessageOut(BaseModel):
    """A single message in a chat's history.

    `grounded`/`citations` are only meaningful for `sender="assistant"`; always None for
    a patient message and for a staff one, which was never retrieved against.

    `attention_mark` is only ever set on a patient message: which of the four kinds it
    is, or None for no mark. There is deliberately no field naming the person who wrote
    a staff message - `sender` carries everything a client's label states, and this
    system has no such person to name.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    sender: Literal["patient", "assistant", "staff"]
    content: str
    grounded: bool | None = None
    citations: list[Citation] | None = None
    attention_mark: (
        Literal[
            "patient_asked_for_person",
            "corpus_could_not_answer",
            "assistant_failed",
            "unanswered",
        ]
        | None
    ) = None
    created_at: datetime


class StaffMessageWrite(BaseModel):
    """`POST /console/chats/{chat_id}/messages` request body.

    The same bounds and the same meaningless-content rule a patient message faces: a
    staff member typing whitespace into the composer has said nothing either.
    """

    content: str = Field(min_length=1, max_length=2000)

    @field_validator("content")
    @classmethod
    def _reject_meaningless_content(cls, value: str) -> str:
        """Raises: ValueError if `value` has no meaningful text."""
        if is_meaningless(value):
            raise ValueError("content must contain meaningful text")
        return value


class ConsoleConversationOut(BaseModel):
    """One conversation as the staff side lists it.

    `emphasized`, `assistant_may_reply` and `pause_seconds_remaining` are derived rather
    than stored, so the switch a staff member sees and the gate a turn obeys are the
    same answer. `pause_seconds_remaining` is null whenever no pause is running -
    including while escalated, which has no deadline for a countdown to show.

    Carries no session id: no response on this surface repeats the credential the
    browser is not allowed to read.
    """

    chat_id: str
    patient_name: str | None
    last_message_at: datetime | None
    emphasized: bool
    escalated: bool
    escalation_reason: str | None
    attention_since: datetime | None
    assistant_may_reply: bool
    pause_seconds_remaining: int | None


class ConsoleConversationsResponse(BaseModel):
    """`GET /console/conversations` response body - the one polled read model.

    `attention_total` counts *conversations* needing a person, once each however many
    marks sit inside one: four unanswered messages in a thread are one person's problem,
    not four.
    """

    attention_total: int
    conversations: list[ConsoleConversationOut]


class AssistantSwitchWrite(BaseModel):
    """`POST /console/chats/{chat_id}/assistant` request body.

    `enabled` is the position the switch is being moved to, not a toggle: two tabs
    showing the same conversation must not turn it into a race over whose click was
    second.
    """

    enabled: bool


class AssistantStateOut(BaseModel):
    """What the assistant may do in one conversation, after a change to it.

    Both fields are derived from the stored columns, so the answer returned here is the
    same one the poll will report a moment later.
    """

    assistant_may_reply: bool
    pause_seconds_remaining: int | None


class ChatHistoryResponse(BaseModel):
    """`GET /chats/{chat_id}/messages` response: the chat's messages, chronological.

    Not guaranteed to alternate sender.
    """

    messages: list[MessageOut]


class ChatSummary(BaseModel):
    """One row of the session's chat list.

    `patient_name` is None while this chat's patient record does not exist yet; the
    client renders its own placeholder from `created_at` rather than the server
    inventing a label it would then have to keep consistent.
    """

    id: str
    patient_name: str | None
    created_at: datetime
    last_message_at: datetime | None


class ChatListResponse(BaseModel):
    """`GET /chats` response body.

    `chats` is already in display order - chats holding messages first, newest message
    first, then chats with none, newest-created first - so the client opens `chats[0]`
    rather than re-deriving the rule on every render. May be empty: a session with zero
    chats is a valid state.

    `session_exists` is what tells an empty list apart from a session the user emptied,
    and the two require opposite behavior - a first arrival is given a chat, an emptied
    session is left alone. The client cannot make that distinction itself: the session
    cookie is `HttpOnly`, so it never sees one.
    """

    chats: list[ChatSummary]
    session_exists: bool


class FaqEntryWrite(BaseModel):
    """`POST`/`PUT /faq` request body."""

    content: str = Field(min_length=1, max_length=20000)

    @field_validator("content")
    @classmethod
    def _reject_meaningless_content(cls, value: str) -> str:
        """Raises: ValueError if `value` has no meaningful text."""
        if is_meaningless(value):
            raise ValueError("content must contain meaningful text")
        return value


class FaqEntry(FaqEntryWrite):
    """`FaqEntryWrite` plus server-assigned fields."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
