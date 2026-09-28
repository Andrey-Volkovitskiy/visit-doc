"""The sign-in server's steps: register, start and complete a sign-in, exchange a code.

Each step returns a typed outcome rather than raising for a refusal it expects, because
every refusal here has an answer the protocol prescribes - an error body, an error page,
a redirect - and the route renders it. Each step owns its transaction and commits or
rolls it back itself, since several of them are two conditional writes that must land
together or not at all.

Logged events name the session, the grant and the client where they are known, and
never a pairing code, an authorization code, a token or a verifier.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlencode, urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from chat.connectors.pkce import s256_matches
from chat.connectors.public_address import ConnectorConfig
from chat.core.logging import get_logger
from chat.domain.models import (
    CLIENT_NAME_LENGTH,
    CODE_CHALLENGE_LENGTH,
    MAX_FAILED_PAIRING_ATTEMPTS,
)
from chat.repositories import (
    grant_repository,
    oauth_repository,
    pairing_code_repository,
)
from chat.repositories.grant_repository import (
    IssuedTokens,
    MismatchedRefresh,
    RefreshRefusal,
    ReusedRefresh,
)

# The one return address registration accepts. It serves Claude on the web, on the
# desktop and on the phone alike.
CLAUDE_REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"
# The one thing a grant allows: reading the two counts.
CONNECTOR_SCOPE = "console:read"
# Asked for so that a refresh token is issued; it grants nothing beyond that.
OFFLINE_ACCESS_SCOPE = "offline_access"
SUPPORTED_SCOPES = (CONNECTOR_SCOPE, OFFLINE_ACCESS_SCOPE)
SUPPORTED_GRANT_TYPES = ("authorization_code", "refresh_token")
_UNNAMED_CLIENT = "Unnamed client"


# --- registration -------------------------------------------------------------------


class RegistrationError(StrEnum):
    """RFC 7591's error codes, as far as this server uses them."""

    INVALID_REDIRECT_URI = "invalid_redirect_uri"
    INVALID_CLIENT_METADATA = "invalid_client_metadata"


@dataclass(frozen=True)
class RegisteredClient:
    """A client that was registered, as the registration response describes it."""

    client_id: str
    client_name: str
    grant_types: list[str]
    response_types: list[str]
    issued_at: int

    def as_response(self) -> dict[str, Any]:
        """Render the RFC 7591 registration response. A public client has no secret."""
        return {
            "client_id": self.client_id,
            "client_id_issued_at": self.issued_at,
            "client_name": self.client_name,
            "redirect_uris": [CLAUDE_REDIRECT_URI],
            "grant_types": self.grant_types,
            "response_types": self.response_types,
            "token_endpoint_auth_method": "none",
        }


@dataclass(frozen=True)
class RegistrationRefused:
    """A registration refused, and the RFC 7591 error that says why."""

    error: RegistrationError


def _string_list(value: object, default: list[str]) -> list[str] | None:
    """Return `value` as a list of strings, `default` when absent, None if malformed."""
    if value is None:
        return default
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None
    return list(value)


@dataclass(frozen=True)
class _Registration:
    """Registration metadata that passed every check, in the form it is stored."""

    client_name: str
    grant_types: list[str]


def _validated(metadata: object) -> _Registration | RegistrationError:
    """Return `metadata` as a registration this server accepts, or why it cannot be."""
    if not isinstance(metadata, dict):
        return RegistrationError.INVALID_CLIENT_METADATA
    if metadata.get("redirect_uris") != [CLAUDE_REDIRECT_URI]:
        return RegistrationError.INVALID_REDIRECT_URI
    if metadata.get("token_endpoint_auth_method", "none") != "none":
        return RegistrationError.INVALID_CLIENT_METADATA
    name = metadata.get("client_name")
    if name is not None and not isinstance(name, str):
        return RegistrationError.INVALID_CLIENT_METADATA
    grant_types = _string_list(metadata.get("grant_types"), ["authorization_code"])
    if grant_types is None or not set(grant_types) <= set(SUPPORTED_GRANT_TYPES):
        return RegistrationError.INVALID_CLIENT_METADATA
    response_types = _string_list(metadata.get("response_types"), ["code"])
    if response_types is None or set(response_types) != {"code"}:
        return RegistrationError.INVALID_CLIENT_METADATA
    trimmed = (name or "").strip()[:CLIENT_NAME_LENGTH]
    return _Registration(
        client_name=trimmed or _UNNAMED_CLIENT, grant_types=grant_types
    )


