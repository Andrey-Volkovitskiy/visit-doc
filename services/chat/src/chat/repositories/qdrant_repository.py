"""Qdrant repository: chunk upsert/search/delete-by-entry + collection bootstrap."""

import uuid
from dataclasses import dataclass

from pydantic import BaseModel
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from chat.core.config import Settings, get_settings
from chat.rag.chunking import ChunkedText

COLLECTION_NAME = get_settings().QDRANT_COLLECTION_NAME
_VECTOR_SIZE = 512  # voyage-3-lite embedding dimension (research.md #1)


class ChunkPayload(BaseModel):
    """Typed shape of a chunk's payload (Qdrant's SDK types it as `dict[str, Any]`)."""

    faq_entry_id: int
    chunk_index: int
    chunk_text: str


@dataclass(frozen=True)
class RetrievedChunk:
    """A chunk returned by `search`, with its similarity score."""

    faq_entry_id: int
    chunk_index: int
    chunk_text: str
    score: float


def create_client(settings: Settings) -> AsyncQdrantClient:
    """Build the Qdrant client for the given settings."""
    return AsyncQdrantClient(url=settings.QDRANT_URL)


async def ensure_collection(client: AsyncQdrantClient) -> None:
    """Create the configured chunks collection if missing. Idempotent."""
    if await client.collection_exists(COLLECTION_NAME):
        return
    await client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=_VECTOR_SIZE, distance=Distance.COSINE),
    )


async def upsert_chunks(
    client: AsyncQdrantClient,
    faq_entry_id: int,
    chunks: list[ChunkedText],
    vectors: list[list[float]],
) -> None:
    """Upsert `chunks` (paired with their embedded `vectors`) for `faq_entry_id`."""
    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload=ChunkPayload(
                faq_entry_id=faq_entry_id,
                chunk_index=chunk.chunk_index,
                chunk_text=chunk.chunk_text,
            ).model_dump(),
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    if not points:
        return
    await client.upsert(collection_name=COLLECTION_NAME, points=points)


async def search(
    client: AsyncQdrantClient, query_vector: list[float], limit: int = 5
) -> list[RetrievedChunk]:
    """Return the nearest chunks to `query_vector`, with their similarity scores."""
    response = await client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        limit=limit,
        with_payload=True,
    )
    results = []
    for point in response.points:
        if point.payload is None:
            continue
        payload = ChunkPayload.model_validate(point.payload)
        results.append(
            RetrievedChunk(
                faq_entry_id=payload.faq_entry_id,
                chunk_index=payload.chunk_index,
                chunk_text=payload.chunk_text,
                score=point.score,
            )
        )
    return results


async def delete_by_entry(client: AsyncQdrantClient, faq_entry_id: int) -> None:
    """Delete all chunks for `faq_entry_id`."""
    await client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[
                FieldCondition(key="faq_entry_id", match=MatchValue(value=faq_entry_id))
            ]
        ),
    )
