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
    SPARSE_VECTOR,
    VECTOR_SIZE,
    ChunkPayload,
    CollectionShapeMismatchError,
    SearchBranches,
    delete_by_entry,
    ensure_collection,
    search,
    sweep_chunks,
    upsert_chunks,
)
from httpx import AsyncClient, Headers
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    Modifier,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)
from ulid import ULID

_TEST_ENTRY_ID = 999999
_TEST_VECTOR = [0.1] * VECTOR_SIZE


def _client() -> AsyncQdrantClient:
    return AsyncQdrantClient(url=Settings().QDRANT_URL)


def _texts(found: SearchBranches) -> tuple[list[str], list[str]]:
    """Return the dense branch's texts, then the lexical branch's."""
    return (
        [c.chunk_text for c in found.dense],
        [c.chunk_text for c in found.lexical],
    )


async def test_ensure_collection_is_idempotent() -> None:
    client = _client()

    await ensure_collection(client)
    await ensure_collection(client)

    await client.close()


async def test_upsert_search_delete_round_trip() -> None:
    client = _client()
    await ensure_collection(client)
    session_id, revision = str(ULID()), str(ULID())
    chunks = [ChunkedText(chunk_index=0, chunk_text="visiting hours are 8am to 5pm")]

    await upsert_chunks(
        client, session_id, _TEST_ENTRY_ID, revision, chunks, [_TEST_VECTOR]
    )
    found = await search(
        client, session_id, _TEST_VECTOR, "visiting hours", [revision], limit=10
    )
    assert _texts(found) == ([chunks[0].chunk_text], [chunks[0].chunk_text])

    await delete_by_entry(client, session_id, _TEST_ENTRY_ID)
    found_after_delete = await search(
        client, session_id, _TEST_VECTOR, "visiting hours", [revision], limit=10
    )
    assert _texts(found_after_delete) == ([], [])

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

    # The query names the superseded text word for word, so the lexical branch would
    # rank it first if its own filter did not exclude it.
    found = await search(
        client, session_id, _TEST_VECTOR, "superseded text", [live], limit=10
    )

    assert found.dense and [c.chunk_text for c in found.dense] == ["live text"]
    assert "superseded text" not in _texts(found)[1]

    await delete_by_entry(client, session_id, _TEST_ENTRY_ID)
    await client.close()


async def test_search_cannot_reach_another_sessions_chunks() -> None:
    # The deliberate cross-session case: the caller supplies the *owner's* own live
    # revision, the one value that would match if the revision term were the only one.
    # Nothing here rests on a revision being unguessable - it is handed over - so what
    # is being pinned is the session predicate itself rather than ULID uniqueness.
    client = _client()
    await ensure_collection(client)
    owner, intruder, theirs = str(ULID()), str(ULID()), str(ULID())
    text = "parking validation desk"
    await upsert_chunks(
        client,
        owner,
        _TEST_ENTRY_ID,
        theirs,
        [ChunkedText(chunk_index=0, chunk_text=text)],
        [_TEST_VECTOR],
    )

    # Both branches, and a query matching the chunk exactly: a lexical branch without
    # the session term would find it on its words alone. Not a stopword ("theirs"):
    # BM25 drops those, and a branch that found nothing would pass for the wrong reason.
    intruding = await search(client, intruder, _TEST_VECTOR, text, [theirs])
    assert _texts(intruding) == ([], [])
    still_theirs = await search(client, owner, _TEST_VECTOR, text, [theirs])
    assert _texts(still_theirs) == ([text], [text])

    await delete_by_entry(client, owner, _TEST_ENTRY_ID)
    await client.close()


async def test_delete_by_entry_cannot_reach_another_sessions_chunks() -> None:
    # The same case for the delete: the intruder names an entry id that really exists,
    # which is the only id that could match if the entry term stood alone. An entry id
    # is unique across sessions, so nothing but the session predicate keeps this from
    # emptying somebody else's entry.
    client = _client()
    await ensure_collection(client)
    owner, intruder, revision = str(ULID()), str(ULID()), str(ULID())
    await upsert_chunks(
        client,
        owner,
        _TEST_ENTRY_ID,
        revision,
        [ChunkedText(chunk_index=0, chunk_text="theirs")],
        [_TEST_VECTOR],
    )

    await delete_by_entry(client, intruder, _TEST_ENTRY_ID)

    survived = await search(client, owner, _TEST_VECTOR, "theirs", [revision])
    assert _texts(survived)[0] == ["theirs"]

    await delete_by_entry(client, owner, _TEST_ENTRY_ID)
    await client.close()


