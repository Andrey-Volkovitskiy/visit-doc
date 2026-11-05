"""Tests for the `visitdoc_session_id` cookie helpers (research.md #2)."""

from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import patch

from chat.api import session_cookie
from chat.api.session_cookie import COOKIE_NAME, read_session_id, set_session_cookie
from chat.core.config import Settings
from fastapi import Request, Response


@contextmanager
def _cookie_secure(secure: bool) -> Generator[None]:
    """Run the block with `SESSION_COOKIE_SECURE` set to `secure`."""
    settings = Settings().model_copy(update={"SESSION_COOKIE_SECURE": secure})
    with patch.object(session_cookie, "get_settings", return_value=settings):
        yield


def _minted_cookie_header() -> str:
    response = Response()
    set_session_cookie(response, "abc123")
    return response.headers["set-cookie"].lower()


def _request_with_cookie(value: str | None) -> Request:
    if value is None:
        return Request({"type": "http", "headers": []})
    cookie_header = f"{COOKIE_NAME}={value}".encode()
    return Request({"type": "http", "headers": [(b"cookie", cookie_header)]})


def test_read_session_id_returns_none_when_cookie_missing() -> None:
    assert read_session_id(_request_with_cookie(None)) is None


def test_read_session_id_returns_cookie_value_when_present() -> None:
    assert read_session_id(_request_with_cookie("abc123")) == "abc123"


def test_set_session_cookie_sets_expected_attributes() -> None:
    header = _minted_cookie_header()

    assert f"{COOKIE_NAME}=abc123".lower() in header
    assert "httponly" in header
    assert "samesite=lax" in header
    assert "max-age=" in header


def test_the_cookie_is_secure_where_the_app_is_served_over_https() -> None:
    with _cookie_secure(True):
        header = _minted_cookie_header()

    assert "; secure" in header


def test_the_cookie_is_not_secure_where_the_app_is_served_over_plain_http() -> None:
    with _cookie_secure(False):
        header = _minted_cookie_header()

    assert "secure" not in header
