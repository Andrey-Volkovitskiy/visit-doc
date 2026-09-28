"""Postgres repository for grants and their tokens: what a paired app holds.

A grant is one app's standing permission for one session; its tokens are the access
and refresh credentials the app presents. Tokens are stored as digests only, and every
expiry is compared against the database's clock. None of these functions commit; the
caller owns the transaction.

Reads for the console carry the session in their `WHERE`, so another session's grant
simply does not resolve. Token lookups carry no session - a token is the credential
that *establishes* one - and return the grant's session for the caller to act as.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    ColumnElement,
    Integer,
    and_,
    exists,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from ulid import ULID

from chat.connectors.secrets import digest, new_secret
from chat.domain.models import OAuthGrant, OAuthToken, OAuthTokenKind

ACCESS_TOKEN_LIFETIME = timedelta(hours=1)
# Replaced on every use, so this is a limit on inactivity rather than on the grant.
REFRESH_TOKEN_LIFETIME = timedelta(days=30)
# How stale `last_used_at` may be before a verification writes it again, so an app
# asking often does not write on every call.
_LAST_USED_RESOLUTION = timedelta(minutes=1)


@dataclass(frozen=True)
class IssuedTokens:
    """A fresh access and refresh token for one grant, in the only form they exist."""

    grant_id: str
    access_token: str
    refresh_token: str
    expires_in: int
    scope: str


@dataclass(frozen=True)
class VerifiedGrant:
    """What a valid access token speaks for.

    `expires_at` is the token's expiry as a Unix timestamp.
    """

    grant_id: str
    session_id: str
    client_id: str
    scope: str
    expires_at: int


@dataclass(frozen=True)
class GrantSummary:
    """One active grant as the console lists it.

    Both ages are whole seconds measured on the database's clock; the last one is None
    for a grant never used.
    """

    id: str
    client_name: str
    paired_seconds_ago: int
    last_used_seconds_ago: int | None


def _seconds_since(
    column: InstrumentedAttribute[datetime] | InstrumentedAttribute[datetime | None],
) -> ColumnElement[int]:
    """Whole seconds from `column` to now, on the database's clock."""
    return func.floor(func.extract("epoch", func.now() - column)).cast(Integer)


async def _insert_token_pair(session: AsyncSession, grant_id: str) -> tuple[str, str]:
    """Store a new access and refresh token for `grant_id`.

    Returns: the access token and the refresh token, in plain form.
    """
    access_token = new_secret()
    refresh_token = new_secret()
    session.add_all(
        [
            OAuthToken(
                token_hash=digest(access_token),
                grant_id=grant_id,
                kind=OAuthTokenKind.ACCESS.value,
                expires_at=func.now() + ACCESS_TOKEN_LIFETIME,
            ),
            OAuthToken(
                token_hash=digest(refresh_token),
                grant_id=grant_id,
                kind=OAuthTokenKind.REFRESH.value,
                expires_at=func.now() + REFRESH_TOKEN_LIFETIME,
            ),
        ]
    )
    await session.flush()
    return access_token, refresh_token


async def create_grant_with_tokens(
    session: AsyncSession,
    *,
    session_id: str,
    client_id: str,
    client_name: str,
    scope: str,
) -> IssuedTokens:
    """Create a grant for `client_id` on `session_id`, with its first token pair."""
    grant_id = str(ULID())
    session.add(
        OAuthGrant(
            id=grant_id,
            session_id=session_id,
            client_id=client_id,
            client_name=client_name,
            scope=scope,
        )
    )
    await session.flush()
    access_token, refresh_token = await _insert_token_pair(session, grant_id)
    return IssuedTokens(
        grant_id=grant_id,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=int(ACCESS_TOKEN_LIFETIME.total_seconds()),
        scope=scope,
    )


