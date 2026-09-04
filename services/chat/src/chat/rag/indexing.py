"""Publishing an entry's chunks under a new revision, and sweeping the old ones.

A save writes its chunks *additively*: it deletes, overwrites and modifies nothing, so
the revision currently being answered from stays intact until a later commit names a
different one live. That is what makes every failure here cost leaked storage rather
than a lost answer - there is no window in which an entry has a row and no chunks.

The sweep is the other side of that: housekeeping that removes what nothing vouches for
any more. It may fail, and when it does nothing is reported and nothing is logged.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient
from voyageai.client_async import AsyncClient

from chat.core.logging import get_logger
from chat.rag.chunking import chunk_content
from chat.rag.embeddings import embed_texts
from chat.repositories.qdrant_repository import (
    delete_by_entry,
    sweep_chunks,
    upsert_chunks,
)

# Which external system a failed sub-step was against, keyed by `FaqOperationError`'s
# `failed_step`. Declared beside the code that raises those tags rather than beside one
# of the callers that report them, so a new step and its dependency are named in one
# place. "chunking" is absent on purpose: it is pure computation, and nothing was
# unreachable.
DEPENDENCY_BY_STEP = {"embedding": "voyage", "persist": "qdrant"}


@dataclass(frozen=True)
class PendingRevision:
    """One entry's content, and the revision its chunks are about to be written as."""

    faq_entry_id: int
    revision: str
    content: str


class FaqOperationError(Exception):
    """Tags which sub-step of a FAQ operation failed.

    Raised, not logged, at the point of failure - logging happens once, centrally,
    by the caller.
    """

    def __init__(self, failed_step: str, cause: Exception) -> None:
        """Tag `cause` with the sub-step that raised it, keeping both for the caller."""
        super().__init__(str(cause))
        self.failed_step = failed_step
        self.cause = cause


async def publish_revision(
    qdrant_client: AsyncQdrantClient,
    voyage_client: AsyncClient,
    session_id: str,
    faq_entry_id: int,
    revision: str,
    content: str,
) -> None:
    """Chunk and embed `content`, then write its chunks as `revision`.

    Args:
        revision: The revision these chunks belong to. Nothing can retrieve them until
            the entry's row names this revision live, which is the caller's next step
            and the only moment the change becomes visible.

    Raises: FaqOperationError wrapping any failure, tagged "chunking", "embedding",
        or "persist" - including content that yields no chunks at all, which is tagged
        "chunking" and never published.

    Chunking and embedding both happen before either store is touched, so a failure in
    either has changed nothing at all. The write itself adds points and removes none,
    so a failure there leaves the previous revision answering exactly as it was.
    """
    await publish_revisions(
        qdrant_client,
        voyage_client,
        session_id,
        [PendingRevision(faq_entry_id, revision, content)],
    )


async def publish_revisions(
    qdrant_client: AsyncQdrantClient,
    voyage_client: AsyncClient,
    session_id: str,
    pending: Sequence[PendingRevision],
) -> None:
    """Chunk and embed every entry in `pending`, then write each one's chunks.

    Raises: FaqOperationError wrapping any failure, tagged "chunking", "embedding", or
        "persist". One failure fails the whole call: these revisions are published by
        one later commit, so a caller told this succeeded may name every one of them
        live, and a partial success would let it name one that has no chunks behind it.

    Every entry's chunks are embedded in a single call rather than one call per entry.
    That is what makes planting a whole starter corpus one round trip on the visitor's
    first request instead of one per entry, and it costs nothing at the single-entry
    call site above, which is the same request with a list of one.

    A failure here leaks the points already written and publishes nothing, which is the
    same trade every save in this module makes: leaked storage rather than a lost - or
    in this case a half-planted - answer.
    """
    logger = get_logger()

    chunked = []
    for item in pending:
        try:
            chunks = chunk_content(item.content)
        except Exception as exc:
            raise FaqOperationError("chunking", exc) from exc
        if not chunks:
            # Not reachable through `/faq` - the request schema rejects content with no
            # meaningful text, and a slice of meaningful content is meaningful too -
            # but a revision is what a row is about to vouch for, so "wrote nothing"
            # must not be able to return the same success as "wrote the chunks". A
            # publish behind an empty revision leaves an entry that lists, answers
            # nothing, and takes the revision it superseded with it when the sweep runs.
            raise FaqOperationError(
                "chunking", ValueError("content produced no chunks")
            )
        chunked.append(chunks)

    chunk_count = sum(len(chunks) for chunks in chunked)
    logger.info("faq.content_chunked", chunk_count=chunk_count)

    texts = [chunk.chunk_text for chunks in chunked for chunk in chunks]
    try:
        vectors = await embed_texts(voyage_client, texts, input_type="document")
    except Exception as exc:
        raise FaqOperationError("embedding", exc) from exc
    if len(vectors) != chunk_count:
        # One vector per chunk is what pairs them below, and a short answer would
        # otherwise be discovered while writing to Qdrant and reported as that store
        # being unreachable - naming the wrong dependency for an embedding that came
        # back wrong.
        raise FaqOperationError(
            "embedding",
            ValueError(f"expected {chunk_count} vectors, got {len(vectors)}"),
        )
    logger.info("faq.chunks_embedded", chunk_count=chunk_count)

    written = 0
    for item, chunks in zip(pending, chunked, strict=True):
        try:
            await upsert_chunks(
                qdrant_client,
                session_id,
                item.faq_entry_id,
                item.revision,
                chunks,
                vectors[written : written + len(chunks)],
            )
        except Exception as exc:
            raise FaqOperationError("persist", exc) from exc
        written += len(chunks)


async def sweep_entry(
    qdrant_client: AsyncQdrantClient,
    session_id: str,
    faq_entry_id: int,
    live_revision: str,
) -> None:
    """Remove `session_id`'s chunks of `faq_entry_id` older than `live_revision`.

    Args:
        live_revision: A revision of this entry that was live when the caller published
            it. It need not still be the live one - a later save may have published
            while this sweep was on its way, and one that has is left alone rather than
            swept away by a caller holding the revision it superseded.

    Never raises, never reports, and never logs - not even a critical dependency event.
    A sweep is not an operation: the chunks it removes are already unreachable, because
    no row names their revision, so a sweep that failed achieved nothing and cost
    nothing but storage. An event raised for it would sit beside events raised for
    operations that genuinely failed, which is the confusion this path exists to avoid.
    """
    try:
        await sweep_chunks(qdrant_client, session_id, faq_entry_id, live_revision)
    except Exception:  # noqa: BLE001, S110 - see the docstring: silent by requirement
        pass


async def remove_entry_chunks(
    qdrant_client: AsyncQdrantClient, session_id: str, faq_entry_id: int
) -> None:
    """Remove every chunk of a deleted entry, on the same silent terms as the sweep.

    The row is gone by the time this runs, so its revisions are already unpublished and
    unreachable. Reporting a failure here as a failed delete would send somebody back
    to re-run something that already achieved every observable effect.
    """
    try:
        await delete_by_entry(qdrant_client, session_id, faq_entry_id)
    except Exception:  # noqa: BLE001, S110 - see the docstring: silent by requirement
        pass
