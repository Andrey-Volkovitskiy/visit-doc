"""Pydantic request/response DTOs (contracts/openapi.yaml)."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from chat.domain.validation import is_meaningless


class ChatRequest(BaseModel):
    """`POST /chat` request body (FR-001, FR-001a)."""

    message: str = Field(min_length=1, max_length=2000)


class IntentLabel(StrEnum):
    """Legal values for a classified patient-message intent (data-model.md).

    The first four members are the classifier's own closed output set (FR-003);
    `CLASSIFICATION_FAILED` is assigned only by orchestration code (`agent/graph.py`'s
    `classify_intent_node`) on a failed/invalid classification call, never by the
    classifier itself (FR-007, research.md #3) - `classify_intent()`'s own request
    schema excludes it from the model's `enum`, so it's structurally unreachable from a
    model response, not just a convention.
    """

    FAQ_QUESTION = "faq_question"
    BOOKING = "booking"
    CALL_STAFF = "call_staff"
    UNKNOWN = "unknown"
    CLASSIFICATION_FAILED = "classification_failed"


class IntentClassificationResult(BaseModel):
    """The parsed, validated result of one `classify_intent()` call (data-model.md).

    Never contains `CLASSIFICATION_FAILED` - that value is assigned by the caller when
    `classify_intent()` raises, not returned in a result (research.md #3).
    """

    intents: list[IntentLabel] = Field(min_length=1)


class Citation(BaseModel):
    """A retrieved chunk cited in a grounded answer, verbatim (research.md #13)."""

    entry_id: int
    chunk_index: int
    chunk_text: str


class ChatTokenEvent(BaseModel):
    """An incremental slice of the streamed answer (FR-004)."""

    type: Literal["token"] = "token"
    text: str


class ChatDoneEvent(BaseModel):
    """Terminal NDJSON event: groundedness flag, citations, and abstention message."""

    type: Literal["done"] = "done"
    grounded: bool
    citations: list[Citation]
    message: str | None = None


class ChatCancelledEvent(BaseModel):
    """Terminal NDJSON event: this request's generation was superseded (FR-015).

    Emitted instead of `ChatDoneEvent` when a newer message on the same chat arrived
    before this one finished generating - no reply was stored for this request; any
    `token` events already received for it should be discarded, not shown as final or
    as an error (contracts/openapi.yaml).
    """

    type: Literal["cancelled"] = "cancelled"


class MessageOut(BaseModel):
    """A single message in a chat's history (FR-002, contracts/openapi.yaml `Message`).

    `grounded`/`citations` are only meaningful for `sender="assistant"`; always None
    for a patient message (data-model.md).
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    sender: Literal["patient", "assistant"]
    content: str
    grounded: bool | None = None
    citations: list[Citation] | None = None
    created_at: datetime


class ChatHistoryResponse(BaseModel):
    """`GET /chat` response body: the chat's messages, chronological (FR-002).

    Not guaranteed to alternate sender (FR-002, FR-014).
    """

    messages: list[MessageOut]


class FaqEntryWrite(BaseModel):
    """`POST`/`PUT /faq` request body (content 1-20,000 chars, FR-015)."""

    content: str = Field(min_length=1, max_length=20000)

    @field_validator("content")
    @classmethod
    def _reject_meaningless_content(cls, value: str) -> str:
        """Raises: ValueError if `value` has no meaningful text (FR-009)."""
        if is_meaningless(value):
            raise ValueError("content must contain meaningful text")
        return value


class FaqEntry(FaqEntryWrite):
    """`FaqEntryWrite` plus server-assigned fields."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
