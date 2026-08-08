"""Tests for the `visitdoc_session_id` cookie helpers (research.md #2)."""

from chat.api.session_cookie import COOKIE_NAME, read_session_id, set_session_cookie
from fastapi import Request, Response


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
    response = Response()
    set_session_cookie(response, "abc123")

    header = response.headers["set-cookie"].lower()
    assert f"{COOKIE_NAME}=abc123".lower() in header
    assert "httponly" in header
    assert "samesite=lax" in header
    assert "secure" not in header
    assert "max-age=" in header
