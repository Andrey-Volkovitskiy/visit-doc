"""Qdrant repository: chunk upsert/search/delete-by-entry + collection bootstrap."""

import uuid
from dataclasses import dataclass

from pydantic import BaseModel
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from chat.core.config import Settings, get_settings
from chat.rag.chunking import ChunkedText

COLLECTION_NAME = get_settings().QDRANT_COLLECTION_NAME
_VECTOR_SIZE = 512  # voyage-3-lite embedding dimension (research.md #1)


class ChunkPayload(BaseModel):
    """Typed shape of a chunk's payload (Qdrant's SDK types it as `dict[str, Any]`).

    A point is written once and never edited: a save publishes a new `revision` rather
    than replacing the chunks of an existing one, so several revisions of one entry can
    sit in the collection at the same time and exactly one of them is live.
    """

    session_id: str
    faq_entry_id: int
    revision: str
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


# The three payload fields every filter in this module addresses. Indexed so a
# retrieval filter, a per-entry sweep and a session-wide delete are all index-backed
# rather than scans over the collection.
_INDEXED_PAYLOAD_FIELDS = {
    "revision": PayloadSchemaType.KEYWORD,
    "faq_entry_id": PayloadSchemaType.INTEGER,
    "session_id": PayloadSchemaType.KEYWORD,
}


async def ensure_collection(qdrant_client: AsyncQdrantClient) -> None:
    """Create the configured chunks collection and its payload indexes. Idempotent."""
    if not await qdrant_client.collection_exists(COLLECTION_NAME):
        await qdrant_client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=_VECTOR_SIZE, distance=Distance.COSINE),
        )
    for field_name, schema in _INDEXED_PAYLOAD_FIELDS.items():
        await qdrant_client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name=field_name,
            field_schema=schema,
            wait=True,
        )


async def upsert_chunks(
    qdrant_client: AsyncQdrantClient,
    session_id: str,
    faq_entry_id: int,
    revision: str,
    chunks: list[ChunkedText],
    vectors: list[list[float]],
) -> None:
    """Write `chunks` (paired with their embedded `vectors`) as one new `revision`.

    Additive: this never deletes, overwrites, or modifies the chunks of any existing
    revision, so the revision currently being answered from stays intact until a later
    commit names a different one live.
    """
    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload=ChunkPayload(
                session_id=session_id,
                faq_entry_id=faq_entry_id,
                revision=revision,
                chunk_index=chunk.chunk_index,
                chunk_text=chunk.chunk_text,
            ).model_dump(),
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    if not points:
        return
    await qdrant_client.upsert(collection_name=COLLECTION_NAME, points=points)


async def search(
    qdrant_client: AsyncQdrantClient,
    query_vector: list[float],
    live_revisions: list[str],
    limit: int = 5,
) -> list[RetrievedChunk]:
    """Return the nearest chunks to `query_vector` among `live_revisions`.

    Args:
        live_revisions: Every revision the caller's own session currently publishes.
            Applied as a filter term on the search itself, never as a check on its
            results - so a chunk of a superseded revision, or of another session's
            entry, is not retrieved rather than retrieved and discarded.

    An empty `live_revisions` matches nothing and returns an empty list.
    """
    if not live_revisions:
        return []
    response = await qdrant_client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        query_filter=Filter(
            must=[FieldCondition(key="revision", match=MatchAny(any=live_revisions))]
        ),
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


async def delete_by_entry(qdrant_client: AsyncQdrantClient, faq_entry_id: int) -> None:
    """Delete all chunks for `faq_entry_id`."""
    await qdrant_client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[
                FieldCondition(key="faq_entry_id", match=MatchValue(value=faq_entry_id))
            ]
        ),
    )


async def sweep_chunks(
    qdrant_client: AsyncQdrantClient, faq_entry_id: int, live_revision: str
) -> None:
    """Delete `faq_entry_id`'s chunks that are not part of `live_revision`.

    One predicate covers both kinds of leftover, because "not the live one" says
    nothing about how a revision came to exist: a revision superseded by a later save,
    and one written by a save that never published, are equally unreachable.

    Scoped to this entry and never widened to the session: a session-wide predicate
    would delete a concurrent save's chunks in the window between their write and the
    commit that publishes them.
    """
    await qdrant_client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[
                FieldCondition(key="faq_entry_id", match=MatchValue(value=faq_entry_id))
            ],
            must_not=[
                FieldCondition(key="revision", match=MatchValue(value=live_revision))
            ],
        ),
    )


async def delete_by_session(qdrant_client: AsyncQdrantClient, session_id: str) -> None:
    """Delete every chunk belonging to `session_id`.

    Runs after that session's rows are already gone, which is why it addresses points
    by their session rather than by the entries that used to name them.
    """
    await qdrant_client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[FieldCondition(key="session_id", match=MatchValue(value=session_id))]
        ),
    )
