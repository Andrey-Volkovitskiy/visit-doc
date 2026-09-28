"""Postgres repository for the sign-in server: clients, sign-ins, authorization codes.

Each step that spends something - completing a sign-in, counting a wrong code,
exchanging an authorization code - is one conditional `UPDATE ... RETURNING`, so a
second attempt at the same step finds nothing to update. None of these functions
commit; the caller owns the transaction, because several of them are one step of a
larger one.
"""

from datetime import timedelta

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from chat.connectors.secrets import digest, new_secret
from chat.domain.models import (
    MAX_FAILED_PAIRING_ATTEMPTS,
    OAuthAuthorizationCode,
    OAuthAuthorizationRequest,
    OAuthClient,
)

AUTHORIZATION_REQUEST_LIFETIME = timedelta(minutes=10)
AUTHORIZATION_CODE_LIFETIME = timedelta(seconds=60)


async def create_client(
    session: AsyncSession, *, client_name: str, redirect_uri: str
) -> OAuthClient:
    """Register a client under a new random `client_id`."""
    client = OAuthClient(
        client_id=new_secret(), client_name=client_name, redirect_uri=redirect_uri
    )
    session.add(client)
    await session.flush()
    await session.refresh(client)
    return client


async def get_client(session: AsyncSession, client_id: str) -> OAuthClient | None:
    """Return the registered client, or None if there is none by that id."""
    return await session.get(OAuthClient, client_id)


def _live_request(request_id: str) -> list[ColumnElement[bool]]:
    """The predicate naming `request_id`'s sign-in while it can still accept a code."""
    return [
        OAuthAuthorizationRequest.id_hash == digest(request_id),
        OAuthAuthorizationRequest.completed_at.is_(None),
        OAuthAuthorizationRequest.failed_attempts < MAX_FAILED_PAIRING_ATTEMPTS,
        OAuthAuthorizationRequest.expires_at > func.now(),
    ]


async def create_request(
    session: AsyncSession,
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str | None,
    scope: str,
    resource: str,
) -> str:
    """Store a sign-in in progress.

    Returns: the request's id, which the pairing page carries. Only its digest is
        stored.
    """
    request_id = new_secret()
    session.add(
        OAuthAuthorizationRequest(
            id_hash=digest(request_id),
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            state=state,
            scope=scope,
            resource=resource,
            expires_at=func.now() + AUTHORIZATION_REQUEST_LIFETIME,
        )
    )
    await session.flush()
    return request_id


async def get_live_request(
    session: AsyncSession, request_id: str
) -> OAuthAuthorizationRequest | None:
    """Return the sign-in `request_id` names, or None if it can accept no code.

    None for an unknown, expired or completed request, and for one that has had its
    five wrong codes.
    """
    result = await session.execute(
        select(OAuthAuthorizationRequest).where(*_live_request(request_id))
    )
    return result.scalars().first()


async def record_failed_attempt(session: AsyncSession, request_id: str) -> int | None:
    """Count one wrong code against a live sign-in.

    Returns: the sign-in's failures after this one, or None if it could accept no
        code, in which case nothing was counted.
    """
    result = await session.execute(
        update(OAuthAuthorizationRequest)
        .where(*_live_request(request_id))
        .values(failed_attempts=OAuthAuthorizationRequest.failed_attempts + 1)
        .returning(OAuthAuthorizationRequest.failed_attempts)
    )
    return result.scalars().first()


async def complete_request(
    session: AsyncSession, request_id: str
) -> OAuthAuthorizationRequest | None:
    """Mark a live sign-in completed.

    Returns: the sign-in as it was stored, or None if it could accept no code, in
        which case nothing changed.
    """
    result = await session.execute(
        update(OAuthAuthorizationRequest)
        .where(*_live_request(request_id))
        .values(completed_at=func.now())
        .returning(OAuthAuthorizationRequest)
    )
    return result.scalars().first()


async def create_authorization_code(
    session: AsyncSession,
    *,
    session_id: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    scope: str,
) -> str:
    """Store a one-minute authorization code for `session_id`.

    Returns: the code, which the redirect carries. Only its digest is stored.
    """
    code = new_secret()
    session.add(
        OAuthAuthorizationCode(
            code_hash=digest(code),
            session_id=session_id,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            scope=scope,
            expires_at=func.now() + AUTHORIZATION_CODE_LIFETIME,
        )
    )
    await session.flush()
    return code


async def consume_authorization_code(
    session: AsyncSession, code: str
) -> OAuthAuthorizationCode | None:
    """Spend an authorization code, if it is unused and unexpired.

    Returns: the code as it was stored, or None for an unknown, used or expired one.

    Rolled back with the rest of its transaction when a later check refuses the
    exchange, which leaves the code unused.
    """
    result = await session.execute(
        update(OAuthAuthorizationCode)
        .where(
            OAuthAuthorizationCode.code_hash == digest(code),
            OAuthAuthorizationCode.used_at.is_(None),
            OAuthAuthorizationCode.expires_at > func.now(),
        )
        .values(used_at=func.now())
        .returning(OAuthAuthorizationCode)
    )
    return result.scalars().first()
