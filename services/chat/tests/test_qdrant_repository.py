from chat.core.config import Settings
from chat.rag.chunking import ChunkedText
from chat.repositories.qdrant_repository import (
    delete_by_entry,
    ensure_collection,
    search,
    upsert_chunks,
)
from qdrant_client import AsyncQdrantClient

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
    chunks = [ChunkedText(chunk_index=0, chunk_text="visiting hours are 8am to 5pm")]

    await upsert_chunks(client, _TEST_ENTRY_ID, chunks, [_TEST_VECTOR])
    found = await search(client, _TEST_VECTOR, limit=10)
    matches = [r for r in found if r.faq_entry_id == _TEST_ENTRY_ID]
    assert matches and matches[0].chunk_text == chunks[0].chunk_text

    await delete_by_entry(client, _TEST_ENTRY_ID)
    found_after_delete = await search(client, _TEST_VECTOR, limit=10)
    assert all(r.faq_entry_id != _TEST_ENTRY_ID for r in found_after_delete)

    await client.close()
