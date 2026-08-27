"""Pydantic request/response DTOs."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from chat.domain.models import PATIENT_NAME_LENGTH
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
    """Which specialist(s) produced the reply a turn ended with."""

    FAQ = "faq"
    BOOKING = "booking"
    MERGED = "merged"


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
    """Terminal NDJSON event: this request's generation was superseded.

    Emitted instead of `ChatDoneEvent` when a newer message on the same chat arrived
    before this one finished generating - no reply was stored for this request; any
    `token` events already received for it should be discarded, not shown as final or
    as an error.
    """

    type: Literal["cancelled"] = "cancelled"


class MessageOut(BaseModel):
    """A single message in a chat's history.

    `grounded`/`citations` are only meaningful for `sender="assistant"`; always None
    for a patient message.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    sender: Literal["patient", "assistant"]
    content: str
    grounded: bool | None = None
    citations: list[Citation] | None = None
    created_at: datetime


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


class ChatPatientUpdate(BaseModel):
    """`PATCH /chats/{chat_id}/patient` request body.

    The bounds match the scheduler's own, so a name this service accepts is never one
    the scheduler will then reject for its length.
    """

    full_name: str = Field(min_length=1, max_length=PATIENT_NAME_LENGTH)


class ChatPatientOut(BaseModel):
    """`PATCH /chats/{chat_id}/patient` response body.

    Carries only what the rename changed. The client patches this into the row it is
    already showing, so the response does not restate the rest of a `ChatSummary` - and
    cannot disagree with it.
    """

    chat_id: str
    patient_name: str


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
