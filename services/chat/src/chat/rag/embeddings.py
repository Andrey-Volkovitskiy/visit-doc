"""Voyage AI embeddings client wrapper (research.md #1)."""

from typing import Literal

from voyageai.client_async import AsyncClient

from chat.core.config import Settings

_MODEL = "voyage-3-lite"


async def embed_texts(
    texts: list[str],
    settings: Settings,
    input_type: Literal["document", "query"] = "document",
) -> list[list[float]]:
    """Embed `texts` via Voyage AI, returning one vector per input string."""
    client = AsyncClient(api_key=settings.VOYAGE_API_KEY)
    result = await client.embed(texts, model=_MODEL, input_type=input_type)
    return [[float(x) for x in vector] for vector in result.embeddings]