async def verify_access(session: AsyncSession, token: str) -> VerifiedGrant | None:
    """Return what an access token speaks for, or None if it speaks for nothing.

    None for an unknown or expired token, a refresh token, and a token whose grant is
    revoked - which is what makes a revocation take effect on the very next call.
    A valid token stamps its grant's `last_used_at`, at most once a minute.
    """
    result = await session.execute(
        select(
            OAuthGrant.id,
            OAuthGrant.session_id,
            OAuthGrant.client_id,
            OAuthGrant.scope,
            # A timestamp, not a duration: a 32-bit integer stops holding one in 2038.
            func.floor(func.extract("epoch", OAuthToken.expires_at)).cast(BigInteger),
        )
        .join(OAuthGrant, OAuthGrant.id == OAuthToken.grant_id)
        .where(
            OAuthToken.token_hash == digest(token),
            OAuthToken.kind == OAuthTokenKind.ACCESS.value,
            OAuthToken.expires_at > func.now(),
            OAuthGrant.revoked_at.is_(None),
        )
    )
    row = result.first()
    if row is None:
        return None
    verified = VerifiedGrant(*row)
    await session.execute(
        update(OAuthGrant)
        .where(
            OAuthGrant.id == verified.grant_id,
            OAuthGrant.session_id == verified.session_id,
            or_(
                OAuthGrant.last_used_at.is_(None),
                OAuthGrant.last_used_at < func.now() - _LAST_USED_RESOLUTION,
            ),
        )
        .values(last_used_at=func.now())
    )
    return verified


class AccessRefusal(StrEnum):
    """Why an access token that was issued no longer speaks for its grant."""

    GRANT_REVOKED = "grant_revoked"
    TOKEN_EXPIRED = "token_expired"


@dataclass(frozen=True)
class RefusedAccess:
    """An access token this service issued, refused, and the grant it belonged to."""

    grant_id: str
    session_id: str
    reason: AccessRefusal


async def find_refused_access(
    session: AsyncSession, token: str
) -> RefusedAccess | None:
    """Return which grant a refused access token belonged to, and why it was refused.

    Returns: the grant, its session and the reason for a token this service issued
        that no longer verifies; None for a token it never issued, which concerns no
        session.

    Read only after `verify_access` refused the token, so a refusal can be logged
    against what it concerned.
    """
    result = await session.execute(
        select(
            OAuthGrant.id,
            OAuthGrant.session_id,
            OAuthGrant.revoked_at.is_not(None),
        )
        .select_from(OAuthToken)
        .join(OAuthGrant, OAuthGrant.id == OAuthToken.grant_id)
        .where(
            OAuthToken.token_hash == digest(token),
            OAuthToken.kind == OAuthTokenKind.ACCESS.value,
        )
    )
    row = result.first()
    if row is None:
        return None
    grant_id, session_id, revoked = row
    return RefusedAccess(
        grant_id=grant_id,
        session_id=session_id,
        reason=AccessRefusal.GRANT_REVOKED if revoked else AccessRefusal.TOKEN_EXPIRED,
    )


def _holds_live_refresh_token() -> ColumnElement[bool]:
    """Whether a grant can still be renewed: it holds an unused, unexpired refresh."""
    return exists().where(
        and_(
            OAuthToken.grant_id == OAuthGrant.id,
            OAuthToken.kind == OAuthTokenKind.REFRESH.value,
            OAuthToken.used_at.is_(None),
            OAuthToken.expires_at > func.now(),
        )
    )


async def list_for_session(
    session: AsyncSession, session_id: str
) -> list[GrantSummary]:
    """Return `session_id`'s active grants, newest first.

    Active means not revoked and still renewable. A revoked grant, and one idle past
    its refresh token's life, can do nothing, so neither is listed.
    """
    result = await session.execute(
        select(
            OAuthGrant.id,
            OAuthGrant.client_name,
            _seconds_since(OAuthGrant.created_at),
            _seconds_since(OAuthGrant.last_used_at),
        )
        .where(
            OAuthGrant.session_id == session_id,
            OAuthGrant.revoked_at.is_(None),
            _holds_live_refresh_token(),
        )
        .order_by(OAuthGrant.created_at.desc(), OAuthGrant.id.desc())
    )
    return [GrantSummary(*row) for row in result.all()]


@dataclass(frozen=True)
class RotatedRefresh:
    """A refresh token spent, and the new pair issued in its place."""

    session_id: str
    client_id: str
    tokens: IssuedTokens


@dataclass(frozen=True)
class ReusedRefresh:
    """A refresh token presented after it was already spent: its grant is revoked."""

    grant_id: str
    session_id: str


@dataclass(frozen=True)
class MismatchedRefresh:
    """A live refresh token presented by a client other than its grant's.

    Nothing may be spent: the caller rolls back, and the rightful client's token still
    works.
    """

    grant_id: str
    session_id: str


