"""Pydantic request/response DTOs (contracts/openapi.yaml)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from chat.domain.validation import is_meaningless


class ChatRequest(BaseModel):
    """`POST /chat` request body (FR-001, FR-001a)."""

    message: str = Field(min_length=1, max_length=2000)


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