async def register_client(
    session: AsyncSession, metadata: object
) -> RegisteredClient | RegistrationRefused:
    """Register a client from its RFC 7591 metadata, if it is one this server serves.

    Only a public client (`token_endpoint_auth_method` absent or `none`) returning to
    Claude's callback, asking for no grant beyond an authorization code and its
    refresh, is registered. Its name is trimmed to what the console can show.
    """
    registration = _validated(metadata)
    if isinstance(registration, RegistrationError):
        get_logger().info("connector.client_rejected", error=registration)
        return RegistrationRefused(error=registration)
    client = await oauth_repository.create_client(
        session,
        client_name=registration.client_name,
        redirect_uri=CLAUDE_REDIRECT_URI,
    )
    await session.commit()
    get_logger().info(
        "connector.client_registered",
        client_id=client.client_id,
        client_name=client.client_name,
    )
    return RegisteredClient(
        client_id=client.client_id,
        client_name=client.client_name,
        grant_types=registration.grant_types,
        response_types=["code"],
        issued_at=int(client.created_at.timestamp()),
    )


# --- starting a sign-in ---------------------------------------------------------------


class SignInFailureReason(StrEnum):
    """Why a sign-in step failed, as `connector.authorize_failed` logs it."""

    UNVERIFIED_CLIENT = "unverified_client"
    INVALID_REQUEST = "invalid_request"
    EXPIRED_REQUEST = "expired_request"
    TOO_MANY_ATTEMPTS = "too_many_attempts"
    BAD_CODE = "bad_code"


@dataclass(frozen=True)
class SignInPage:
    """A sign-in stored and ready: render the pairing page for it."""

    request_id: str
    client_name: str
    redirect_host: str


@dataclass(frozen=True)
class SignInRefused:
    """A sign-in whose client or redirect target could not be verified.

    Rendered as an error page and never redirected: redirecting to an unverified
    target is how an authorization server leaks to an attacker.
    """


@dataclass(frozen=True)
class SignInRedirect:
    """Send the browser back to the client, carrying the answer in the query."""

    location: str


def _redirect(redirect_uri: str, **query: str | None) -> str:
    """Return `redirect_uri` with `query`'s non-None values appended."""
    params = {key: value for key, value in query.items() if value is not None}
    return f"{redirect_uri}?{urlencode(params)}"


def _requested_scope(scope: str | None) -> str | None:
    """Return a requested scope normalized, or None if it asks for anything unknown.

    An absent or empty scope means the connector's own. The normalized form always
    holds it, whatever else was asked for.
    """
    requested = set((scope or "").split())
    if not requested <= set(SUPPORTED_SCOPES):
        return None
    requested.add(CONNECTOR_SCOPE)
    return " ".join(s for s in SUPPORTED_SCOPES if s in requested)


async def start_authorization(
    session: AsyncSession, config: ConnectorConfig, params: Mapping[str, str]
) -> SignInPage | SignInRefused | SignInRedirect:
    """Validate an `/oauth/authorize` query, and store the sign-in if it is valid.

    The client and its redirect URI are checked first, because until both are known
    good no answer may be redirected anywhere. Any other invalid parameter is answered
    at the client's redirect URI with `invalid_request` and the caller's `state`.
    """
    client_id = params.get("client_id", "")
    redirect_uri = params.get("redirect_uri", "")
    client = (
        await oauth_repository.get_client(session, client_id) if client_id else None
    )
    if client is None or redirect_uri != client.redirect_uri:
        get_logger().info(
            "connector.authorize_failed",
            reason=SignInFailureReason.UNVERIFIED_CLIENT,
            client_id=client_id or None,
        )
        return SignInRefused()

    state = params.get("state") or None
    challenge = params.get("code_challenge", "")
    scope = _requested_scope(params.get("scope"))
    resource = params.get("resource") or config.address
    if (
        params.get("response_type") != "code"
        or params.get("code_challenge_method") != "S256"
        or not challenge
        or len(challenge) > CODE_CHALLENGE_LENGTH
        or scope is None
        or resource != config.address
    ):
        get_logger().info(
            "connector.authorize_failed",
            reason=SignInFailureReason.INVALID_REQUEST,
            client_id=client.client_id,
        )
        return SignInRedirect(
            location=_redirect(redirect_uri, error="invalid_request", state=state)
        )

    request_id = await oauth_repository.create_request(
        session,
        client_id=client.client_id,
        redirect_uri=redirect_uri,
        code_challenge=challenge,
        state=state,
        scope=scope,
        resource=resource,
    )
    await session.commit()
    return SignInPage(
        request_id=request_id,
        client_name=client.client_name,
        redirect_host=urlsplit(redirect_uri).hostname or redirect_uri,
    )