class RefreshRefusal(StrEnum):
    """Why a refresh token bought nothing and concerned no grant it can name."""

    # Never issued, expired, or its grant is revoked: one answer for all three, since
    # the client may be told none of them apart.
    NOT_LIVE = "not_live"


async def rotate_refresh(
    session: AsyncSession, refresh_token: str, client_id: str
) -> RotatedRefresh | ReusedRefresh | MismatchedRefresh | RefreshRefusal:
    """Spend a refresh token and issue its grant a new pair, or revoke on reuse.

    Returns: the new pair; or, for a token spent once already, the grant it revoked;
        or, for another client's token, the grant it targeted; or why nothing
        happened.

    Spending is one conditional `UPDATE`, so of two concurrent refreshes with one
    token exactly one is issued a pair. A token that matches but was already spent is
    the stolen-copy case: its grant is revoked outright, cutting off both copies. A
    mismatched client is refused before anything is written, and the caller rolls the
    spend back, so the rightful client's token still works.
    """
    token_hash = digest(refresh_token)
    spent = await session.execute(
        update(OAuthToken)
        .where(
            OAuthToken.token_hash == token_hash,
            OAuthToken.kind == OAuthTokenKind.REFRESH.value,
            OAuthToken.used_at.is_(None),
            OAuthToken.expires_at > func.now(),
            OAuthToken.grant_id == OAuthGrant.id,
            OAuthGrant.revoked_at.is_(None),
        )
        .values(used_at=func.now())
        .returning(
            OAuthGrant.id, OAuthGrant.session_id, OAuthGrant.client_id, OAuthGrant.scope
        )
    )
    row = spent.first()
    if row is not None:
        grant_id, session_id, grant_client_id, scope = row
        if grant_client_id != client_id:
            return MismatchedRefresh(grant_id=grant_id, session_id=session_id)
        access_token, new_refresh_token = await _insert_token_pair(session, grant_id)
        return RotatedRefresh(
            session_id=session_id,
            client_id=grant_client_id,
            tokens=IssuedTokens(
                grant_id=grant_id,
                access_token=access_token,
                refresh_token=new_refresh_token,
                expires_in=int(ACCESS_TOKEN_LIFETIME.total_seconds()),
                scope=scope,
            ),
        )

    reused = await session.execute(
        update(OAuthGrant)
        .where(
            OAuthGrant.id == OAuthToken.grant_id,
            OAuthToken.token_hash == token_hash,
            OAuthToken.kind == OAuthTokenKind.REFRESH.value,
            OAuthToken.used_at.is_not(None),
        )
        .values(revoked_at=func.coalesce(OAuthGrant.revoked_at, func.now()))
        .returning(OAuthGrant.id, OAuthGrant.session_id)
    )
    revoked = reused.first()
    if revoked is not None:
        return ReusedRefresh(grant_id=revoked[0], session_id=revoked[1])
    return RefreshRefusal.NOT_LIVE


class Revocation(StrEnum):
    """What asking to revoke one of a session's grants did."""

    # It was active, and this call revoked it.
    REVOKED = "revoked"
    # It was revoked before this call, which changed nothing.
    ALREADY_REVOKED = "already_revoked"
    # It is not this session's grant, or does not exist; nothing changed.
    NOT_FOUND = "not_found"


async def revoke(session: AsyncSession, session_id: str, grant_id: str) -> Revocation:
    """Revoke one of `session_id`'s grants.

    The session is in both statements' `WHERE`, so another session's grant id does not
    resolve. The write is conditional on the grant not being revoked yet, so an earlier
    revocation's moment is kept, and of two concurrent calls exactly one is `REVOKED`:
    the other waits on its row lock, then finds `revoked_at` set.
    """
    result = await session.execute(
        update(OAuthGrant)
        .where(
            OAuthGrant.id == grant_id,
            OAuthGrant.session_id == session_id,
            OAuthGrant.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
        .returning(OAuthGrant.id)
    )
    if result.first() is not None:
        return Revocation.REVOKED
    existing = await session.execute(
        select(OAuthGrant.id).where(
            OAuthGrant.id == grant_id, OAuthGrant.session_id == session_id
        )
    )
    if existing.first() is not None:
        return Revocation.ALREADY_REVOKED
    return Revocation.NOT_FOUND
