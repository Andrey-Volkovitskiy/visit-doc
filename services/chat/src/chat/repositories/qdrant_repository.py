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
# How many of one entry's points a sweep reads to learn which revisions it holds.
# An entry past this is swept partially, which costs storage and nothing else.
_SWEEP_PAGE_LIMIT = 1000


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


async def _create_payload_indexes(
    qdrant_client: AsyncQdrantClient, fields: dict[str, PayloadSchemaType]
) -> None:
    """Index each of `fields`, waiting for Qdrant to acknowledge each one.

    Waited on rather than fired and forgotten: the caller is startup, and a filter
    issued against a half-built index is a filter answering from part of the corpus.
    """
    for field_name, schema in fields.items():
        await qdrant_client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name=field_name,
            field_schema=schema,
            wait=True,
        )


async def ensure_collection(qdrant_client: AsyncQdrantClient) -> None:
    """Create the configured chunks collection and its payload indexes. Idempotent.

    The indexes are reconciled against the schema the collection already reports, so a
    collection created before a field joined `_INDEXED_PAYLOAD_FIELDS` gains that index
    on the next start. Only what is genuinely missing is written: this runs on every
    process start, and an index creation is a blocking write, while reading the schema
    back is one cheap read.
    """
    if not await qdrant_client.collection_exists(COLLECTION_NAME):
        await qdrant_client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=_VECTOR_SIZE, distance=Distance.COSINE),
        )
        await _create_payload_indexes(qdrant_client, _INDEXED_PAYLOAD_FIELDS)
        return
    indexed = (await qdrant_client.get_collection(COLLECTION_NAME)).payload_schema
    missing: dict[str, PayloadSchemaType] = {}
    for field_name, schema in _INDEXED_PAYLOAD_FIELDS.items():
        existing = indexed.get(field_name)
        if existing is None or existing.data_type != schema:
            missing[field_name] = schema
    await _create_payload_indexes(qdrant_client, missing)


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
    """Delete `faq_entry_id`'s chunks from revisions older than `live_revision`.

    Args:
        live_revision: A revision of this entry that was live when the caller published
            it - not necessarily one that still is. That is the point: revisions are
            ULIDs, and an entry's published revisions strictly increase, because a save
            may only publish over the revision it read. So every revision older than
            one that was ever live is dead for good, and one that is newer either is
            live now or is a save still in flight.

    The revisions to delete are read back and named, rather than addressed as
    "everything but the live one". A predicate that deletes by what it does *not* name
    also deletes whatever it has not heard of: a save that published in the window
    between this caller's own publish and this delete would lose the chunks its row
    already vouches for. Naming them cannot reach a revision this sweep never saw.

    One predicate still covers both kinds of leftover, because being older than a live
    revision says nothing about how a revision came to exist: a revision superseded by
    a later save, and one written by a save that never published, are equally
    unreachable.

    Scoped to this entry and never widened to the session: a session-wide predicate
    would delete a concurrent save's chunks in the window between their write and the
    commit that publishes them.
    """
    belongs_to_entry = FieldCondition(
        key="faq_entry_id", match=MatchValue(value=faq_entry_id)
    )
    records, _ = await qdrant_client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(must=[belongs_to_entry]),
        limit=_SWEEP_PAGE_LIMIT,
        with_payload=["revision"],
        with_vectors=False,
    )
    superseded: set[str] = set()
    for record in records:
        revision = (record.payload or {}).get("revision")
        if isinstance(revision, str) and revision < live_revision:
            superseded.add(revision)
    if not superseded:
        return
    await qdrant_client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[
                belongs_to_entry,
                FieldCondition(key="revision", match=MatchAny(any=sorted(superseded))),
            ]
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
