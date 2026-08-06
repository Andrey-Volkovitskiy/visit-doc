"""`POST /chat` — NDJSON streaming endpoint (FR-001..FR-006, FR-015, FR-018)."""

from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from chat.agent.answer_faq import answer_faq
from chat.api.dependencies import get_voyage_client
from chat.core.correlation import bind_turn_id
from chat.core.logging import get_logger
from chat.domain.schemas import ChatRequest
from chat.rag.retriever import TurnPipelineError

router = APIRouter()

# Pipeline steps backed by an FR-015-scoped dependency (qdrant/anthropic_api) - not
# "embedding" (Voyage) or "groundedness" (pure computation), per spec.md Assumptions.
_CRITICAL_DEPENDENCY_BY_STEP = {"retrieval": "qdrant", "generation": "anthropic_api"}


@router.post("/chat")
async def post_chat(chat_request: ChatRequest, request: Request) -> StreamingResponse:
    """Ask a question and receive a streamed, grounded (or abstaining) answer."""
    client = request.app.state.qdrant_client
    voyage_client = get_voyage_client(request)
    anthropic_client = request.app.state.anthropic_client

    async def event_stream() -> AsyncIterator[bytes]:
        with bind_turn_id():
            try:
                async for event in answer_faq(
                    client, voyage_client, anthropic_client, chat_request.message
                ):
                    yield (event.model_dump_json() + "\n").encode()
            except TurnPipelineError as exc:
                logger = get_logger()
                logger.error(
                    "turn.error",
                    pipeline_step=exc.pipeline_step,
                    error_detail=str(exc.cause),
                )
                dependency = _CRITICAL_DEPENDENCY_BY_STEP.get(exc.pipeline_step)
                if dependency is not None:
                    logger.critical(
                        "critical.dependency_unreachable",
                        dependency=dependency,
                        error_detail=str(exc.cause),
                    )
                raise
            except Exception as exc:
                get_logger().error(
                    "turn.error", pipeline_step="unknown", error_detail=str(exc)
                )
                raise

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")
