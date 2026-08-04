"""`answer_faq`: retrieve -> groundedness gate -> generate/stream (research.md #9).

Plain async function, no agent framework — LangGraph is deferred to Phase 1
(docs/ROADMAP.md); this function's shape is what a future LangGraph node will wrap.
"""

from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic
from qdrant_client import AsyncQdrantClient

from chat.core.config import Settings
from chat.domain.schemas import ChatDoneEvent, ChatTokenEvent, Citation
from chat.rag.groundedness import is_grounded
from chat.rag.retriever import search_faq

_MODEL = "claude-sonnet-5"
_SYSTEM_PROMPT = (
    "You are a clinic assistant. Answer the visitor's question using ONLY the provided "
    "context. Do not use outside knowledge. Be concise."
)
_ABSTENTION_MESSAGE = "I don't have a confident answer to that."


async def answer_faq(
    client: AsyncQdrantClient, settings: Settings, message: str
) -> AsyncIterator[ChatTokenEvent | ChatDoneEvent]:
    """Retrieve context for `message`, then stream a grounded answer or abstain."""
    chunks = await search_faq(client, settings, message)

    if not is_grounded(chunks):
        yield ChatDoneEvent(grounded=False, citations=[], message=_ABSTENTION_MESSAGE)
        return

    context = "\n\n".join(chunk.chunk_text for chunk in chunks)
    prompt = f"Context:\n{context}\n\nQuestion: {message}"

    anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    async with anthropic_client.messages.stream(
        model=_MODEL,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        async for event in stream:
            if event.type == "text":
                yield ChatTokenEvent(text=event.text)

    citations = [
        Citation(
            entry_id=c.faq_entry_id, chunk_index=c.chunk_index, chunk_text=c.chunk_text
        )
        for c in chunks
    ]
    yield ChatDoneEvent(grounded=True, citations=citations)
