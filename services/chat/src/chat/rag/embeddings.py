"""Voyage AI embeddings client wrapper (research.md #1)."""

from typing import Literal

from voyageai.client_async import AsyncClient

_MODEL = "voyage-3-lite"


async def embed_texts(
    client: AsyncClient,
    texts: list[str],
    input_type: Literal["document", "query"] = "document",
) -> list[list[float]]:
    """Embed `texts` via Voyage AI, returning one vector per input string."""
    result = await client.embed(texts, model=_MODEL, input_type=input_type)
    return [[float(x) for x in vector] for vector in result.embeddings]
