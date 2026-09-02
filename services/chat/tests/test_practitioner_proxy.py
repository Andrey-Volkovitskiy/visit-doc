"""`/console/practitioners` - a proxy, and the two things a proxy must not do.

It must not re-implement a rule: every default, every refusal and every status code is
the scheduler's, because the scheduler owns practitioners. And it must not retry: a
console form that quietly sent a create twice would leave two practitioners where the
staff member asked for one, so an outcome nobody knows is reported as unknown.

The session credential never leaves the server. It lives in an `HttpOnly` cookie the
page cannot read, and the scheduler's admin surface wants it as a header - which is the
whole reason this proxy exists rather than the browser calling that surface itself.
"""

import json
from typing import Any, Self
from unittest.mock import patch

import aiohttp
import pytest
from chat.db.session import engine, session_factory
from chat.main import app
from chat.repositories import chat_repository
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient, Response

from .conftest import fake_anthropic_client

_PRACTITIONER = {
    "id": "01PRACT0000000000000000000",
    "full_name": "Dr. Ada Lovelace",
    "specialty": "general_practice",
    "appointment_duration_minutes": 30,
    "schedule": [],
}


class _RecordedRequest:
    """One call the proxy made, as the transport saw it."""

    def __init__(
        self,
        method: str,
        url: str,
        body: Any | None,
        headers: dict[str, str],
        timeout: aiohttp.ClientTimeout | None,
    ) -> None:
        self.method = method
        self.url = url
        self.body = body
        self.headers = headers
        self.timeout = timeout


class _FakeResponse:
    def __init__(self, status: int, payload: Any | None) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def json(self, content_type: str | None = None) -> Any:
        if self._payload is None:
            raise aiohttp.ContentTypeError(None, ())  # type: ignore[arg-type]
        return self._payload


class _FakeHttpSession:
    """Stands in for the shared `aiohttp.ClientSession` the proxy sends over.

    Records every request, so a test can assert what actually crossed the boundary -
    the method, the path, and the header carrying the session - rather than asserting
    against a return value it supplied itself.
    """

    def __init__(
        self,
        status: int = 200,
        payload: Any | None = None,
        error: Exception | None = None,
    ) -> None:
        self.requests: list[_RecordedRequest] = []
        self._status = status
        self._payload = payload
        self._error = error

    def request(
        self,
        method: str,
        url: str,
        *,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        timeout: aiohttp.ClientTimeout | None = None,
    ) -> _FakeResponse:
        self.requests.append(
            _RecordedRequest(method, url, json, headers or {}, timeout)
        )
        if self._error is not None:
            raise self._error
        return _FakeResponse(self._status, self._payload)


async def _session_id() -> str:
    async with session_factory() as session:
        session_row = await chat_repository.create_session(session)
    await engine.dispose()
    return session_row.id


async def _call(
    transport: _FakeHttpSession,
    method: str,
    path: str,
    *,
    session_id: str | None,
    body: Any | None = None,
) -> Response:
    """Drive one console practitioner route over `transport`."""
    await engine.dispose()
    with patch("chat.main.AsyncAnthropic") as mock_anthropic_cls:
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app):
            # Replaces the real pool the lifespan built, after it exists: the proxy
            # reads it off app state per request, exactly as it does in production.
            app.state.http_session = transport
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                if session_id is not None:
                    http.cookies.set("visitdoc_session_id", session_id)
                return await http.request(method, path, json=body)


_ROUTES = [
    ("GET", "/console/practitioners", "GET", "/practitioners", None),
    ("POST", "/console/practitioners", "POST", "/practitioners", {}),
    (
        "PATCH",
        "/console/practitioners/01PRACT0000000000000000000",
        "PATCH",
        "/practitioners/01PRACT0000000000000000000",
        {"full_name": "Dr. Grace Hopper"},
    ),
    (
        "DELETE",
        "/console/practitioners/01PRACT0000000000000000000",
        "DELETE",
        "/practitioners/01PRACT0000000000000000000",
        None,
    ),
]


