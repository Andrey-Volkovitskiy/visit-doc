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
from urllib.parse import unquote

import aiohttp
import pytest
import yarl
from chat.clients import scheduler_rest
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
        url: yarl.URL,
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
        url: yarl.URL,
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
    ("GET", "/console/specialties", "GET", "/specialties", None),
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
    assert str(sent.url).endswith(expected_path)


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


# --- the id is a name, never an address ----------------------------------------------


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
@pytest.mark.parametrize(
    ("encoded_id", "decoded_id"),
    [
        ("abc%3Fadmin=1", "abc?admin=1"),
        ("abc%23frag", "abc#frag"),
        ("%2E%2E", ".."),
        ("%2E", "."),
    ],
)
async def test_url_metacharacters_in_an_id_cannot_reshape_the_scheduler_path(
    method: str, encoded_id: str, decoded_id: str
) -> None:
    # Starlette hands the route its path param already unquoted, so whoever sent the
    # request chooses those characters. Interpolated raw they would be read by the HTTP
    # client's URL parser, not by this code: `?` puts an attacker's query string on an
    # internal write endpoint carrying the victim's session, `#` truncates the path
    # before the id, and `..` walks up to the collection itself.
    session_id = await _session_id()
    transport = _FakeHttpSession(status=200, payload=_PRACTITIONER)

    await _call(
        transport,
        method,
        f"/console/practitioners/{encoded_id}",
        session_id=session_id,
        body={} if method == "PATCH" else None,
    )

    # What the request line will actually carry: one segment below /practitioners,
    # with no query and no fragment, still readable as the id that was asked for.
    sent = transport.requests[0].url
    assert sent.raw_path.startswith("/practitioners/")
    segment = sent.raw_path.removeprefix("/practitioners/")
    assert "/" not in segment
    assert unquote(segment) == decoded_id
    assert sent.query_string == ""
    assert not sent.fragment


@pytest.mark.parametrize(
    "path",
    [
        "/practitioners/abc?admin=1",
        "/practitioners/abc#frag",
        "/practitioners/..",
        "/practitioners/.",
        "practitioners",
        # Characters an enumerate-the-metacharacters guard let straight through, while
        # `forward` sends the path verbatim. A space alone malforms the request line;
        # a backslash is read as `/` by some intermediaries; CR/LF and NUL have no
        # business in a URL at all; nor is a non-ASCII character an encoded path.
        "/practitioners/abc def",
        "/practitioners/a\\b",
        "/practitioners/abc\r\nX-Session-Id: other",
        "/practitioners/abc\x00d",
        "/practitioners/caf\u00e9",
        # A `%` the allow-list has to admit for `path_segment`'s escapes, but which
        # here begins no escape at all - so the string is not an encoded path, and what
        # an intermediary makes of the stray `%` is nobody's decision here.
        "/practitioners/a%",
        "/practitioners/%zz",
        "/practitioners/%2",
    ],
)
async def test_the_transport_refuses_a_path_that_is_not_one(path: str) -> None:
    # The encoding above is a caller's discipline, and a future caller can forget it.
    # This is the transport's own promise that a path is a path: it fails here, having
    # sent nothing, rather than addressing whatever the string turned out to name.
    transport = _FakeHttpSession(status=200, payload=_PRACTITIONER)

    with pytest.raises(ValueError):
        await scheduler_rest.forward(
            transport,  # type: ignore[arg-type]
            "http://scheduler:8001",
            "PATCH",
            path,
            "01SESSION000000000000000000",
        )

    assert transport.requests == []


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
