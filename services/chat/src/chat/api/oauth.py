"""The sign-in server Claude connects through: discovery, register, authorize, token.

OAuth 2.1 as Claude's connectors require it - public clients, PKCE S256, one scope,
dynamic registration restricted to Claude's callback - with the pairing code as the
one step that proves who is connecting. The logic is `chat.connectors.authorization`'s;
these routes read the request and render the answer the protocol prescribes.

While the connector is unavailable every route answers `503` with the reason: metadata
naming an issuer that does not exist would send Claude somewhere it cannot reach.

Kept out of the published schema: these are a protocol surface, described by the
discovery document rather than by the SPA's API.
"""

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from chat.connectors import authorization
from chat.connectors.authorization import (
    SUPPORTED_GRANT_TYPES,
    SUPPORTED_SCOPES,
    RegistrationRefused,
    SignInCompleted,
    SignInPage,
    SignInRedirect,
    SignInRefused,
    TokenError,
    TokenRefusalReason,
    TokenRefused,
    WrongPairingCode,
    refuse_token,
)
from chat.connectors.public_address import (
    ConnectorConfig,
    ConnectorUnavailable,
    current_connector,
)
from chat.db.session import session_factory
from chat.domain.schemas import UnavailableReason
from chat.repositories.grant_repository import IssuedTokens
from chat.repositories.pairing_code_repository import PAIRING_CODE_LIFETIME

router = APIRouter(include_in_schema=False)

# Autoescaping on for every template: every value these pages show came from the
# caller, and the page asks for a secret.
_TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).parents[1] / "connectors" / "templates"),
    autoescape=select_autoescape(default=True, default_for_string=True),
)
_NOT_VALID = (
    "That code is not valid. Get a new one from the Staff Console if it has expired."
)
_EXPIRED_HEADING = "This sign-in has expired"
# The same three reasons the console's tab words, for whoever runs this service.
_UNAVAILABLE_REASON = {
    UnavailableReason.NOT_CONFIGURED: (
        "its public address (PUBLIC_BASE_URL) is not set."
    ),
    UnavailableReason.NOT_HTTPS: (
        "its public address (PUBLIC_BASE_URL) is not an https:// address."
    ),
    UnavailableReason.HAS_PATH: (
        "its public address (PUBLIC_BASE_URL) must be an origin only, with no path."
    ),
}
_EXPIRED = "This sign-in has expired. Start again from Claude."
# What the page tells a person about the code they are typing, from the lifetime the
# code is issued with rather than a second copy of it.
_PAIRING_CODE_MINUTES = int(PAIRING_CODE_LIFETIME.total_seconds() // 60)
# Token responses carry credentials, so no cache may keep one (RFC 6749 §5.1).
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
# One description per error code, never per refusal reason: which check failed is
# logged for whoever runs this service and withheld from the caller.
_TOKEN_ERROR_DESCRIPTION = {
    TokenError.INVALID_REQUEST: "The token request is malformed.",
    TokenError.INVALID_CLIENT: "The client is not registered.",
    TokenError.INVALID_GRANT: "The code or refresh token is not valid.",
    TokenError.UNSUPPORTED_GRANT_TYPE: (
        "Only the authorization_code and refresh_token grants are supported."
    ),
}
_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
# The pairing page asks for a secret, so no other site may frame it: a page shown
# invisibly inside another could collect a code typed by someone who cannot see it.
_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
}


def unavailable_response(unavailable: ConnectorUnavailable) -> JSONResponse:
    """The fixed `503` every connector route answers with while unavailable."""
    return JSONResponse(
        status_code=503,
        content={
            "error": "temporarily_unavailable",
            "error_description": unavailable.reason.value,
        },
    )


def _page(template: str, status_code: int, **context: object) -> HTMLResponse:
    """Render one of the sign-in pages, uncached and unframeable."""
    return HTMLResponse(
        _TEMPLATES.get_template(template).render(**context),
        status_code=status_code,
        headers=_PAGE_HEADERS,
    )


def _pairing_page(page: SignInPage, message: str | None = None) -> HTMLResponse:
    """Render the page asking for the pairing code, with `message` above the form."""
    return _page(
        "authorize.html",
        200,
        client_name=page.client_name,
        redirect_host=page.redirect_host,
        request_id=page.request_id,
        message=message,
        code_lifetime_minutes=_PAIRING_CODE_MINUTES,
    )


def _error_page(heading: str, message: str, status_code: int = 400) -> HTMLResponse:
    """Render a sign-in that cannot continue, saying why."""
    return _page("error.html", status_code, heading=heading, message=message)


def _unavailable_page(unavailable: ConnectorUnavailable) -> HTMLResponse:
    """Render the `503` a person sees at either sign-in page while it is unavailable."""
    return _error_page(
        "Connecting is unavailable",
        "VisitDoc cannot accept connections right now: "
        f"{_UNAVAILABLE_REASON[unavailable.reason]}",
        status_code=503,
    )


def _metadata(config: ConnectorConfig) -> dict[str, Any]:
    """Return the RFC 8414 document for `config`'s issuer.

    `client_id_metadata_document_supported` is left out on purpose, so Claude registers
    dynamically.
    """
    return {
        "issuer": config.issuer,
        "authorization_endpoint": f"{config.issuer}/oauth/authorize",
        "token_endpoint": f"{config.issuer}/oauth/token",
        "registration_endpoint": f"{config.issuer}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": list(SUPPORTED_GRANT_TYPES),
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": list(SUPPORTED_SCOPES),
    }


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata() -> Response:
    """Publish where Claude registers, signs in and exchanges codes."""
    config = current_connector()
    if isinstance(config, ConnectorUnavailable):
        return unavailable_response(config)
    return JSONResponse(_metadata(config))