# --- what crosses the boundary ------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "expected_method", "expected_path", "body"), _ROUTES
)
async def test_every_route_forwards_to_the_service_that_owns_practitioners(
    method: str,
    path: str,
    expected_method: str,
    expected_path: str,
    body: Any | None,
) -> None:
    session_id = await _session_id()
    transport = _FakeHttpSession(status=200, payload=[_PRACTITIONER])

    await _call(transport, method, path, session_id=session_id, body=body)

    assert len(transport.requests) == 1
    sent = transport.requests[0]
    assert sent.method == expected_method
    assert sent.url.endswith(expected_path)


@pytest.mark.parametrize(
    ("method", "path", "expected_method", "expected_path", "body"), _ROUTES
)
async def test_every_route_carries_the_cookie_session_as_the_header(
    method: str,
    path: str,
    expected_method: str,
    expected_path: str,
    body: Any | None,
) -> None:
    # The one thing the browser cannot do for itself, and the reason this proxy exists.
    session_id = await _session_id()
    transport = _FakeHttpSession(status=200, payload=[_PRACTITIONER])

    await _call(transport, method, path, session_id=session_id, body=body)

    assert transport.requests[0].headers["X-Session-Id"] == session_id


async def test_a_request_body_is_relayed_unchanged() -> None:
    # Not re-validated, not normalized, not defaulted: the shape and the rules are the
    # scheduler's, so anything this side did to them would be a second copy.
    session_id = await _session_id()
    transport = _FakeHttpSession(status=201, payload=_PRACTITIONER)
    body = {
        "full_name": "Dr. Grace Hopper",
        "specialty": "cardiology",
        "schedule": [{"weekday": "monday", "start_time": "09:00", "end_time": "17:00"}],
    }

    await _call(
        transport, "POST", "/console/practitioners", session_id=session_id, body=body
    )

    assert transport.requests[0].body == body


async def test_a_response_body_is_relayed_unchanged() -> None:
    session_id = await _session_id()
    transport = _FakeHttpSession(status=200, payload=[_PRACTITIONER])

    response = await _call(
        transport, "GET", "/console/practitioners", session_id=session_id
    )

    assert response.json() == [_PRACTITIONER]


async def test_an_empty_create_is_valid_and_reaches_the_scheduler() -> None:
    # Every field defaulted, including the pool-assigned name. A console that supplied
    # its own defaults would be re-implementing the thing this route forwards to.
    session_id = await _session_id()
    transport = _FakeHttpSession(status=201, payload=_PRACTITIONER)

    response = await _call(
        transport, "POST", "/console/practitioners", session_id=session_id, body=None
    )

    assert response.status_code == 201
    assert transport.requests[0].body == {}


@pytest.mark.parametrize(
    ("status", "detail"),
    [
        (409, "another practitioner in this session already has that name"),
        (422, "working ranges on one weekday must not overlap"),
        (404, "practitioner not found"),
    ],
)
async def test_the_schedulers_own_refusals_reach_the_caller(
    status: int, detail: str
) -> None:
    # FR-035/SC-013: a refusal is an answer, and its wording belongs to the service
    # that decided it. Rewriting one here would be inventing a reason.
    session_id = await _session_id()
    transport = _FakeHttpSession(status=status, payload={"detail": detail})

    response = await _call(
        transport,
        "POST",
        "/console/practitioners",
        session_id=session_id,
        body={"full_name": "Dr. Ada Lovelace"},
    )

    assert response.status_code == status
    assert response.json() == {"detail": detail}


async def test_a_delete_relays_the_schedulers_empty_success() -> None:
    session_id = await _session_id()
    transport = _FakeHttpSession(status=204, payload=None)

    response = await _call(
        transport,
        "DELETE",
        "/console/practitioners/01PRACT0000000000000000000",
        session_id=session_id,
    )

    assert response.status_code == 204
    assert response.content == b""


