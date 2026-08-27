"""`GET /health` — liveness for the scheduler process."""

from typing import Literal

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def get_health() -> dict[str, Literal["ok"]]:
    """Report that the process is up and serving.

    Deliberately does not touch the database or the gRPC server: it answers "is this
    process running", which is what a caller waiting for startup needs, and a check
    that fails on a slow query would answer a different question badly.
    """
    return {"status": "ok"}