# --- completing a sign-in -------------------------------------------------------------


@dataclass(frozen=True)
class SignInCompleted:
    """The code was right: redirect to the client with an authorization code."""

    location: str


@dataclass(frozen=True)
class WrongPairingCode:
    """The code was wrong, expired or used: show the page again for the same sign-in."""

    page: SignInPage


@dataclass(frozen=True)
class SignInExpired:
    """The sign-in can accept no code: unknown, expired, completed or out of tries."""


async def _page_for(session: AsyncSession, request_id: str) -> SignInPage | None:
    """Return the pairing page for a sign-in that can still accept a code."""
    request = await oauth_repository.get_live_request(session, request_id)
    if request is None:
        return None
    client = await oauth_repository.get_client(session, request.client_id)
    if client is None:
        return None
    return SignInPage(
        request_id=request_id,
        client_name=client.client_name,
        redirect_host=urlsplit(request.redirect_uri).hostname or request.redirect_uri,
    )


async def complete_authorization(
    session: AsyncSession, request_id: str, typed_code: str
) -> SignInCompleted | WrongPairingCode | SignInExpired:
    """Try a typed pairing code against a sign-in in progress.

    Completing the sign-in, spending the pairing code and storing the authorization
    code are one transaction: if either conditional write finds nothing, it rolls back
    whole, so a sign-in is never completed without a code spent, nor a code spent on a
    sign-in that did not complete. A wrong code is then counted in a transaction of its
    own, and the fifth ends the sign-in.
    """
    request = await oauth_repository.complete_request(session, request_id)
    if request is None:
        await session.rollback()
        get_logger().info(
            "connector.authorize_failed", reason=SignInFailureReason.EXPIRED_REQUEST
        )
        return SignInExpired()

    # Read before anything can roll back: a rollback expires every loaded object, and
    # an expired one cannot be read again without a query.
    client_id = request.client_id
    session_id = await pairing_code_repository.consume(session, typed_code)
    if session_id is None:
        await session.rollback()
        failures = await oauth_repository.record_failed_attempt(session, request_id)
        page = await _page_for(session, request_id)
        await session.commit()
        if failures is None or failures >= MAX_FAILED_PAIRING_ATTEMPTS or page is None:
            # A sign-in that stopped accepting codes while this one was being checked -
            # expired, or completed by a concurrent submission - counted nothing, and
            # is not one that ran out of tries.
            get_logger().info(
                "connector.authorize_failed",
                reason=(
                    SignInFailureReason.EXPIRED_REQUEST
                    if failures is None or failures < MAX_FAILED_PAIRING_ATTEMPTS
                    else SignInFailureReason.TOO_MANY_ATTEMPTS
                ),
                client_id=client_id,
            )
            return SignInExpired()
        get_logger().info(
            "connector.authorize_failed",
            reason=SignInFailureReason.BAD_CODE,
            client_id=client_id,
            failed_attempts=failures,
        )
        return WrongPairingCode(page=page)

    code = await oauth_repository.create_authorization_code(
        session,
        session_id=session_id,
        client_id=client_id,
        redirect_uri=request.redirect_uri,
        code_challenge=request.code_challenge,
        scope=request.scope,
    )
    await session.commit()
    get_logger().info(
        "connector.pairing_code_accepted",
        session_id=session_id,
        client_id=client_id,
    )
    return SignInCompleted(
        location=_redirect(request.redirect_uri, code=code, state=request.state)
    )


# --- the token endpoint ---------------------------------------------------------------


class TokenError(StrEnum):
    """RFC 6749 §5.2's error codes, as far as this server uses them."""

    INVALID_REQUEST = "invalid_request"
    INVALID_CLIENT = "invalid_client"
    INVALID_GRANT = "invalid_grant"
    UNSUPPORTED_GRANT_TYPE = "unsupported_grant_type"


class TokenRefusalReason(StrEnum):
    """This server's own name for the check that refused a token request."""

    NOT_A_FORM_BODY = "not_a_form_body"
    MISSING_FIELD = "missing_field"
    UNSUPPORTED_GRANT_TYPE = "unsupported_grant_type"
    UNKNOWN_CLIENT = "unknown_client"
    CODE_INVALID = "code_invalid"
    CLIENT_MISMATCH = "client_mismatch"
    REDIRECT_URI_MISMATCH = "redirect_uri_mismatch"
    PKCE_MISMATCH = "pkce_mismatch"
    REFRESH_REUSED = "refresh_reused"
    # `grant_repository.RefreshRefusal.NOT_LIVE`, under the same value.
    NOT_LIVE = "not_live"