async def test_the_sweep_cannot_reach_another_sessions_chunks() -> None:
    # And for the sweep, whose delete would otherwise be addressed by an entry id and a
    # revision it read back itself. The intruder names the owner's entry and passes a
    # revision newer than the owner's, so every point the owner holds is older than the
    # one it was given - the whole set the sweep exists to remove.
    client = _client()
    await ensure_collection(client)
    owner, intruder = str(ULID()), str(ULID())
    # Sorted rather than minted in order: two ULIDs from the same millisecond differ
    # only in their random tail, and this test needs `newer` to be the greater one.
    theirs, newer = sorted((str(ULID()), str(ULID())))
    await upsert_chunks(
        client,
        owner,
        _TEST_ENTRY_ID,
        theirs,
        [ChunkedText(chunk_index=0, chunk_text="theirs")],
        [_TEST_VECTOR],
    )

    await sweep_chunks(client, intruder, _TEST_ENTRY_ID, newer)

    survived = await search(client, owner, _TEST_VECTOR, "theirs", [theirs])
    assert _texts(survived)[0] == ["theirs"]

    await delete_by_entry(client, owner, _TEST_ENTRY_ID)
    await client.close()


async def test_search_returns_more_than_five_when_asked_for_more() -> None:
    # The observation pool: retrieval fetches wider than the similarity cap so the cap
    # has something to discard and the discarded candidates are on the record. A search
    # that silently stopped at its old default would make the cap untunable.
    client = _client()
    await ensure_collection(client)
    session_id, revision = str(ULID()), str(ULID())
    chunks = [ChunkedText(chunk_index=i, chunk_text=f"chunk {i}") for i in range(12)]
    await upsert_chunks(
        client, session_id, _TEST_ENTRY_ID, revision, chunks, [_TEST_VECTOR] * 12
    )

    found = await search(
        client, session_id, _TEST_VECTOR, "chunk", [revision], limit=12
    )

    assert len(found.dense) == 12
    assert len(found.lexical) == 12

    await delete_by_entry(client, session_id, _TEST_ENTRY_ID)
    await client.close()


async def test_search_returns_candidates_in_descending_score_order() -> None:
    client = _client()
    await ensure_collection(client)
    session_id, revision = str(ULID()), str(ULID())
    # Three vectors at increasing angles from the query, so their cosine scores differ
    # and their order is a fact about the search rather than about insertion order.
    near = [1.0] + [0.0] * (VECTOR_SIZE - 1)
    mid = [0.6, 0.8] + [0.0] * (VECTOR_SIZE - 2)
    far = [0.0, 1.0] + [0.0] * (VECTOR_SIZE - 2)
    await upsert_chunks(
        client,
        session_id,
        _TEST_ENTRY_ID,
        revision,
        [
            ChunkedText(chunk_index=i, chunk_text=t)
            for i, t in enumerate(("far", "near", "mid"))
        ],
        [far, near, mid],
    )

    found = await search(client, session_id, near, "unrelated", [revision], limit=10)

    assert [c.chunk_text for c in found.dense] == ["near", "mid", "far"]
    scores = [c.score for c in found.dense]
    assert scores == sorted(scores, reverse=True)

    await delete_by_entry(client, session_id, _TEST_ENTRY_ID)
    await client.close()


async def test_search_with_no_live_revisions_returns_nothing() -> None:
    client = _client()
    await ensure_collection(client)
    session_id = str(ULID())
    await upsert_chunks(
        client,
        session_id,
        _TEST_ENTRY_ID,
        str(ULID()),
        [ChunkedText(chunk_index=0, chunk_text="anything")],
        [_TEST_VECTOR],
    )

    found = await search(client, session_id, _TEST_VECTOR, "anything", [], limit=10)

    assert found == SearchBranches(dense=[], lexical=[])

    await delete_by_entry(client, session_id, _TEST_ENTRY_ID)
    await client.close()


async def test_the_lexical_branch_ranks_an_exact_code_first_whatever_the_vectors() -> (
    None
):
    # Every chunk carries the same dense vector, so dense search cannot tell them apart:
    # only the chunk's words can put the form code first.
    client = _client()
    await ensure_collection(client)
    session_id, revision = str(ULID()), str(ULID())
    texts = (
        "Bring photo identification to reception.",
        "Form PR-4 releases your records to another provider.",
        "Parking is free for the first hour.",
    )
    await upsert_chunks(
        client,
        session_id,
        _TEST_ENTRY_ID,
        revision,
        [ChunkedText(chunk_index=i, chunk_text=t) for i, t in enumerate(texts)],
        [_TEST_VECTOR] * len(texts),
    )

    found = await search(
        client, session_id, _TEST_VECTOR, "What is the PR-4?", [revision], limit=10
    )

    assert [c.chunk_index for c in found.lexical] == [1]
    assert len(found.dense) == len(texts)

    await delete_by_entry(client, session_id, _TEST_ENTRY_ID)
    await client.close()


