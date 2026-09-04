"""`/faq` — the corpus one session's assistant answers from.

Every route filters on the cookie session, so an entry belonging to somebody else is
reported exactly as one that never existed.

The write path is the feature's deepest change and its shape is the whole point. A save
chunks and embeds *before* either store is written, adds its chunks under a **new
revision**, and publishes with one local commit — the single moment the entry becomes
visible to a listing and to retrieval alike. Nothing is deleted, overwritten or
reverted, so a failure at any step leaves the entry answering exactly the text it was
answering a moment ago, and the accepted cost is leaked storage rather than a lost
answer.

There is deliberately no compensating write. One existed, and a best-effort repair that
half-succeeded and swallowed its own failure is what used to leave the two stores
silently disagreeing.
"""

from typing import NoReturn

from fastapi import APIRouter, HTTPException, Request
from ulid import ULID

from chat.api.dependencies import get_voyage_client
from chat.api.session_cookie import read_session_id
from chat.core.config import get_settings
from chat.core.correlation import bind_operation_id
from chat.core.logging import get_logger
from chat.db.session import session_factory
from chat.domain.schemas import FaqEntry, FaqEntryWrite
from chat.rag.indexing import (
    DEPENDENCY_BY_STEP,
    FaqOperationError,
    publish_revision,
    remove_entry_chunks,
    sweep_entry,
)
from chat.repositories import faq_repository

router = APIRouter()

_NOT_FOUND = "No entry with this ID."
_CONFLICT = "That entry was changed by another save. Please try again."
_UNAVAILABLE = "the clinic's documents could not be saved; nothing was changed"


def _require_session(request: Request) -> str:
    """Return the caller's session id.

    Raises: HTTPException 404 if the request carries no session cookie.

    A visitor with no session owns no corpus, so an entry they name cannot exist for
    them - reported identically to one that belongs to somebody else, so a probing
    caller learns nothing from which case it hit.
    """
    session_id = read_session_id(request)
    if session_id is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return session_id


def _log_faq_failure(
    operation: str, entry_id: int | None, exc: Exception, *, dependency: str | None
) -> None:
    """Log `faq.operation_failed`, plus a critical event if `dependency` is given.

    Args:
        dependency: The failing external system's name when the failure is a dependency
            being unreachable, or None for a failure outside that critical-event scope.
    """
    logger = get_logger()
    if isinstance(exc, FaqOperationError):
        failed_step, cause = exc.failed_step, exc.cause
    else:
        failed_step, cause = "publish", exc
    logger.error(
        "faq.operation_failed",
        operation=operation,
        entry_id=entry_id,
        failed_step=failed_step,
        error_detail=str(cause),
    )
    if dependency is not None:
        logger.critical(
            "critical.dependency_unreachable",
            dependency=dependency,
            error_detail=str(cause),
        )


def _log_dependency_unreachable(dependency: str, exc: Exception) -> None:
    """Log a critical event for a dependency failure outside any FAQ operation.

    Covers read-only endpoints (list/get), which carry no `operation_id` - the
    failure is still logged as a critical event in its own right.
    """
    get_logger().critical(
        "critical.dependency_unreachable", dependency=dependency, error_detail=str(exc)
    )


def _refuse_full_corpus(session_id: str, count: int, cap: int) -> NoReturn:
    """Log the refusal and raise the 409 a create beyond the cap is answered with.

    Raises: HTTPException 409, always.
    """
    get_logger().info(
        "faq.create_refused", session_id=session_id, entry_count=count, cap=cap
    )
    raise HTTPException(
        status_code=409,
        detail=f"this session's corpus is full ({cap} entries) - delete one first",
    )


async def _write_chunks(
    request: Request,
    operation: str,
    session_id: str,
    entry_id: int,
    revision: str,
    content: str,
) -> None:
    """Chunk, embed and write this save's chunks under `revision`.

    Raises: HTTPException 503 if any step failed. Nothing observable changed: the entry
        is still answering from whatever revision its row names, and the retry is the
        same request again.
    """
    try:
        await publish_revision(
            request.app.state.qdrant_client,
            get_voyage_client(request),
            session_id,
            entry_id,
            revision,
            content,
        )
    except FaqOperationError as exc:
        _log_faq_failure(
            operation,
            entry_id,
            exc,
            dependency=DEPENDENCY_BY_STEP.get(exc.failed_step),
        )
        raise HTTPException(status_code=503, detail=_UNAVAILABLE) from exc


@router.post("/faq", status_code=201)
async def create_faq_entry(body: FaqEntryWrite, request: Request) -> FaqEntry:
    """Add an entry to this session's corpus.

    Raises:
        HTTPException 404: the request carries no session cookie.
        HTTPException 409: this session's corpus is already at its cap.
        HTTPException 503: a dependency was unreachable, and nothing was created.

    The id is reserved from the sequence before anything is written, so the chunks can
    carry the entry they belong to before the row that publishes them exists. A create
    that fails leaves that id unused, which costs nothing: the sequence is not a count
    of rows, and it never hands that id out again - so the only chunks this entry can
    ever have are the ones written just above, and there is nothing here to sweep.

    The cap is read twice, and only the second reading enforces it. The first, here,
    spares a full corpus the work of chunking and embedding something it will refuse;
    the one the insert carries is what makes the cap a bound, because a count taken in
    a transaction that ends before the insert only describes a corpus some other create
    is free to fill in the meantime.
    """
    session_id = _require_session(request)
    cap = get_settings().FAQ_MAX_ENTRIES_PER_SESSION
    with bind_operation_id():
        try:
            async with session_factory() as session:
                count = await faq_repository.count_for_session(session, session_id)
                if count >= cap:
                    _refuse_full_corpus(session_id, count, cap)
                entry_id = await faq_repository.reserve_id(session)
        except HTTPException:
            raise
        except Exception as exc:
            _log_faq_failure("create", None, exc, dependency="postgres")
            raise HTTPException(status_code=503, detail=_UNAVAILABLE) from exc

        revision = str(ULID())
        await _write_chunks(
            request, "create", session_id, entry_id, revision, body.content
        )

        try:
            async with session_factory() as session:
                entry, count = await faq_repository.create_within_cap(
                    session, session_id, body.content, revision, entry_id, cap
                )
        except Exception as exc:
            _log_faq_failure("create", entry_id, exc, dependency="postgres")
            raise HTTPException(status_code=503, detail=_UNAVAILABLE) from exc

        if entry is None:
            # Another create took the last place while this one was embedding. Nothing
            # was inserted, so the chunks already written carry an id no row will ever
            # name: removing them is the same silent housekeeping a delete does, not a
            # repair of anything a reader could have seen.
            await remove_entry_chunks(
                request.app.state.qdrant_client, session_id, entry_id
            )
            _refuse_full_corpus(session_id, count, cap)

        get_logger().info(
            "faq.entry_created",
            entry_id=entry.id,
            session_id=session_id,
            revision=revision,
        )
        return FaqEntry.model_validate(entry)