@dataclass(frozen=True)
class TokenRefused:
    """A token request refused.

    `reason` is logged, never sent: the client is told only `error`, so a caller
    probing with stolen parts learns nothing from which part was wrong.
    """

    error: TokenError
    reason: TokenRefusalReason


def refuse_token(
    error: TokenError,
    reason: TokenRefusalReason,
    *,
    client_id: str | None = None,
    session_id: str | None = None,
    grant_id: str | None = None,
) -> TokenRefused:
    """Log a refused token request, naming what it concerned, and return the refusal.

    Args:
        session_id: With `grant_id`, the grant the request targeted, when known.
    """
    get_logger().info(
        "connector.token_refused",
        error=error,
        reason=reason,
        client_id=client_id,
        session_id=session_id,
        grant_id=grant_id,
    )
    return TokenRefused(error=error, reason=reason)


async def exchange_code(
    session: AsyncSession,
    *,
    code: str,
    client_id: str,
    redirect_uri: str,
    code_verifier: str,
) -> IssuedTokens | TokenRefused:
    """Exchange an authorization code for a grant and its first tokens.

    The code is spent first, then its client, redirect URI and PKCE challenge are
    checked in the same transaction; any mismatch rolls the spend back, so a failed
    exchange leaves the code as it was - worth nothing without its verifier, and gone
    in a minute anyway.
    """
    client = await oauth_repository.get_client(session, client_id)
    if client is None:
        return refuse_token(
            TokenError.INVALID_CLIENT, TokenRefusalReason.UNKNOWN_CLIENT
        )

    stored = await oauth_repository.consume_authorization_code(session, code)
    if stored is None:
        await session.rollback()
        return refuse_token(
            TokenError.INVALID_GRANT,
            TokenRefusalReason.CODE_INVALID,
            client_id=client_id,
        )
    mismatch = (
        TokenRefusalReason.CLIENT_MISMATCH
        if stored.client_id != client_id
        else TokenRefusalReason.REDIRECT_URI_MISMATCH
        if stored.redirect_uri != redirect_uri
        else TokenRefusalReason.PKCE_MISMATCH
        if not s256_matches(code_verifier, stored.code_challenge)
        else None
    )
    if mismatch is not None:
        # Read before the rollback, which expires every loaded object.
        code_session_id = stored.session_id
        await session.rollback()
        return refuse_token(
            TokenError.INVALID_GRANT,
            mismatch,
            client_id=client_id,
            session_id=code_session_id,
        )

    tokens = await grant_repository.create_grant_with_tokens(
        session,
        session_id=stored.session_id,
        client_id=client_id,
        client_name=client.client_name,
        scope=CONNECTOR_SCOPE,
    )
    await session.commit()
    get_logger().info(
        "connector.grant_created",
        session_id=stored.session_id,
        grant_id=tokens.grant_id,
        client_id=client_id,
    )
    return tokens


async def refresh(
    session: AsyncSession, *, refresh_token: str, client_id: str
) -> IssuedTokens | TokenRefused:
    """Exchange a refresh token for a new pair, replacing it.

    A refresh token spent once already revokes its whole grant: that is the only way a
    second copy of it can exist. Every refusal is `invalid_grant`, so a caller learns
    nothing from which check it failed.
    """
    outcome = await grant_repository.rotate_refresh(session, refresh_token, client_id)
    if isinstance(outcome, ReusedRefresh):
        await session.commit()
        get_logger().warning(
            "connector.refresh_reuse_detected",
            session_id=outcome.session_id,
            grant_id=outcome.grant_id,
            client_id=client_id,
        )
        return TokenRefused(
            error=TokenError.INVALID_GRANT, reason=TokenRefusalReason.REFRESH_REUSED
        )
    if isinstance(outcome, MismatchedRefresh):
        await session.rollback()
        return refuse_token(
            TokenError.INVALID_GRANT,
            TokenRefusalReason.CLIENT_MISMATCH,
            client_id=client_id,
            session_id=outcome.session_id,
            grant_id=outcome.grant_id,
        )
    if isinstance(outcome, RefreshRefusal):
        await session.rollback()
        return refuse_token(
            TokenError.INVALID_GRANT,
            TokenRefusalReason(outcome.value),
            client_id=client_id,
        )
    await session.commit()
    get_logger().info(
        "connector.token_refreshed",
        session_id=outcome.session_id,
        grant_id=outcome.tokens.grant_id,
        client_id=client_id,
    )
    return outcome.tokens
