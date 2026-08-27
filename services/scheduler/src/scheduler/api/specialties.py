"""`GET /specialties` — the closed list, name-sorted."""

from fastapi import APIRouter
from shared_models.scheduling import Specialty

router = APIRouter()


@router.get("/specialties")
async def list_specialties() -> list[str]:
    """Return every specialty, sorted by name.

    The source a chooser is populated from, so the list lives in one place rather than
    being copied into each client. Sorted explicitly rather than relying on declaration
    order - the ordering is part of the contract.

    Deliberately not session-scoped: the list is the same for every caller and contains
    no session data, so this is the one route in this API that needs no session header.
    """
    return sorted(specialty.value for specialty in Specialty)
