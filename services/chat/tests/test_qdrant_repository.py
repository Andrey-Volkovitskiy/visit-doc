from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Any
from unittest.mock import AsyncMock

import pytest
from chat.core.config import Settings
from chat.rag.chunking import ChunkedText
from chat.repositories import qdrant_repository
from chat.repositories.qdrant_repository import (
    ChunkPayload,
    delete_by_entry,
    ensure_collection,
    search,
    sweep_chunks,
    upsert_chunks,
)
from httpx import AsyncClient, Headers
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
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


async def test_the_sweep_removes_older_revisions_and_spares_a_newer_one() -> None:
    # A sweep can reach the store after a later save has published, so it is given a
    # revision that was live rather than one that still is. Deleting by "older than
    # the one I was given" is what makes that harmless: an entry's published revisions
    # strictly increase, so nothing newer can be dead, and nothing older can be live.
    client = _client()
    await ensure_collection(client)
    session_id = str(ULID())
    older, given, newer = str(ULID()), str(ULID()), str(ULID())
    for revision in (older, given, newer):
        await upsert_chunks(
            client,
            session_id,
            _TEST_ENTRY_ID,
            revision,
            [ChunkedText(chunk_index=0, chunk_text=revision)],
            [_TEST_VECTOR],
        )

    await sweep_chunks(client, _TEST_ENTRY_ID, given)

    found = await search(client, _TEST_VECTOR, [older, given, newer], limit=10)
    assert {chunk.chunk_text for chunk in found} == {given, newer}

    await delete_by_entry(client, _TEST_ENTRY_ID)
    await client.close()


# --- `ensure_collection` reconciles indexes rather than rewriting them --------------


async def _drop_collection(name: str) -> None:
    """Delete `name` over Qdrant's REST API, and check that it is really gone.

    Deliberately does not go through an `AsyncQdrantClient`: see `_own_collection`.
    Unconditional, because Qdrant answers a delete of a collection that was never
    created `200 {"result": false}` rather than an error - so there is no state to
    read back first, and therefore nothing a test could make lie about it.
    """
    base_url = Settings().QDRANT_URL.rstrip("/")
    async with AsyncClient(base_url=base_url) as http_client:
        await http_client.delete(f"/collections/{name}")
        remaining = await http_client.get(f"/collections/{name}")
    assert remaining.status_code == HTTPStatus.NOT_FOUND, (
        f"scratch collection {name} outlived its test"
    )


@asynccontextmanager
async def _own_collection(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[str, None]:
    """Point the repository at a collection of this test's own, and drop it afterwards.

    What `ensure_collection` does depends on what the collection already holds, so a
    test of it cannot use the suite's shared one - conftest created and indexed that
    before the first test ran.

    The drop reaches Qdrant directly rather than through the client the test is
    holding, and that is the point rather than an implementation detail. A test in here
    patches that client's own methods to lie - that is how the create race is
    reproduced - and `monkeypatch` only undoes a patch at *test teardown*, which is
    after this `finally` has already run. A cleanup routed through the client therefore
    asks the stub whether to clean up, is told the collection does not exist, and leaks
    it into the local Qdrant on every run (measured: two per run of this file, on top
    of everything every earlier run had already left there). Capturing the bound
    methods on the way in would fix today's two tests and leave the trap armed for the
    next one to patch before entering; this helper is handed no client at all, so there
    is no ordering left to get wrong.
    """
    name = f"faq_chunks_ensure_{ULID()}"
    monkeypatch.setattr(qdrant_repository, "COLLECTION_NAME", name)
    try:
        yield name
    finally:
        await _drop_collection(name)


def _recorded_index_writes(
    monkeypatch: pytest.MonkeyPatch, client: AsyncQdrantClient
) -> list[str]:
    """Return a list that collects the field name of every index `client` is asked to
    write from now on. Each call is still passed through to Qdrant.
    """
    written: list[str] = []
    create_payload_index = client.create_payload_index

    async def record(**kwargs: Any) -> Any:
        written.append(kwargs["field_name"])
        return await create_payload_index(**kwargs)

    monkeypatch.setattr(client, "create_payload_index", record)
    return written


async def test_ensure_collection_indexes_every_field_its_filters_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    async with _own_collection(monkeypatch) as name:
        await ensure_collection(client)

        schema = (await client.get_collection(name)).payload_schema
        assert {field: info.data_type for field, info in schema.items()} == (
            qdrant_repository._INDEXED_PAYLOAD_FIELDS
        )

    await client.close()


async def test_ensure_collection_writes_no_index_when_the_collection_is_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The restart case, which is every start after the first: creating an index is a
    # blocking write, and there is nothing here for it to do.
    client = _client()
    async with _own_collection(monkeypatch):
        await ensure_collection(client)
        written = _recorded_index_writes(monkeypatch, client)

        await ensure_collection(client)

        assert written == []

    await client.close()


async def test_ensure_collection_adds_an_index_the_collection_does_not_have(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A collection that predates a field joining the indexed set: the next start has to
    # write that one index, and only that one.
    client = _client()
    async with _own_collection(monkeypatch) as name:
        await ensure_collection(client)
        await client.delete_payload_index(name, field_name="session_id", wait=True)
        written = _recorded_index_writes(monkeypatch, client)

        await ensure_collection(client)

        assert written == ["session_id"]
        assert "session_id" in (await client.get_collection(name)).payload_schema

    await client.close()


async def _absent(*_args: Any, **_kwargs: Any) -> bool:
    """Report the collection as missing, whatever it is and whether or not it is."""
    return False


def _unexpected_response(status_code: int) -> UnexpectedResponse:
    return UnexpectedResponse(
        status_code=status_code,
        reason_phrase="",
        content=b'{"status":{"error":"..."}}',
        headers=Headers(),
    )


async def test_ensure_collection_survives_losing_the_create_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two processes starting together both see the collection absent and both create
    # it; Qdrant answers the loser 409. Reproduced by reporting a collection that does
    # exist as missing, which is exactly what the loser saw a moment earlier.
    client = _client()
    async with _own_collection(monkeypatch) as name:
        await ensure_collection(client)
        monkeypatch.setattr(client, "collection_exists", _absent)

        await ensure_collection(client)

        schema = (await client.get_collection(name)).payload_schema
        assert {field: info.data_type for field, info in schema.items()} == (
            qdrant_repository._INDEXED_PAYLOAD_FIELDS
        )

    await client.close()


async def test_ensure_collection_still_fails_on_a_status_that_is_not_a_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The 409 is tolerated because it says the collection exists. Nothing else does, so
    # nothing else may start a process that would then answer from a collection it has
    # no evidence for.
    client = _client()
    async with _own_collection(monkeypatch):
        monkeypatch.setattr(
            client,
            "create_collection",
            AsyncMock(side_effect=_unexpected_response(500)),
        )

        with pytest.raises(UnexpectedResponse):
            await ensure_collection(client)

    await client.close()


async def test_the_loser_of_the_create_race_finishes_the_winners_indexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The winner had created the collection but not yet all of its indexes. The loser
    # must not take "I did not create this" for "this is already indexed" - a filter
    # against a missing index is answered by a scan, or by an error.
    client = _client()
    async with _own_collection(monkeypatch) as name:
        await ensure_collection(client)
        await client.delete_payload_index(name, field_name="revision", wait=True)
        monkeypatch.setattr(client, "collection_exists", _absent)
        written = _recorded_index_writes(monkeypatch, client)

        await ensure_collection(client)

        assert written == ["revision"]

    await client.close()
