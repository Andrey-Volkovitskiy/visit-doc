"""`visitdoc_session_id` cookie: read/mint helpers.

`HttpOnly` keeps the value unreadable from frontend JavaScript (the frontend never
needs it - the browser attaches it automatically). `Secure` follows
`SESSION_COOKIE_SECURE`: set where the app is served over HTTPS, so the session id
never crosses the network in the clear, and unset where it is served over plain HTTP,
which would otherwise never get the cookie back.
"""

from fastapi import Request, Response

from chat.core.config import get_settings

COOKIE_NAME = "visitdoc_session_id"
_MAX_AGE_SECONDS = 400 * 24 * 60 * 60  # ~400 days, the practical browser-enforced cap


def read_session_id(request: Request) -> str | None:
    """Return the visitor's session id from the request cookie, or None if absent."""
    return request.cookies.get(COOKIE_NAME)


def set_session_cookie(response: Response, session_id: str) -> None:
    """Mint the session cookie on `response`.

    Only called when a new `Session` was just created - never reissued for an
    existing session, including across a `DELETE /chat`.
    """
    response.set_cookie(
        COOKIE_NAME,
        session_id,
        max_age=_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=get_settings().SESSION_COOKIE_SECURE,
    )
