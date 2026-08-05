"""FAQ content CRUD endpoints (FR-006..FR-010/015/016/018/021/022)."""

from fastapi import APIRouter, HTTPException, Request

from chat.core.config import get_settings
from chat.core.correlation import bind_operation_id
from chat.core.logging import get_logger
from chat.db.session import session_factory
from chat.domain.schemas import FaqEntry, FaqEntryWrite
from chat.rag.indexing import FaqOperationError, deindex_faq_entry, index_faq_entry
from chat.repositories import faq_repository

router = APIRouter()

_NOT_FOUND = "No entry with this ID."


def _log_faq_failure(
    operation: str, entry_id: int | None, exc: Exception, *, dependency: str | None
) -> None:
    """Log `faq.operation_failed`, plus a critical event if `dependency` is given.

    `dependency` is the failing external system's name ("postgres"/"qdrant") when the
    failure is a dependency being unreachable, or None for a failure outside FR-015's
    critical-event scope, e.g. chunking (FR-007, FR-015, FR-016, FR-017, FR-018).
    """
    logger = get_logger()
    if isinstance(exc, FaqOperationError):
        failed_step, cause = exc.failed_step, exc.cause
    else:
        failed_step, cause = "persist", exc
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

    Covers read-only endpoints (list/get), which aren't "management operations"
    (FR-007) and so carry no `operation_id` - the failure is still a critical event
    in its own right (FR-015, spec.md edge cases: "...or during normal operation").
    """
    get_logger().critical(
        "critical.dependency_unreachable", dependency=dependency, error_detail=str(exc)
    )


@router.post("/faq", status_code=201)
async def create_faq_entry(body: FaqEntryWrite, request: Request) -> FaqEntry:
    """Create a new FAQ entry (FR-006)."""
    with bind_operation_id():
        try:
            async with session_factory() as session:
                entry = await faq_repository.create(session, body.content)
        except Exception as exc:
            _log_faq_failure("create", None, exc, dependency="postgres")
            raise

        settings = get_settings()
        client = request.app.state.qdrant_client
        try:
            await index_faq_entry(client, settings, entry.id, entry.content)
        except FaqOperationError as exc:
            dependency = "qdrant" if exc.failed_step == "persist" else None
            _log_faq_failure("create", entry.id, exc, dependency=dependency)
            raise

        get_logger().info("faq.entry_created", entry_id=entry.id)
        return FaqEntry.model_validate(entry)


@router.get("/faq")
async def list_faq_entries() -> list[FaqEntry]:
    """List existing FAQ entries (FR-008)."""
    try:
        async with session_factory() as session:
            entries = await faq_repository.list_all(session)
    except Exception as exc:
        _log_dependency_unreachable("postgres", exc)
        raise
    return [FaqEntry.model_validate(entry) for entry in entries]


@router.get("/faq/{entry_id}")
async def get_faq_entry(entry_id: int) -> FaqEntry:
    """Retrieve a single FAQ entry by ID (FR-008)."""
    try:
        async with session_factory() as session:
            entry = await faq_repository.get(session, entry_id)
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
    """Replace the content of an existing FAQ entry; re-indexes it (FR-007, FR-010)."""
    with bind_operation_id():
        try:
            async with session_factory() as session:
                entry = await faq_repository.update(session, entry_id, body.content)
        except Exception as exc:
            _log_faq_failure("update", entry_id, exc, dependency="postgres")
            raise
        if entry is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)

        settings = get_settings()
        client = request.app.state.qdrant_client
        try:
            await index_faq_entry(client, settings, entry.id, entry.content)
        except FaqOperationError as exc:
            dependency = "qdrant" if exc.failed_step == "persist" else None
            _log_faq_failure("update", entry.id, exc, dependency=dependency)
            raise

        get_logger().info("faq.entry_updated", entry_id=entry.id)
        return FaqEntry.model_validate(entry)


@router.delete("/faq/{entry_id}", status_code=204)
async def delete_faq_entry(entry_id: int, request: Request) -> None:
    """Delete an existing FAQ entry (FR-016).

    Deindexes first (data-model.md ordering), so a partial failure never leaves
    orphaned, still-retrievable chunks behind.
    """
    with bind_operation_id():
        try:
            await deindex_faq_entry(request.app.state.qdrant_client, entry_id)
        except FaqOperationError as exc:
            _log_faq_failure("delete", entry_id, exc, dependency="qdrant")
            raise

        try:
            async with session_factory() as session:
                deleted = await faq_repository.delete(session, entry_id)
        except Exception as exc:
            _log_faq_failure("delete", entry_id, exc, dependency="postgres")
            raise

        if not deleted:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        get_logger().info("faq.entry_deleted", entry_id=entry_id)
