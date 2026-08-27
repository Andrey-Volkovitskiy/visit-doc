"""Shared FastAPI dependency helpers for the admin routes.

Authorization here *is* the session id. The chat service mints it from `os.urandom`
entropy specifically so it can act as a bearer credential, and every handler scopes its
query to it - so a row belonging to another session is indistinguishable from one that
does not exist, without this phase inventing an auth system it does not have.
"""

from typing import Annotated

from fastapi import Header, HTTPException

SESSION_HEADER = "X-Session-Id"


async def require_session_id(
    x_session_id: Annotated[str | None, Header()] = None,
) -> str:
    """Return the caller's session id from `X-Session-Id`.

    Raises: HTTPException 401 if the header is absent or empty.
    """
    if not x_session_id:
        raise HTTPException(status_code=401, detail=f"{SESSION_HEADER} is required")
    return x_session_id