# --- transport failures, which the scheduler cannot report ---------------------------


@pytest.mark.parametrize(
    ("method", "path", "expected_method", "expected_path", "body"), _ROUTES
)
async def test_an_unreachable_scheduler_is_reported_as_having_changed_nothing(
    method: str,
    path: str,
    expected_method: str,
    expected_path: str,
    body: Any | None,
) -> None:
    session_id = await _session_id()
    transport = _FakeHttpSession(error=aiohttp.ClientError("connection refused"))

    response = await _call(transport, method, path, session_id=session_id, body=body)

    assert response.status_code == 503
    assert "nothing was changed" in response.json()["detail"]


@pytest.mark.parametrize(
    ("method", "path", "expected_method", "expected_path", "body"), _ROUTES
)
async def test_a_timed_out_scheduler_is_reported_as_an_unknown_outcome(
    method: str,
    path: str,
    expected_method: str,
    expected_path: str,
    body: Any | None,
) -> None:
    # A deadline is the caller's, not the callee's: it expiring means the answer did
    # not arrive, not that the work did not happen.
    session_id = await _session_id()
    transport = _FakeHttpSession(error=TimeoutError("no answer"))

    response = await _call(transport, method, path, session_id=session_id, body=body)

    assert response.status_code == 504
    detail = response.json()["detail"]
    assert "may not have been applied" in detail
    assert "try again" in detail


@pytest.mark.parametrize(
    ("method", "path", "expected_method", "expected_path", "body"), _ROUTES
)
async def test_exactly_one_attempt_is_made_on_every_route(
    method: str,
    path: str,
    expected_method: str,
    expected_path: str,
    body: Any | None,
) -> None:
    # A retried POST would create two practitioners. There is no route here where a
    # second attempt is safe, so no route retries.
    session_id = await _session_id()
    transport = _FakeHttpSession(error=TimeoutError("no answer"))

    await _call(transport, method, path, session_id=session_id, body=body)

    assert len(transport.requests) == 1


async def test_the_request_carries_a_deadline() -> None:
    # Without one, an unresponsive scheduler holds a console form open indefinitely -
    # and the one attempt this makes would never end.
    session_id = await _session_id()
    transport = _FakeHttpSession(status=200, payload=[])

    await _call(transport, "GET", "/console/practitioners", session_id=session_id)

    timeout = transport.requests[0].timeout
    assert timeout is not None
    assert timeout.total is not None
    assert 0 < timeout.total <= 10


# --- the credential the browser never holds ------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "expected_method", "expected_path", "body"), _ROUTES
)
async def test_no_response_ever_contains_the_session_id(
    method: str,
    path: str,
    expected_method: str,
    expected_path: str,
    body: Any | None,
) -> None:
    # SC-012: the session is a bearer credential. A response echoing it would hand the
    # page the one thing the `HttpOnly` cookie exists to keep from it.
    session_id = await _session_id()
    transport = _FakeHttpSession(status=200, payload=[_PRACTITIONER])

    response = await _call(transport, method, path, session_id=session_id, body=body)

    assert session_id not in response.text
    assert session_id not in json.dumps(dict(response.headers))


@pytest.mark.parametrize(
    ("method", "path", "expected_method", "expected_path", "body"), _ROUTES
)
async def test_a_request_with_no_session_sends_nothing_at_all(
    method: str,
    path: str,
    expected_method: str,
    expected_path: str,
    body: Any | None,
) -> None:
    # There is no session to act for, and forwarding without one would ask the
    # scheduler to decide something this side already knows the answer to.
    transport = _FakeHttpSession(status=200, payload=[])

    response = await _call(transport, method, path, session_id=None, body=body)

    assert response.status_code == 401
    assert transport.requests == []
