"""FAQ content CRUD endpoints (FR-006..FR-010, FR-016)."""

from fastapi import APIRouter, HTTPException, Request

from chat.core.config import get_settings
from chat.db.session import session_factory
from chat.domain.schemas import FaqEntry, FaqEntryWrite
from chat.rag.indexing import deindex_faq_entry, index_faq_entry
from chat.repositories import faq_repository

router = APIRouter()

_NOT_FOUND = "No entry with this ID."


@router.post("/faq", status_code=201)
async def create_faq_entry(body: FaqEntryWrite, request: Request) -> FaqEntry:
    """Create a new FAQ entry (FR-006)."""
    async with session_factory() as session:
        entry = await faq_repository.create(session, body.content)

    settings = get_settings()
    client = request.app.state.qdrant_client
    await index_faq_entry(client, settings, entry.id, entry.content)
    return FaqEntry.model_validate(entry)


@router.get("/faq")
async def list_faq_entries() -> list[FaqEntry]:
    """List existing FAQ entries (FR-008)."""
    async with session_factory() as session:
        entries = await faq_repository.list_all(session)
    return [FaqEntry.model_validate(entry) for entry in entries]


@router.get("/faq/{entry_id}")
async def get_faq_entry(entry_id: int) -> FaqEntry:
    """Retrieve a single FAQ entry by ID (FR-008)."""
    async with session_factory() as session:
        entry = await faq_repository.get(session, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return FaqEntry.model_validate(entry)


@router.put("/faq/{entry_id}")
async def update_faq_entry(
    entry_id: int, body: FaqEntryWrite, request: Request
) -> FaqEntry:
    """Replace the content of an existing FAQ entry; re-indexes it (FR-007, FR-010)."""
    async with session_factory() as session:
        entry = await faq_repository.update(session, entry_id, body.content)
    if entry is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)

    settings = get_settings()
    client = request.app.state.qdrant_client
    await index_faq_entry(client, settings, entry.id, entry.content)
    return FaqEntry.model_validate(entry)


@router.delete("/faq/{entry_id}", status_code=204)
async def delete_faq_entry(entry_id: int, request: Request) -> None:
    """Delete an existing FAQ entry (FR-016).

    Deindexes first (data-model.md ordering), so a partial failure never leaves
    orphaned, still-retrievable chunks behind.
    """
    await deindex_faq_entry(request.app.state.qdrant_client, entry_id)

    async with session_factory() as session:
        deleted = await faq_repository.delete(session, entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
