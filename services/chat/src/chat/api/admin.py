"""`/admin/sessions` — maintenance, guarded by one secret and absent from the schema.

Deliberately a separate module from `api/console.py`, and never reachable from it: a
maintenance surface sharing a module with a published one is one refactor away from
sharing its router and appearing in `/openapi.json`.

This is **not a user role** (FR-049). Patients and staff still never log in, and nothing
in the console links here.

The guard has four properties, each of which has a wrong default:

1. the secret travels in a **header**, never a query string or a path segment, which
   reach access logs and browser history where redaction does not follow;
2. the comparison is **constant-time**, and made over bytes rather than text, so a
   refusal says nothing about how much of the secret was right - and a secret written
   in any alphabet is compared rather than raising out of the guard;
3. every route declares `include_in_schema=False` **on the decorator** — a router
   cannot retroactively hide its routes from the published schema;
4. an **unset or empty configured secret refuses every request**, checked before the
   comparison — an empty one would otherwise `compare_digest`-match an empty header and
   admit everybody.
"""

import hmac
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from chat.clients import scheduling
from chat.clients.scheduling import (
    SchedulingError,
    SchedulingUnavailableError,
)
from chat.core.config import get_settings
from chat.core.logging import get_logger
from chat.db.session import session_factory
from chat.repositories import chat_repository
from chat.repositories.qdrant_repository import delete_by_session

router = APIRouter()

ADMIN_SECRET_HEADER = "X-Admin-Secret"
# One body for every refusal: absent, wrong, and not configured are reported
# identically, so a caller learns nothing from which of them they hit.
_REFUSED = "refused"


class SessionSummaryOut(BaseModel):
    """One session in the listing, and what deleting it would take with it.

    `chats` and `faq_entries` are what the deletion's cascade would remove, counted
    before it is asked for, so an admin can see the size of what they are about to
    delete. `last_message_at` is null for a session nobody ever said anything in.
    """

    session_id: str
    created_at: datetime
    chats: int
    faq_entries: int
    last_message_at: datetime | None = None


class SessionListingResponse(BaseModel):
    """Every session this service holds, oldest first.

    The same order the sweep attempts them in, so a listing read beforehand and the
    report it returns line up row for row.
    """

    sessions: list[SessionSummaryOut]


class SessionDeletionResult(BaseModel):
    """What happened to one session.

    `status` is `deleted` or `incomplete`, and a partial outcome is never reported as
    success. `detail` names the store that did not finish and says what is known of
    what it removed - nothing, or genuinely not known - so an admin re-running knows
    what they are waiting on; the counts are present only for a session that was
    actually removed.
    """

    session_id: str
    status: Literal["deleted", "incomplete"]
    detail: str | None = None
    patients_deleted: int | None = None
    practitioners_deleted: int | None = None
    appointments_deleted: int | None = None


class SessionDeletionResponse(BaseModel):
    """One result per session asked for, in the order they were attempted."""

    results: list[SessionDeletionResult]


def require_admin_secret(
    x_admin_secret: Annotated[str | None, Header()] = None,
) -> None:
    """Refuse the request unless it carries the configured admin secret.

    Raises: HTTPException 403, identically for every refusal.

    The configured value is checked for emptiness *before* the comparison: an empty
    configured secret would match an empty header and open every route to everybody,
    which is the one failure mode this guard exists to prevent. Blank counts as empty -
    a secret of spaces is a deployment that meant to set one and did not.

    The comparison is made over bytes because `compare_digest` raises on a non-ASCII
    `str`, and an exception escaping here is not a refusal: it answers differently from
    every other one, which is exactly what a prober is looking for. The supplied value
    is encoded back the way Starlette decoded it - latin-1, one byte per code point -
    so what is compared is the bytes the client actually sent, against the UTF-8 bytes
    a client sends a non-ASCII secret as.
    """
    configured = get_settings().ADMIN_SECRET.strip()
    if not configured or not x_admin_secret:
        raise HTTPException(status_code=403, detail=_REFUSED)
    try:
        provided = x_admin_secret.encode("latin-1")
    except UnicodeEncodeError:  # no header could have carried this value
        raise HTTPException(status_code=403, detail=_REFUSED) from None
    if not hmac.compare_digest(configured.encode("utf-8"), provided):
        raise HTTPException(status_code=403, detail=_REFUSED)


def _refuse(route: str) -> None:
    """Record a refusal, carrying nothing about the attempt.

    Not the supplied value, not its length, and not which of the three causes it was:
    each of those tells somebody probing this route how close they are.
    """
    get_logger().warning("admin.refused", route=route)


def _scheduling_detail(exc: SchedulingError) -> str:
    """Say what a failed scheduling deletion is known to have removed.

    Three answers, never collapsed into one: nothing, because no attempt reached the
    scheduler; nothing, because the scheduler answered by rejecting the request - which
    it will answer the same way again; or genuinely unknown, because it may have done
    the work after this service stopped waiting for it. Unknown is the fallback, since
    a failure this build cannot place is not evidence that nothing happened.
    """
    if isinstance(exc, SchedulingUnavailableError) and not exc.outcome_unknown:
        return f"scheduling deleted nothing - it could not be reached: {exc}"
    if scheduling.rejected_before_writing(exc):
        return f"scheduling deleted nothing - it rejected the request: {exc}"
    return f"scheduling may have deleted this session, or may not: {exc}"


