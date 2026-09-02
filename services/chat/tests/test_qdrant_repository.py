from chat.core.config import Settings
from chat.rag.chunking import ChunkedText
from chat.repositories.qdrant_repository import (
    ChunkPayload,
    delete_by_entry,
    ensure_collection,
    search,
    upsert_chunks,
)
from qdrant_client import AsyncQdrantClient
from ulid import ULID

_TEST_ENTRY_ID = 999999
_TEST_VECTOR = [0.1] * 512


def _client() -> AsyncQdrantClient:
    return AsyncQdrantClient(url=Settings().QDRANT_URL)


async def test_ensure_collection_is_idempotent() -> None:
    client = _client()

    await ensure_collection(client)
    await ensure_collection(client)

    await client.close()


async def test_upsert_search_delete_round_trip() -> None:
    client = _client()
    await ensure_collection(client)
    revision = str(ULID())
    chunks = [ChunkedText(chunk_index=0, chunk_text="visiting hours are 8am to 5pm")]

    await upsert_chunks(
        client, str(ULID()), _TEST_ENTRY_ID, revision, chunks, [_TEST_VECTOR]
    )
    found = await search(client, _TEST_VECTOR, [revision], limit=10)
    matches = [r for r in found if r.faq_entry_id == _TEST_ENTRY_ID]
    assert matches and matches[0].chunk_text == chunks[0].chunk_text

    await delete_by_entry(client, _TEST_ENTRY_ID)
    found_after_delete = await search(client, _TEST_VECTOR, [revision], limit=10)
    assert all(r.faq_entry_id != _TEST_ENTRY_ID for r in found_after_delete)

    await client.close()


# --- 007: chunks carry their owner and revision, and search filters on them --------


async def test_chunk_payload_carries_its_session_entry_and_revision() -> None:
    # `revision` is what retrieval filters on; `faq_entry_id` is what the per-entry
    # sweep addresses; `session_id` is the only handle left once an entry's rows are
    # gone, which is what lets a session delete clear its own chunks.
    assert {"session_id", "faq_entry_id", "revision"} <= set(ChunkPayload.model_fields)


async def test_search_returns_only_chunks_of_the_revisions_it_was_given() -> None:
    client = _client()
    await ensure_collection(client)
    session_id, live, superseded = str(ULID()), str(ULID()), str(ULID())
    await upsert_chunks(
        client,
        session_id,
        _TEST_ENTRY_ID,
        live,
        [ChunkedText(chunk_index=0, chunk_text="live text")],
        [_TEST_VECTOR],
    )
    await upsert_chunks(
        client,
        session_id,
        _TEST_ENTRY_ID,
        superseded,
        [ChunkedText(chunk_index=0, chunk_text="superseded text")],
        [_TEST_VECTOR],
    )

    found = await search(client, _TEST_VECTOR, [live], limit=10)

    assert [chunk.chunk_text for chunk in found] == ["live text"]

    await delete_by_entry(client, _TEST_ENTRY_ID)
    await client.close()


async def test_search_cannot_reach_another_sessions_chunks() -> None:
    # A revision id is minted by one session's save and never shared, so filtering to
    # this session's live revisions scopes both at once - the session predicate and the
    # live-revision predicate are the same term.
    client = _client()
    await ensure_collection(client)
    theirs = str(ULID())
    await upsert_chunks(
        client,
        str(ULID()),
        _TEST_ENTRY_ID,
        theirs,
        [ChunkedText(chunk_index=0, chunk_text="theirs")],
        [_TEST_VECTOR],
    )

    assert await search(client, _TEST_VECTOR, [str(ULID())], limit=10) == []

    await delete_by_entry(client, _TEST_ENTRY_ID)
    await client.close()


async def test_search_with_no_live_revisions_returns_nothing() -> None:
    client = _client()
    await ensure_collection(client)
    await upsert_chunks(
        client,
        str(ULID()),
        _TEST_ENTRY_ID,
        str(ULID()),
        [ChunkedText(chunk_index=0, chunk_text="anything")],
        [_TEST_VECTOR],
    )

    assert await search(client, _TEST_VECTOR, [], limit=10) == []

    await delete_by_entry(client, _TEST_ENTRY_ID)
    await client.close()
