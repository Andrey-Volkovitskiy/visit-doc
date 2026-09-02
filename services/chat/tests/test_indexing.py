from unittest.mock import MagicMock, patch

import pytest
import structlog
from chat.core.correlation import bind_operation_id
from chat.rag.chunking import ChunkedText
from chat.rag.indexing import (
    FaqOperationError,
    publish_revision,
    remove_entry_chunks,
    sweep_entry,
)
from chat.rag.retriever import search_faq
from structlog.testing import capture_logs


async def test_publish_revision_chunks_embeds_and_writes_in_that_order() -> None:
    # Both stores are untouched until the last of the three, so a failure in either of
    # the first two changed nothing at all. And nothing is deleted: the revision being
    # answered from stays intact until a commit elsewhere names a different one live.
    calls: list[str] = []
    chunks = [ChunkedText(chunk_index=0, chunk_text="hello")]

    def fake_chunk(_content: str) -> list[ChunkedText]:
        calls.append("chunk")
        return chunks

    async def fake_embed(
        _client: object, _texts: list[str], input_type: str
    ) -> list[list[float]]:
        calls.append("embed")
        return [[0.1]]

    async def fake_upsert(*_args: object) -> None:
        calls.append("upsert")

    async def fake_delete(*_args: object) -> None:
        calls.append("delete")

    with (
        patch("chat.rag.indexing.chunk_content", fake_chunk),
        patch("chat.rag.indexing.embed_texts", fake_embed),
        patch("chat.rag.indexing.upsert_chunks", fake_upsert),
        patch("chat.rag.indexing.delete_by_entry", fake_delete),
        patch("chat.rag.indexing.sweep_chunks", fake_delete),
    ):
        await publish_revision(
            MagicMock(), MagicMock(), "01SESSION", 1, "01REVISION", "hello"
        )

    assert calls == ["chunk", "embed", "upsert"]


async def test_publish_revision_logs_substeps_correlated_by_operation_id() -> None:
    chunks = [
        ChunkedText(chunk_index=0, chunk_text="hello"),
        ChunkedText(chunk_index=1, chunk_text="world"),
    ]

    with (
        patch("chat.rag.indexing.chunk_content", return_value=chunks),
        patch("chat.rag.indexing.embed_texts", return_value=[[0.1], [0.2]]),
        patch("chat.rag.indexing.upsert_chunks"),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
        bind_operation_id(),
    ):
        await publish_revision(
            MagicMock(), MagicMock(), "01SESSION", 1, "01REVISION", "hello world"
        )

    operation_ids = {entry["operation_id"] for entry in logs}
    events = {entry["event"]: entry for entry in logs}

    assert len(operation_ids) == 1
    assert list(events) == ["faq.content_chunked", "faq.chunks_embedded"]
    assert events["faq.content_chunked"]["chunk_count"] == 2
    assert events["faq.chunks_embedded"]["chunk_count"] == 2


async def test_publish_revision_refuses_to_publish_a_revision_with_no_chunks() -> None:
    # A revision with no points behind it would be a row vouching for an answer the
    # store cannot produce - and the sweep that follows the publish would take the
    # previous revision's chunks with it. So an empty chunking result is a typed
    # failure, not a success that happened to write nothing.
    with (
        patch("chat.rag.indexing.chunk_content", return_value=[]),
        patch("chat.rag.indexing.embed_texts") as embed,
        patch("chat.rag.indexing.upsert_chunks") as upsert,
        pytest.raises(FaqOperationError) as raised,
    ):
        await publish_revision(
            MagicMock(), MagicMock(), "01SESSION", 1, "01REVISION", "- - -"
        )

    assert raised.value.failed_step == "chunking"
    embed.assert_not_called()
    upsert.assert_not_called()


async def test_the_sweep_addresses_one_entry_and_spares_its_live_revision() -> None:
    with patch("chat.rag.indexing.sweep_chunks") as sweep:
        await sweep_entry(MagicMock(), 7, "01LIVEREVISION")

    assert sweep.call_args.args[1:] == (7, "01LIVEREVISION")


async def test_a_failed_sweep_raises_nothing_and_logs_nothing() -> None:
    # FR-042h in its strongest form: not even a critical dependency event. A sweep is
    # not an operation, so nothing about it failing is an operator's problem.
    with (
        patch("chat.rag.indexing.sweep_chunks", side_effect=RuntimeError("down")),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
        bind_operation_id(),
    ):
        await sweep_entry(MagicMock(), 1, "01LIVEREVISION")

    assert logs == []


async def test_removing_a_deleted_entrys_chunks_is_equally_silent() -> None:
    # The row is already gone, so its revisions are unpublished and unreachable:
    # reporting a leak here would be reporting a delete that in fact succeeded.
    with (
        patch("chat.rag.indexing.delete_by_entry", side_effect=RuntimeError("down")),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
        bind_operation_id(),
    ):
        await remove_entry_chunks(MagicMock(), 1)

    assert logs == []


async def test_removing_a_deleted_entrys_chunks_addresses_that_entry() -> None:
    with patch("chat.rag.indexing.delete_by_entry") as delete:
        await remove_entry_chunks(MagicMock(), 7)

    assert delete.call_args.args[1] == 7


# --- 007: an empty corpus costs nothing, and is not a failed read -----------------


async def test_search_faq_with_no_live_revisions_calls_no_dependency() -> None:
    # With no live revisions there is no filter value that could match, so embedding
    # the query and searching would spend two dependencies to learn what the empty
    # list already said. Asserted as an absence: the calls must not happen at all.
    with (
        patch("chat.rag.retriever.embed_texts") as embed,
        patch("chat.rag.retriever.search") as search_points,
    ):
        found = await search_faq(MagicMock(), MagicMock(), "anything", [])

    assert found == []
    embed.assert_not_called()
    search_points.assert_not_called()


async def test_search_faq_with_live_revisions_passes_them_to_the_search() -> None:
    # The revisions reach the store as a filter term on the search itself, not as a
    # predicate applied to whatever came back.
    revisions = ["01REVISIONAAA", "01REVISIONBBB"]
    with (
        patch("chat.rag.retriever.embed_texts", return_value=[[0.1]]),
        patch("chat.rag.retriever.search", return_value=[]) as search_points,
    ):
        await search_faq(MagicMock(), MagicMock(), "anything", revisions)

    assert search_points.call_args.args[2] == revisions