async def _delete_one(request: Request, session_id: str) -> SessionDeletionResult:
    """Remove one session from both stores, and report what happened.

    The order is the one whose only failure mode is benign: the scheduler first, then
    this service's session row - which takes its chats, messages, marks and FAQ entries
    by cascade - then that session's chunks. A crash between the steps leaves a session
    a re-run clears, rather than stranding rows with nothing left to name them.

    A failure to remove the chunks is **not** an incomplete deletion: the rows that
    vouched for them are already gone, so they are unreachable, and reporting a leak
    would send an admin back to re-run something that already achieved every observable
    effect.
    """
    logger = get_logger()
    settings = get_settings()
    try:
        purged = await scheduling.delete_session(
            request.app.state.scheduling_channel, settings, session_id=session_id
        )
    except SchedulingError as exc:
        # The base class, not the outage subclass: a status the scheduler *answered*
        # with is this one session's failure too, and letting it escape would abandon
        # the sweep and lose the report for every session already deleted.
        logger.warning(
            "session.delete_incomplete", session_id=session_id, failed_at="scheduling"
        )
        return SessionDeletionResult(
            session_id=session_id,
            status="incomplete",
            detail=_scheduling_detail(exc),
        )

    try:
        async with session_factory() as db_session:
            chats = await chat_repository.delete_session(db_session, session_id)
    except Exception as exc:  # noqa: BLE001 - reported per session, never raised
        logger.warning(
            "session.delete_incomplete", session_id=session_id, failed_at="chat_store"
        )
        return SessionDeletionResult(
            session_id=session_id,
            status="incomplete",
            detail=f"this service's store did not complete the deletion: {exc}",
        )

    # Silent on failure, exactly as the FAQ sweep is: nothing names these chunks any
    # more, so they are already unreachable.
    await _remove_chunks(request, session_id)

    logger.info(
        "session.deleted",
        session_id=session_id,
        chats_deleted=chats.chats_deleted,
        faq_entries_deleted=chats.faq_entries_deleted,
        patients_deleted=purged.patients_deleted,
        practitioners_deleted=purged.practitioners_deleted,
        appointments_deleted=purged.appointments_deleted,
    )
    return SessionDeletionResult(
        session_id=session_id,
        status="deleted",
        patients_deleted=purged.patients_deleted,
        practitioners_deleted=purged.practitioners_deleted,
        appointments_deleted=purged.appointments_deleted,
    )


async def _remove_chunks(request: Request, session_id: str) -> None:
    """Remove a deleted session's chunks, reporting nothing if it fails."""
    try:
        await delete_by_session(request.app.state.qdrant_client, session_id)
    except Exception:  # noqa: BLE001, S110 - see `_delete_one`: a leak is not a failure
        pass


@router.get("/admin/sessions", include_in_schema=False)
async def list_sessions(
    x_admin_secret: Annotated[str | None, Header()] = None,
) -> SessionListingResponse:
    """List every session this service holds, with what deleting one would remove.

    Raises: HTTPException 403 if the request is not carrying the configured secret.

    Read-only, and this service's stores only - nothing here calls the scheduler, so a
    listing is answerable while scheduling is unreachable, which is exactly when an
    admin is looking for what to re-run. The scheduler's own counts are therefore not
    part of it: they are known only once a deletion has asked for them.
    """
    try:
        require_admin_secret(x_admin_secret)
    except HTTPException:
        _refuse("list_sessions")
        raise

    async with session_factory() as db_session:
        summaries = await chat_repository.list_sessions(db_session)
    return SessionListingResponse(
        sessions=[
            SessionSummaryOut(
                session_id=summary.session_id,
                created_at=summary.created_at,
                chats=summary.chats,
                faq_entries=summary.faq_entries,
                last_message_at=summary.last_message_at,
            )
            for summary in summaries
        ]
    )


@router.delete(
    "/admin/sessions/{session_id}",
    include_in_schema=False,
)
async def delete_session(
    session_id: str,
    request: Request,
    x_admin_secret: Annotated[str | None, Header()] = None,
) -> SessionDeletionResponse:
    """Delete one session from both stores.

    Raises: HTTPException 403 if the request is not carrying the configured secret.

    Deleting an absent session succeeds with zero counts on both sides, so re-running
    one reported incomplete is safe and converges.
    """
    try:
        require_admin_secret(x_admin_secret)
    except HTTPException:
        _refuse("delete_session")
        raise
    return SessionDeletionResponse(results=[await _delete_one(request, session_id)])


@router.delete("/admin/sessions", include_in_schema=False)
async def delete_all_sessions(
    request: Request,
    x_admin_secret: Annotated[str | None, Header()] = None,
) -> SessionDeletionResponse:
    """Delete every session, offering per session exactly what one deletion offers.

    Raises: HTTPException 403 if the request is not carrying the configured secret.

    One session failing does not stop the rest: each is attempted and reported on its
    own, so an admin sees which ones to re-run rather than being told the whole sweep
    failed.
    """
    try:
        require_admin_secret(x_admin_secret)
    except HTTPException:
        _refuse("delete_all_sessions")
        raise

    async with session_factory() as db_session:
        session_ids = await chat_repository.list_session_ids(db_session)
    results = [await _delete_one(request, session_id) for session_id in session_ids]
    return SessionDeletionResponse(results=results)
