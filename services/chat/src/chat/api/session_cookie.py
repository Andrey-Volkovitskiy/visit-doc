"""`visitdoc_session_id` cookie: read/mint helpers.

`HttpOnly` keeps the value unreadable from frontend JavaScript (the frontend never
needs it - the browser attaches it automatically); `Secure=False` is deliberate for
this phase's local-HTTP-only deployment and MUST be revisited once the app is served
over HTTPS.
"""

from fastapi import Request, Response

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
        secure=False,
    )
