"""Qdrant repository: chunk upsert/search/delete-by-entry + collection bootstrap."""

import uuid
from dataclasses import dataclass
from http import HTTPStatus

from pydantic import BaseModel
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
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
from chat.core.logging import get_logger
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


async def _create_collection(qdrant_client: AsyncQdrantClient) -> None:
    """Create the chunks collection, tolerating another process having just made it.

    Two processes starting together both find the collection absent and both ask for
    it; the loser is answered `409 Conflict`, which says the collection exists - the
    state this was asking for. That one status is therefore not a failure. Every other
    status, and every other error, still fails the start: a process whose collection is
    not there answers from nothing, which is worse than not starting.
    """
    try:
        await qdrant_client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=_VECTOR_SIZE, distance=Distance.COSINE),
        )
    except UnexpectedResponse as exc:
        if exc.status_code != HTTPStatus.CONFLICT:
            raise
        get_logger().info(
            "qdrant.collection_created_concurrently", collection=COLLECTION_NAME
        )


async def ensure_collection(qdrant_client: AsyncQdrantClient) -> None:
    """Create the configured chunks collection and its payload indexes. Idempotent.

    The indexes are reconciled against the schema the collection already reports, so a
    collection created before a field joined `_INDEXED_PAYLOAD_FIELDS` gains that index
    on the next start. Only what is genuinely missing is written: this runs on every
    process start, and an index creation is a blocking write, while reading the schema
    back is one cheap read.

    The reconciliation runs on the just-created collection too, rather than the create
    branch writing every index and returning. That is what makes concurrent starts safe
    all the way through: the process that lost the create race reads back whatever the
    winner has managed to build so far and finishes the job, instead of assuming the
    collection it did not make is already indexed.
    """
    if not await qdrant_client.collection_exists(COLLECTION_NAME):
        await _create_collection(qdrant_client)
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
    session_id: str,
    query_vector: list[float],
    live_revisions: list[str],
    limit: int = 5,
) -> list[RetrievedChunk]:
    """Return the nearest chunks to `query_vector` among `session_id`'s live revisions.

    Args:
        live_revisions: Every revision the caller's own session currently publishes.

    Both terms are filters on the search itself, never checks on its results, so a
    chunk of a superseded revision, or of another session's entry, is not retrieved
    rather than retrieved and discarded.

    The session term is not made redundant by the revision one. A revision is a ULID,
    and unique only means no collision - it says nothing about who may read the points
    carrying it, so a revision reaching this function from anywhere but the caller's own
    rows would otherwise resolve. The predicate is what makes it resolve to nothing.

    An empty `live_revisions` matches nothing and returns an empty list.
    """
    if not live_revisions:
        return []
    response = await qdrant_client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        query_filter=Filter(
            must=[
                FieldCondition(key="session_id", match=MatchValue(value=session_id)),
                FieldCondition(key="revision", match=MatchAny(any=live_revisions)),
            ]
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


async def delete_by_entry(
    qdrant_client: AsyncQdrantClient, session_id: str, faq_entry_id: int
) -> None:
    """Delete every chunk `session_id` owns for `faq_entry_id`.

    Scoped to the session as well as the entry, though an entry id comes from one shared
    sequence and so is already unique. Unique only means no collision: it says nothing
    about who may delete the points carrying it, and an id arriving from anywhere but
    the caller's own rows must address nothing rather than address somebody else's
    chunks.
    """
    await qdrant_client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[
                FieldCondition(key="session_id", match=MatchValue(value=session_id)),
                FieldCondition(
                    key="faq_entry_id", match=MatchValue(value=faq_entry_id)
                ),
            ]
        ),
    )


async def sweep_chunks(
    qdrant_client: AsyncQdrantClient,
    session_id: str,
    faq_entry_id: int,
    live_revision: str,
) -> None:
    """Delete `session_id`'s chunks of `faq_entry_id` older than `live_revision`.

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

    The session term narrows this and never widens it: the entry term still decides
    *which* chunks are candidates, because a predicate covering every entry of the
    session would delete a concurrent save's chunks in the window between their write
    and the commit that publishes them.
    """
    belongs_to_caller = [
        FieldCondition(key="session_id", match=MatchValue(value=session_id)),
        FieldCondition(key="faq_entry_id", match=MatchValue(value=faq_entry_id)),
    ]
    records, _ = await qdrant_client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(must=belongs_to_caller),
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
                *belongs_to_caller,
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