async def test_the_lexical_branch_returns_nothing_for_a_query_sharing_no_term() -> None:
    # BM25 scores only shared terms. A chunk with none in common is not a weak match to
    # be ranked last; it is no match, and the branch says so by leaving it out.
    client = _client()
    await ensure_collection(client)
    session_id, revision = str(ULID()), str(ULID())
    await upsert_chunks(
        client,
        session_id,
        _TEST_ENTRY_ID,
        revision,
        [ChunkedText(chunk_index=0, chunk_text="Parking is free for the first hour.")],
        [_TEST_VECTOR],
    )

    found = await search(
        client, session_id, _TEST_VECTOR, "vaccination schedule", [revision], limit=10
    )

    assert _texts(found) == (["Parking is free for the first hour."], [])

    await delete_by_entry(client, session_id, _TEST_ENTRY_ID)
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

    await sweep_chunks(client, session_id, _TEST_ENTRY_ID, given)

    found = await search(
        client, session_id, _TEST_VECTOR, "anything", [older, given, newer], limit=10
    )
    assert {chunk.chunk_text for chunk in found.dense} == {given, newer}

    await delete_by_entry(client, session_id, _TEST_ENTRY_ID)
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


async def test_ensure_collection_refuses_a_collection_of_the_wrong_vector_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A collection's vector size is fixed at creation and `ensure_collection` skips
    # creating one that already exists - so a change of embedding model would otherwise
    # start cleanly and then fail every upsert and every search, one turn at a time.
    client = _client()
    async with _own_collection(monkeypatch) as name:
        await client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(
                size=VECTOR_SIZE // 2, distance=Distance.COSINE
            ),
        )

        with pytest.raises(CollectionShapeMismatchError) as mismatch:
            await ensure_collection(client)

    assert str(VECTOR_SIZE // 2) in str(mismatch.value)
    assert str(VECTOR_SIZE) in str(mismatch.value)

    await client.close()


_RIGHT_SPARSE_VECTORS = {SPARSE_VECTOR: SparseVectorParams(modifier=Modifier.IDF)}


@pytest.mark.parametrize(
    "sparse_vectors",
    [
        None,
        {SPARSE_VECTOR: SparseVectorParams()},
        {"lexical": SparseVectorParams(modifier=Modifier.IDF)},
    ],
    ids=["no-sparse-vector", "sparse-vector-without-idf", "sparse-vector-misnamed"],
)
async def test_ensure_collection_refuses_a_collection_without_the_bm25_vector(
    monkeypatch: pytest.MonkeyPatch,
    sparse_vectors: dict[str, SparseVectorParams] | None,
) -> None:
    # A chunk with no sparse vector is not an error to Qdrant: it is simply absent from
    # the lexical search, which reads as "no lexical match". So a collection created
    # before this build has to stop the start rather than be searched.
    client = _client()
    async with _own_collection(monkeypatch) as name:
        await client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            sparse_vectors_config=sparse_vectors,
        )

        with pytest.raises(CollectionShapeMismatchError) as mismatch:
            await ensure_collection(client)

    assert SPARSE_VECTOR in str(mismatch.value)

    await client.close()


async def test_ensure_collection_creates_both_vectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    async with _own_collection(monkeypatch) as name:
        await ensure_collection(client)

        params = (await client.get_collection(name)).config.params

    assert isinstance(params.vectors, VectorParams)
    assert params.vectors.size == VECTOR_SIZE
    assert params.sparse_vectors is not None
    assert params.sparse_vectors[SPARSE_VECTOR].modifier == Modifier.IDF

    await client.close()


async def test_upsert_writes_a_bm25_vector_computed_from_the_chunk_text() -> None:
    # Qdrant derives the sparse vector from the text it is handed, so a point whose
    # text has more distinct terms carries more of them - which a vector built from
    # anything but this chunk's own text would not.
    client = _client()
    await ensure_collection(client)
    session_id, revision = str(ULID()), str(ULID())
    chunks = [
        ChunkedText(chunk_index=0, chunk_text="form PR-4"),
        ChunkedText(chunk_index=1, chunk_text="spirometry 94010 costs $85 self-pay"),
    ]
    await upsert_chunks(
        client, session_id, _TEST_ENTRY_ID, revision, chunks, [_TEST_VECTOR] * 2
    )

    records, _ = await client.scroll(
        qdrant_repository.COLLECTION_NAME,
        scroll_filter=Filter(
            must=[FieldCondition(key="session_id", match=MatchValue(value=session_id))]
        ),
        with_payload=["chunk_index"],
        with_vectors=True,
    )
    terms: dict[int, int] = {}
    for record in records:
        assert isinstance(record.vector, dict)
        sparse = record.vector[SPARSE_VECTOR]
        assert isinstance(sparse, SparseVector)
        terms[(record.payload or {})["chunk_index"]] = len(sparse.indices)
    assert 0 < terms[0] < terms[1]

    await delete_by_entry(client, session_id, _TEST_ENTRY_ID)
    await client.close()


async def test_ensure_collection_accepts_a_collection_of_the_right_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    async with _own_collection(monkeypatch) as name:
        await client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            sparse_vectors_config=_RIGHT_SPARSE_VECTORS,
        )

        await ensure_collection(client)

        assert (await client.get_collection(name)).payload_schema.keys() == (
            qdrant_repository._INDEXED_PAYLOAD_FIELDS.keys()
        )

    await client.close()