@router.post("/oauth/register")
async def register(request: Request) -> Response:
    """Register a client (RFC 7591), if it is a public client returning to Claude."""
    config = current_connector()
    if isinstance(config, ConnectorUnavailable):
        return unavailable_response(config)
    try:
        metadata: object = await request.json()
    except ValueError:
        metadata = None
    async with session_factory() as session:
        outcome = await authorization.register_client(session, metadata)
    if isinstance(outcome, RegistrationRefused):
        return JSONResponse(status_code=400, content={"error": outcome.error.value})
    return JSONResponse(status_code=201, content=outcome.as_response())


@router.get("/oauth/authorize")
async def authorize_page(request: Request) -> Response:
    """Validate Claude's sign-in request and ask for the pairing code."""
    config = current_connector()
    if isinstance(config, ConnectorUnavailable):
        return _unavailable_page(config)
    async with session_factory() as session:
        outcome = await authorization.start_authorization(
            session, config, request.query_params
        )
    if isinstance(outcome, SignInRefused):
        return _error_page(
            "This sign-in cannot continue",
            "The app asking to connect is not one VisitDoc knows, or asked to be "
            "answered somewhere it did not register. Start again from Claude.",
        )
    if isinstance(outcome, SignInRedirect):
        return RedirectResponse(outcome.location, status_code=302)
    return _pairing_page(outcome)


@router.post("/oauth/authorize")
async def authorize_submit(
    request_id: Annotated[str | None, Form(alias="request")] = None,
    code: Annotated[str | None, Form()] = None,
) -> Response:
    """Check the typed pairing code, and send Claude its authorization code if right.

    Answered with a page, never a JSON body, whatever happens: only the pairing page's
    form posts here, so a person is reading the answer.
    """
    config = current_connector()
    if isinstance(config, ConnectorUnavailable):
        return _unavailable_page(config)
    if not request_id or code is None:
        return _error_page(_EXPIRED_HEADING, _EXPIRED)
    async with session_factory() as session:
        outcome = await authorization.complete_authorization(session, request_id, code)
    if isinstance(outcome, SignInCompleted):
        return RedirectResponse(outcome.location, status_code=302)
    if isinstance(outcome, WrongPairingCode):
        return _pairing_page(outcome.page, message=_NOT_VALID)
    return _error_page(_EXPIRED_HEADING, _EXPIRED)


def _token_error(refused: TokenRefused) -> JSONResponse:
    """Render a refused token request as RFC 6749 §5.2's error body.

    The description is the error code's, so it says nothing the code does not.
    """
    return JSONResponse(
        status_code=400,
        content={
            "error": refused.error.value,
            "error_description": _TOKEN_ERROR_DESCRIPTION[refused.error],
        },
        headers=_NO_STORE,
    )


def _token_success(tokens: IssuedTokens) -> JSONResponse:
    """Render a new token pair as RFC 6749 §5.1's success body, uncached."""
    return JSONResponse(
        {
            "access_token": tokens.access_token,
            "token_type": "Bearer",
            "expires_in": tokens.expires_in,
            "refresh_token": tokens.refresh_token,
            "scope": tokens.scope,
        },
        headers=_NO_STORE,
    )


@router.post("/oauth/token")
async def token(request: Request) -> Response:
    """Exchange an authorization code, or a refresh token, for a token pair.

    Only a form body is read, as RFC 6749 requires; a JSON body is `invalid_request`.
    """
    config = current_connector()
    if isinstance(config, ConnectorUnavailable):
        return unavailable_response(config)
    # A media type is case-insensitive and may carry parameters (`; charset=utf-8`),
    # so the type itself is compared whole, never a prefix of the header.
    media_type = request.headers.get("content-type", "").split(";")[0]
    if media_type.strip().lower() != _FORM_CONTENT_TYPE:
        return _token_error(
            refuse_token(TokenError.INVALID_REQUEST, TokenRefusalReason.NOT_A_FORM_BODY)
        )
    form = await request.form()
    fields = {key: value for key, value in form.items() if isinstance(value, str)}
    grant_type = fields.get("grant_type")

    if grant_type == "authorization_code":
        required = ("code", "redirect_uri", "client_id", "code_verifier")
        if any(not fields.get(name) for name in required):
            return _token_error(
                refuse_token(
                    TokenError.INVALID_REQUEST, TokenRefusalReason.MISSING_FIELD
                )
            )
        async with session_factory() as session:
            outcome = await authorization.exchange_code(
                session,
                code=fields["code"],
                client_id=fields["client_id"],
                redirect_uri=fields["redirect_uri"],
                code_verifier=fields["code_verifier"],
            )
    elif grant_type == "refresh_token":
        if not fields.get("refresh_token") or not fields.get("client_id"):
            return _token_error(
                refuse_token(
                    TokenError.INVALID_REQUEST, TokenRefusalReason.MISSING_FIELD
                )
            )
        async with session_factory() as session:
            outcome = await authorization.refresh(
                session,
                refresh_token=fields["refresh_token"],
                client_id=fields["client_id"],
            )
    else:
        return _token_error(
            refuse_token(
                TokenError.UNSUPPORTED_GRANT_TYPE,
                TokenRefusalReason.UNSUPPORTED_GRANT_TYPE,
            )
        )

    if isinstance(outcome, TokenRefused):
        return _token_error(outcome)
    return _token_success(outcome)