@router.get("/faq")
async def list_faq_entries(request: Request) -> list[FaqEntry]:
    """List this session's FAQ entries.

    A visitor with no session has an empty corpus, which is the ordinary starting state
    of every session rather than an error - so this returns an empty list, never a 404.

    Every entry listed is one the assistant can answer from, and there is no field
    saying so: an entry owns a live revision or it cannot be stored, so a retrievability
    indicator could never report anything but "yes" - and a signal that can never fire
    teaches a reader to rely on one that would not warn them.
    """
    session_id = read_session_id(request)
    if session_id is None:
        return []
    try:
        async with session_factory() as session:
            entries = await faq_repository.list_all(session, session_id)
    except Exception as exc:
        _log_dependency_unreachable("postgres", exc)
        raise
    return [FaqEntry.model_validate(entry) for entry in entries]


@router.get("/faq/{entry_id}")
async def get_faq_entry(entry_id: int, request: Request) -> FaqEntry:
    """Retrieve one of this session's FAQ entries by ID."""
    session_id = _require_session(request)
    try:
        async with session_factory() as session:
            entry = await faq_repository.get(session, session_id, entry_id)
    except Exception as exc:
        _log_dependency_unreachable("postgres", exc)
        raise
    if entry is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return FaqEntry.model_validate(entry)


@router.put("/faq/{entry_id}")
async def update_faq_entry(
    entry_id: int, body: FaqEntryWrite, request: Request
) -> FaqEntry:
    """Replace an entry's content, publishing a new revision of its chunks.

    Raises:
        HTTPException 404: no such entry in this session.
        HTTPException 409: another save published while this one was preparing. A
            failed, retryable save - not a missing entry.
        HTTPException 503: a dependency was unreachable, and nothing was changed.

    The revision this save expects to supersede is read here, inside the operation, and
    never supplied by the caller: a caller-supplied one would let a stale client publish
    over a save it never saw.
    """
    session_id = _require_session(request)
    with bind_operation_id():
        try:
            async with session_factory() as session:
                previous = await faq_repository.get(session, session_id, entry_id)
        except Exception as exc:
            _log_faq_failure("update", entry_id, exc, dependency="postgres")
            raise HTTPException(status_code=503, detail=_UNAVAILABLE) from exc
        if previous is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        superseded = previous.live_revision

        revision = str(ULID())
        await _write_chunks(
            request, "update", session_id, entry_id, revision, body.content
        )

        try:
            async with session_factory() as session:
                entry = await faq_repository.publish(
                    session, session_id, entry_id, body.content, revision, superseded
                )
        except Exception as exc:
            _log_faq_failure("update", entry_id, exc, dependency="postgres")
            raise HTTPException(status_code=503, detail=_UNAVAILABLE) from exc

        if entry is None:
            # The guard matched no row: another save had already superseded the
            # revision this one read. An ordinary outcome, and the loser is told its
            # save failed rather than told its entry is gone.
            get_logger().info(
                "faq.publish_conflict",
                entry_id=entry_id,
                session_id=session_id,
                expected_revision=superseded,
            )
            raise HTTPException(status_code=409, detail=_CONFLICT)

        await sweep_entry(
            request.app.state.qdrant_client, session_id, entry.id, revision
        )
        get_logger().info(
            "faq.entry_updated",
            entry_id=entry.id,
            session_id=session_id,
            revision=revision,
            superseded_revision=superseded,
        )
        return FaqEntry.model_validate(entry)


@router.delete("/faq/{entry_id}", status_code=204)
async def delete_faq_entry(entry_id: int, request: Request) -> None:
    """Delete an entry from this session's corpus.

    Raises: HTTPException 404 if this session has no such entry.

    The row goes **first**, which un-publishes every revision it named and makes the
    entry unanswerable at that instant. Removing its chunks follows as housekeeping, and
    its failure is not reported: the chunks are already unreachable, so reporting a leak
    as a failed delete would send somebody back to re-run something that already
    achieved every observable effect.
    """
    session_id = _require_session(request)
    with bind_operation_id():
        try:
            async with session_factory() as session:
                deleted = await faq_repository.delete(session, session_id, entry_id)
        except Exception as exc:
            _log_faq_failure("delete", entry_id, exc, dependency="postgres")
            raise HTTPException(status_code=503, detail=_UNAVAILABLE) from exc

        if not deleted:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)

        await remove_entry_chunks(request.app.state.qdrant_client, session_id, entry_id)
        get_logger().info("faq.entry_deleted", entry_id=entry_id, session_id=session_id)
