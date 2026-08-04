"""`POST /chat` — NDJSON streaming endpoint (FR-001..FR-005)."""

from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from chat.agent.answer_faq import answer_faq
from chat.core.config import get_settings
from chat.domain.schemas import ChatRequest

router = APIRouter()


@router.post("/chat")
async def post_chat(chat_request: ChatRequest, request: Request) -> StreamingResponse:
    """Ask a question and receive a streamed, grounded (or abstaining) answer."""
    client = request.app.state.qdrant_client
    settings = get_settings()

    async def event_stream() -> AsyncIterator[bytes]:
        async for event in answer_faq(client, settings, chat_request.message):
            yield (event.model_dump_json() + "\n").encode()

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")
