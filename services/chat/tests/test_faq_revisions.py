"""The FAQ write path: additive revisions, one publishing commit, and no repair.

The rule the whole design serves is that a failed save costs *leaked storage*, never a
lost answer. Content is written once, in the commit that publishes it, so a failure
before that commit changed nothing anybody can observe and a failure *of* it is simply
the change not happening. There is nothing to roll back, which is why there is no
compensating write to half-succeed and swallow its own failure.

The sweep is the other half of that bargain: it is housekeeping, it may fail, and its
failure is neither reported nor logged - chunks that are not live are already
unreachable, so a sweep is not an operation that can fail.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from chat.api import faq as faq_api
from chat.core.config import Settings
from chat.db.session import engine, session_factory
from chat.domain.models import FaqEntry
from chat.main import app
from chat.rag import indexing
from chat.repositories import chat_repository, faq_repository
from chat.repositories.qdrant_repository import (
    COLLECTION_NAME,
    ChunkPayload,
    create_client,
    ensure_collection,
)
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient, Response
from structlog.testing import capture_logs

from .conftest import fake_anthropic_client, fake_embed_texts

_FIRST = "Visiting hours are 8am to 5pm on weekdays."
_SECOND = "Visiting hours are 9am to 6pm on weekdays."


async def _session_id() -> str:
    async with session_factory() as session:
        session_row = await chat_repository.create_session(session)
    await engine.dispose()
    return session_row.id


async def _entries(session_id: str) -> list[FaqEntry]:
    async with session_factory() as session:
        return await faq_repository.list_all(session, session_id)


async def _points() -> list[ChunkPayload]:
    """Return every chunk currently in the retrieval store."""
    qdrant_client = create_client(Settings())
    try:
        await ensure_collection(qdrant_client)
        records, _ = await qdrant_client.scroll(
            collection_name=COLLECTION_NAME, limit=1000, with_payload=True
        )
    finally:
        await qdrant_client.close()
    return [ChunkPayload.model_validate(r.payload) for r in records if r.payload]


@asynccontextmanager
async def _running_app(
    session_id: str | None, *, patches: list[Any] | None = None
) -> AsyncIterator[AsyncClient]:
    """Run the real app, with fake embeddings, and yield a client pointed at it.

    Every request that overlaps another must be issued through one of these, not one
    each. `app` is a module-level singleton, so a second `TestClient` over it runs a
    second lifespan: it replaces `app.state.qdrant_client` with a client of its own,
    and whichever block exits first closes the one it built while the other request is
    still in flight. A request that loses its Qdrant client that way fails in
    `remove_entry_chunks`, which is silent by requirement - so the test sees leaked
    chunks and blames the code under test for a teardown the harness did.
    """
    await engine.dispose()
    with (
        patch("chat.rag.indexing.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client()
        for extra in patches or []:
            extra.start()
        try:
            with TestClient(app):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://t"
                ) as http:
                    if session_id is not None:
                        http.cookies.set("visitdoc_session_id", session_id)
                    yield http
        finally:
            for extra in reversed(patches or []):
                extra.stop()


async def _call(
    session_id: str | None,
    method: str,
    path: str,
    body: Any | None = None,
    *,
    patches: list[Any] | None = None,
) -> Response:
    """Drive one `/faq` route through the real app, with fake embeddings."""
    async with _running_app(session_id, patches=patches) as http:
        return await http.request(method, path, json=body)


async def _create(session_id: str, content: str = _FIRST, **kwargs: Any) -> Response:
    return await _call(session_id, "POST", "/faq", {"content": content}, **kwargs)


async def _update(
    session_id: str, entry_id: int, content: str, **kwargs: Any
) -> Response:
    return await _call(
        session_id, "PUT", f"/faq/{entry_id}", {"content": content}, **kwargs
    )


# --- the create sequence ------------------------------------------------------------


async def test_a_create_publishes_one_revision_and_lists_it() -> None:
    session_id = await _session_id()

    response = await _create(session_id)

    assert response.status_code == 201
    entries = await _entries(session_id)
    assert [e.content for e in entries] == [_FIRST]
    chunks = await _points()
    assert {c.revision for c in chunks} == {entries[0].live_revision}
    assert {c.faq_entry_id for c in chunks} == {entries[0].id}
    assert {c.session_id for c in chunks} == {session_id}


async def test_the_row_is_the_last_thing_written() -> None:
    # FR-042a/c: chunking and embedding happen before either store is touched, and the
    # single local commit that inserts the row is the only moment the entry becomes
    # visible - to the console and to retrieval alike.
    session_id = await _session_id()
    order: list[str] = []
    real_upsert = indexing.upsert_chunks

    async def _recording_upsert(*args: Any, **kwargs: Any) -> None:
        order.append("chunks")
        await real_upsert(*args, **kwargs)

    async def _recording_embed(*args: Any, **kwargs: Any) -> list[list[float]]:
        order.append("embed")
        return await fake_embed_texts(*args, **kwargs)

    real_create = faq_repository.create

    async def _recording_create(*args: Any, **kwargs: Any) -> FaqEntry:
        order.append("row")
        return await real_create(*args, **kwargs)

    await _create(
        session_id,
        patches=[
            patch("chat.rag.indexing.upsert_chunks", _recording_upsert),
            patch("chat.rag.indexing.embed_texts", _recording_embed),
            patch("chat.api.faq.faq_repository.create", _recording_create),
        ],
    )

    assert order == ["embed", "chunks", "row"]


async def test_a_creates_id_is_reserved_before_its_chunks_are_written() -> None:
    # The chunks carry the entry they belong to, so the id has to exist before them -
    # and it comes from the sequence rather than from an inserted row (research #12).
    session_id = await _session_id()

    await _create(session_id)

    entries = await _entries(session_id)
    chunks = await _points()
    assert chunks
    assert all(c.faq_entry_id == entries[0].id for c in chunks)


# --- the three failure points -------------------------------------------------------


def _embedding_fails() -> Any:
    return patch(
        "chat.rag.indexing.embed_texts", side_effect=RuntimeError("voyage is down")
    )


def _chunk_write_fails() -> Any:
    return patch(
        "chat.rag.indexing.upsert_chunks", side_effect=RuntimeError("qdrant is down")
    )


def _publishing_commit_fails(target: str) -> Any:
    return patch(target, side_effect=RuntimeError("postgres is down"))


@pytest.mark.parametrize(
    "failure",
    [
        _embedding_fails,
        _chunk_write_fails,
        lambda: _publishing_commit_fails("chat.api.faq.faq_repository.create"),
    ],
)
async def test_a_failed_create_leaves_the_corpus_exactly_as_it_was(
    failure: Any,
) -> None:
    session_id = await _session_id()
    await _create(session_id, _FIRST)
    before = await _entries(session_id)

    response = await _create(
        session_id, "Parking is free for the first hour.", patches=[failure()]
    )

    assert response.status_code >= 500
    after = await _entries(session_id)
    assert [(e.id, e.content, e.live_revision) for e in after] == [
        (e.id, e.content, e.live_revision) for e in before
    ]


@pytest.mark.parametrize(
    "failure",
    [
        _embedding_fails,
        _chunk_write_fails,
        lambda: _publishing_commit_fails("chat.api.faq.faq_repository.publish"),
    ],
)
async def test_a_failed_update_leaves_the_entry_answering_its_previous_text(
    failure: Any,
) -> None:
    # FR-042e/SC-015a. The whole point: whatever broke, the assistant goes on answering
    # from the text it was answering from a moment ago.
    session_id = await _session_id()
    created = await _create(session_id, _FIRST)
    entry_id = created.json()["id"]
    before = (await _entries(session_id))[0]

    response = await _update(session_id, entry_id, _SECOND, patches=[failure()])

    assert response.status_code >= 500
    after = (await _entries(session_id))[0]
    assert after.content == _FIRST
    assert after.live_revision == before.live_revision


async def test_content_that_chunks_to_nothing_is_refused_not_published() -> None:
    # `FaqEntryWrite` already rejects content with no meaningful text, and a slice of
    # meaningful content is meaningful too - so this is reached by patching the
    # chunker, not by posting a body. It is guarded all the same, because a revision
    # published with nothing behind it is the one state this design forbids: the row
    # would vouch for an answer the store cannot produce, and the sweep that follows
    # the publish would take the previous revision's chunks with it.
    session_id = await _session_id()
    created = await _create(session_id, _FIRST)
    entry_id = created.json()["id"]
    before = (await _entries(session_id))[0]
    chunks_before = {(c.revision, c.chunk_index) for c in await _points()}

    response = await _update(
        session_id,
        entry_id,
        _SECOND,
        patches=[patch("chat.rag.indexing.chunk_content", return_value=[])],
    )

    assert response.status_code >= 500
    after = (await _entries(session_id))[0]
    assert after.content == _FIRST
    assert after.live_revision == before.live_revision
    assert chunks_before <= {(c.revision, c.chunk_index) for c in await _points()}


async def test_a_failed_save_performs_no_compensating_write() -> None:
    # The deleted `_revert_faq_update` is part of this feature: a best-effort repair
    # that half-succeeds and swallows its own failure is what left the two stores
    # silently disagreeing. Under additive revisions there is nothing to compensate for.
    session_id = await _session_id()
    created = await _create(session_id, _FIRST)
    entry_id = created.json()["id"]
    live_before = (await _entries(session_id))[0].live_revision
    chunks_before = {(c.revision, c.chunk_index) for c in await _points()}

    await _update(
        session_id,
        entry_id,
        _SECOND,
        patches=[_publishing_commit_fails("chat.api.faq.faq_repository.publish")],
    )

    chunks_after = {(c.revision, c.chunk_index) for c in await _points()}
    # The new revision's chunks are simply left where they were written: unreachable,
    # because no row names them, and swept by the next successful save.
    assert chunks_before <= chunks_after
    assert (await _entries(session_id))[0].live_revision == live_before
    assert not hasattr(faq_api, "_revert_faq_update")


# --- the staleness guard ------------------------------------------------------------


async def test_two_saves_racing_on_one_entry_leave_exactly_one_live_revision() -> None:
    # FR-042c: the expected revision is read inside the operation and carried in the
    # publishing `UPDATE`'s own `WHERE`, so the loser writes nothing rather than
    # publishing over a revision it never saw.
    session_id = await _session_id()
    created = await _create(session_id, _FIRST)
    entry_id = created.json()["id"]

    first, second = await asyncio.gather(
        _update(session_id, entry_id, "One version of the answer."),
        _update(session_id, entry_id, "A different version of the answer."),
        return_exceptions=True,
    )

    statuses = sorted(r.status_code for r in (first, second) if isinstance(r, Response))
    assert statuses[0] == 200
    entry = (await _entries(session_id))[0]
    live = [c for c in await _points() if c.revision == entry.live_revision]
    assert live
    assert entry.content in {
        "One version of the answer.",
        "A different version of the answer.",
    }


async def test_a_publish_that_matched_nothing_is_a_failed_save() -> None:
    # Zero rows updated means another save had already superseded the revision this
    # one read. That is an ordinary outcome and a retryable one - not a 404, which
    # would tell a staff member their entry is gone when it is sitting there answering.
    session_id = await _session_id()
    created = await _create(session_id, _FIRST)
    entry_id = created.json()["id"]

    async def _lost_the_race(*_args: Any, **_kwargs: Any) -> None:
        return None

    with capture_logs() as logs:
        response = await _update(
            session_id,
            entry_id,
            _SECOND,
            patches=[patch("chat.api.faq.faq_repository.publish", _lost_the_race)],
        )

    assert response.status_code == 409
    assert any(entry["event"] == "faq.publish_conflict" for entry in logs)
    # And the entry is still there, still answering what it answered before.
    assert (await _entries(session_id))[0].content == _FIRST


# --- the corpus cap -----------------------------------------------------------------


async def test_two_creates_racing_at_the_cap_cannot_push_a_session_past_it() -> None:
    # FR-039f: the cap bounds the filter term every FAQ retrieval turn carries, so it
    # is the whole point of the check rather than an off-by-one. A count read in one
    # transaction and an insert committed in another cannot enforce it: both creates
    # read the same last free place and both take it. The count and the insert share
    # one transaction, serialized per session, so the second one counts the first.
    session_id = await _session_id()
    capped = Settings(FAQ_MAX_ENTRIES_PER_SESSION=2)
    with patch("chat.api.faq.get_settings", return_value=capped):
        await _create(session_id, "Parking is free for the first hour.")

        # Both creates are held at their chunk write until the other arrives, so each
        # has counted the corpus before either has inserted into it - the interleaving
        # the cap has to survive, and one a pair of real requests only sometimes
        # produces.
        both_have_counted = asyncio.Barrier(2)
        real_upsert = indexing.upsert_chunks

        async def _upsert_together(*args: Any, **kwargs: Any) -> None:
            await both_have_counted.wait()
            await real_upsert(*args, **kwargs)

        # Both through one running app: two would tear each other's Qdrant client
        # down mid-request, and the refused create's cleanup is exactly what that
        # loses (see `_running_app`).
        with patch("chat.rag.indexing.upsert_chunks", _upsert_together):
            async with _running_app(session_id) as http:
                first, second = await asyncio.gather(
                    http.post("/faq", json={"content": "One version of the answer."}),
                    http.post(
                        "/faq", json={"content": "A different version of the answer."}
                    ),
                    return_exceptions=True,
                )

    statuses = sorted(r.status_code for r in (first, second) if isinstance(r, Response))
    assert statuses == [201, 409]
    entries = await _entries(session_id)
    assert len(entries) == 2
    # And the refused create left nothing behind: the chunks it had already written
    # belong to an id no row will ever name, so they are removed rather than leaked
    # into a store the rows no longer account for.
    assert {c.revision for c in await _points()} == {e.live_revision for e in entries}


# --- retrying ------------------------------------------------------------------------


async def test_a_failed_save_succeeds_on_resubmission_with_no_manual_repair() -> None:
    # FR-042g/SC-015c: retrying is always safe and needs nothing done to the index by
    # hand - the retry writes its own revision and publishes it.
    session_id = await _session_id()
    created = await _create(session_id, _FIRST)
    entry_id = created.json()["id"]
    await _update(session_id, entry_id, _SECOND, patches=[_embedding_fails()])

    response = await _update(session_id, entry_id, _SECOND)

    assert response.status_code == 200
    entry = (await _entries(session_id))[0]
    assert entry.content == _SECOND
    live = [c for c in await _points() if c.revision == entry.live_revision]
    assert live
    assert all(_SECOND[:20] in c.chunk_text for c in live)


async def test_resubmitting_a_save_that_already_succeeded_changes_nothing_visible() -> (
    None
):
    session_id = await _session_id()
    created = await _create(session_id, _FIRST)
    entry_id = created.json()["id"]
    await _update(session_id, entry_id, _SECOND)
    first_live = (await _entries(session_id))[0].live_revision

    await _update(session_id, entry_id, _SECOND)

    entries = await _entries(session_id)
    assert len(entries) == 1
    assert entries[0].content == _SECOND
    # A second publish is a new revision, and exactly one of them is live.
    assert len([c.revision for c in await _points() if c.revision == first_live]) >= 0
    live = {
        c.revision for c in await _points() if c.revision == entries[0].live_revision
    }
    assert len(live) == 1


# --- the sweep ------------------------------------------------------------------------


async def test_the_sweep_removes_this_entrys_superseded_revisions() -> None:
    session_id = await _session_id()
    created = await _create(session_id, _FIRST)
    entry_id = created.json()["id"]
    first_live = (await _entries(session_id))[0].live_revision

    await _update(session_id, entry_id, _SECOND)

    revisions = {c.revision for c in await _points()}
    assert first_live not in revisions
    assert revisions == {(await _entries(session_id))[0].live_revision}


async def test_the_sweep_removes_a_revision_that_was_never_published() -> None:
    # One predicate covers both: "not the live one" says nothing about how a revision
    # came to exist.
    session_id = await _session_id()
    created = await _create(session_id, _FIRST)
    entry_id = created.json()["id"]
    await _update(
        session_id,
        entry_id,
        _SECOND,
        patches=[_publishing_commit_fails("chat.api.faq.faq_repository.publish")],
    )
    assert len({c.revision for c in await _points()}) == 2

    await _update(session_id, entry_id, "A third attempt, which succeeds.")

    entry = (await _entries(session_id))[0]
    assert {c.revision for c in await _points()} == {entry.live_revision}


async def test_a_late_sweep_spares_the_revision_that_overtook_it() -> None:
    # A save's sweep reaches Qdrant after its publishing commit, so a later save can
    # publish in between and the revision this sweep holds is no longer the live one.
    # A predicate of "everything but mine" would then delete the live revision's
    # chunks, leaving a row vouching for an answer the store can no longer produce.
    session_id = await _session_id()
    created = await _create(session_id, _FIRST)
    entry_id = created.json()["id"]
    overtaken = (await _entries(session_id))[0].live_revision

    await _update(session_id, entry_id, _SECOND)
    live = (await _entries(session_id))[0].live_revision
    qdrant_client = create_client(Settings())
    try:
        await indexing.sweep_entry(qdrant_client, entry_id, overtaken)
    finally:
        await qdrant_client.close()

    assert {c.revision for c in await _points()} == {live}


async def test_the_sweep_is_idempotent() -> None:
    session_id = await _session_id()
    created = await _create(session_id, _FIRST)
    entry_id = created.json()["id"]
    entry = (await _entries(session_id))[0]
    qdrant_client = create_client(Settings())
    try:
        await indexing.sweep_entry(qdrant_client, entry_id, entry.live_revision)
        await indexing.sweep_entry(qdrant_client, entry_id, entry.live_revision)
    finally:
        await qdrant_client.close()

    assert {c.revision for c in await _points()} == {entry.live_revision}


async def test_a_failed_sweep_fails_nothing_and_says_nothing() -> None:
    # FR-042h/SC-015d: a sweep is not an operation. Chunks that are not live are
    # already unreachable, so a failed sweep costs storage and nothing else - and an
    # event raised for it would sit beside events raised for operations that failed.
    session_id = await _session_id()
    created = await _create(session_id, _FIRST)
    entry_id = created.json()["id"]

    with capture_logs() as logs:
        response = await _update(
            session_id,
            entry_id,
            _SECOND,
            patches=[
                patch(
                    "chat.rag.indexing.sweep_chunks",
                    side_effect=RuntimeError("qdrant is down"),
                )
            ],
        )

    assert response.status_code == 200
    assert (await _entries(session_id))[0].content == _SECOND
    events = [entry["event"] for entry in logs]
    assert "critical.dependency_unreachable" not in events
    assert not [e for e in events if "sweep" in e]
    assert not [e for e in events if e == "faq.operation_failed"]


async def test_the_sweep_never_widens_past_the_entry_it_is_for() -> None:
    # FR-042h: a session-wide predicate would delete a concurrent save's chunks in the
    # window between their write and the commit that publishes them.
    session_id = await _session_id()
    first = await _create(session_id, _FIRST)
    second = await _create(session_id, "Parking is free for the first hour.")
    other_entry_revision = (await _entries(session_id))[1].live_revision

    await _update(session_id, first.json()["id"], _SECOND)

    revisions = {c.revision for c in await _points()}
    assert other_entry_revision in revisions
    assert second.status_code == 201
