"""`/admin/sessions` — the guard, and the four properties whose defaults are all wrong.

This is a maintenance surface on a public app. Nothing links to it, nothing in the
browser calls it, and it is not a user role: patients and staff still never log in.
What protects it is one secret and four properties, each of which is easy to get
backwards -

1. carried in a **header**, never a query string or a path segment;
2. compared in **constant time**;
3. absent from the published schema, declared on the route itself;
4. **fail-closed** when the configured secret is unset or empty, checked *before* the
   comparison - an empty configured secret would otherwise match an empty header.

And one rule about what a refusal says: nothing. Not which part was wrong, not how much
of the secret was right, and never the secret itself.
"""

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest
from chat.api import admin
from chat.clients.scheduling import (
    SchedulingError,
    SchedulingNotFoundError,
    SchedulingRequestError,
    SchedulingUnavailableError,
    SessionPurge,
)
from chat.core.config import Settings
from chat.db.session import engine, session_factory
from chat.domain.models import MessageSender
from chat.main import app
from chat.repositories import chat_repository, faq_repository
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient, Response
from structlog.testing import capture_logs
from ulid import ULID

from .conftest import fake_anthropic_client

_SECRET = "a-secret-nobody-should-see-in-a-log"
# A passphrase an operator might plausibly pick. It reaches the guard as latin-1-decoded
# mojibake rather than as the text written here, which is the whole point of the pair of
# tests below.
_NON_ASCII_SECRET = "café-Grüße-пароль"


def _sent_as_utf8(secret: str) -> dict[str, str | bytes]:
    """The header a real client puts on the wire for `secret`, as raw UTF-8 bytes."""
    return {"X-Admin-Secret": secret.encode("utf-8")}


def _settings(secret: str) -> Settings:
    base = Settings()
    return base.model_copy(update={"ADMIN_SECRET": secret})


async def _session_id() -> str:
    async with session_factory() as session:
        session_row = await chat_repository.create_session(session)
    await engine.dispose()
    return session_row.id


# Every route on the admin surface, as the router declares it, mapped to the path a
# request to it takes. The guard is a property of the surface rather than of any one
# route, so the tests below run against all of them, and the first test below asserts
# these keys are exactly the routes the admin router declares - so a route added
# without an entry here fails there instead of shipping open.
_PATH_BY_ROUTE = {
    ("DELETE", "/admin/sessions"): "/admin/sessions",
    ("DELETE", "/admin/sessions/{session_id}"): "/admin/sessions/01WHATEVER",
    ("GET", "/admin/sessions"): "/admin/sessions",
}
_ROUTES = [(method, path) for (method, _), path in _PATH_BY_ROUTE.items()]


async def _seed_one_chat_and_entry(session_id: str) -> None:
    """Give `session_id` one chat holding one message, and one FAQ entry."""
    async with session_factory() as session:
        chat = await chat_repository.create_chat(session, session_id)
        await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=chat.id,
            session_id=session_id,
            sender=MessageSender.PATIENT,
            content="hi",
        )
        await faq_repository.create(session, session_id, "policy", str(ULID()))
    await engine.dispose()


async def _call(
    path: str,
    *,
    method: str = "DELETE",
    configured: str = _SECRET,
    headers: dict[str, str | bytes] | None = None,
    params: dict[str, str] | None = None,
) -> Response:
    """Send one admin request, with `configured` as the deployment's secret."""
    await engine.dispose()
    with (
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch("chat.api.admin.get_settings", return_value=_settings(configured)),
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                return await http.request(
                    method, path, headers=headers or {}, params=params
                )


# --- the surface those properties are asserted over -----------------------------------


def test_the_guard_tests_run_against_every_route_the_admin_router_declares() -> None:
    """`_PATH_BY_ROUTE` is checked against the router, not maintained beside it.

    Read off the router rather than off the app or the published schema: neither can
    see these routes. `include_router` leaves a wrapper on `app.routes` rather than the
    `APIRoute`s themselves, and every route here is declared `include_in_schema=False`.
    """
    declared = {
        (method, route.path)
        for route in admin.router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }

    assert declared == set(_PATH_BY_ROUTE)


# --- the four properties -------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), _ROUTES)
async def test_the_secret_is_read_from_the_header(method: str, path: str) -> None:
    response = await _call(path, method=method, headers={"X-Admin-Secret": _SECRET})

    assert response.status_code == 200


@pytest.mark.parametrize(("method", "path"), _ROUTES)
async def test_the_secret_is_never_accepted_from_a_query_string(
    method: str, path: str
) -> None:
    # A query string reaches access logs and browser history, where the redaction that
    # covers a log event does not follow.
    response = await _call(
        path, method=method, params={"secret": _SECRET, "admin_secret": _SECRET}
    )

    assert response.status_code == 403


async def test_the_secret_is_never_accepted_from_a_path_segment() -> None:
    response = await _call(f"/admin/sessions/{_SECRET}")

    assert response.status_code == 403


@pytest.mark.parametrize(("method", "path"), _ROUTES)
async def test_the_comparison_is_constant_time(method: str, path: str) -> None:
    # A refusal that returned faster for a wrong first character would say how much of
    # the secret was right, one request at a time.
    with patch("chat.api.admin.hmac.compare_digest", return_value=True) as compare:
        await _call(path, method=method, headers={"X-Admin-Secret": "wrong"})

    compare.assert_called_once()


async def test_no_admin_route_appears_in_the_published_schema() -> None:
    # Declared on the decorators: a router cannot retroactively hide its routes from
    # `/openapi.json`.
    await engine.dispose()
    with patch("chat.main.AsyncAnthropic") as mock_anthropic_cls:
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app) as client:
            schema = client.get("/openapi.json").json()

    assert not [path for path in schema["paths"] if path.startswith("/admin")]


@pytest.mark.parametrize("configured", ["", "   "])
@pytest.mark.parametrize(("method", "path"), _ROUTES)
async def test_an_unconfigured_secret_refuses_every_request(
    method: str, path: str, configured: str
) -> None:
    # A deployment that has not configured one has no admin, not an open door.
    empty_header = await _call(path, method=method, configured=configured, headers={})
    matching_header = await _call(
        path,
        method=method,
        configured=configured,
        headers={"X-Admin-Secret": configured},
    )
    anything = await _call(
        path,
        method=method,
        configured=configured,
        headers={"X-Admin-Secret": "anything"},
    )

    assert empty_header.status_code == 403
    assert matching_header.status_code == 403
    assert anything.status_code == 403


async def test_the_emptiness_check_happens_before_the_comparison() -> None:
    # An empty configured secret would `compare_digest`-match an empty header and admit
    # everybody, so the order here is the guard rather than an implementation detail.
    with patch("chat.api.admin.hmac.compare_digest", return_value=True) as compare:
        response = await _call(
            "/admin/sessions", configured="", headers={"X-Admin-Secret": ""}
        )

    assert response.status_code == 403
    compare.assert_not_called()


# --- what a refusal says --------------------------------------------------------------


@pytest.mark.parametrize(
    ("configured", "headers"),
    [
        (_SECRET, {}),
        (_SECRET, {"X-Admin-Secret": "wrong"}),
        (_SECRET, {"X-Admin-Secret": _SECRET[:-1]}),
        ("", {"X-Admin-Secret": _SECRET}),
    ],
)
async def test_every_refusal_is_the_identical_answer(
    configured: str, headers: dict[str, str]
) -> None:
    response = await _call("/admin/sessions", configured=configured, headers=headers)

    assert response.status_code == 403
    assert response.json() == {"detail": "refused"}


@pytest.mark.parametrize(("method", "path"), _ROUTES)
async def test_a_non_ascii_secret_is_accepted(method: str, path: str) -> None:
    # `compare_digest` refuses a non-ASCII `str` outright, so a comparison made over
    # text would fail every request an operator with an accented passphrase makes -
    # the correct ones included.
    response = await _call(
        path,
        method=method,
        configured=_NON_ASCII_SECRET,
        headers=_sent_as_utf8(_NON_ASCII_SECRET),
    )

    assert response.status_code == 200


@pytest.mark.parametrize(("method", "path"), _ROUTES)
async def test_a_non_ascii_wrong_secret_is_refused_like_any_other(
    method: str, path: str
) -> None:
    # And refused, not crashed into: an answer that differs from every other refusal
    # tells a prober their header was read.
    with capture_logs() as logs:
        response = await _call(
            path, method=method, headers=_sent_as_utf8("café-but-wrong")
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "refused"}
    assert [e["event"] for e in logs if e["event"] == "admin.refused"]


async def test_a_refusal_records_only_which_route_it_was() -> None:
    # Not the supplied value, not its length, and not which of the three causes it was:
    # each of those tells somebody probing how close they are.
    with capture_logs() as logs:
        await _call("/admin/sessions", headers={"X-Admin-Secret": "wrong"})

    refused = next(e for e in logs if e["event"] == "admin.refused")
    assert set(refused) - {"log_level"} == {"event", "route"}


async def test_the_secret_never_appears_in_a_response_or_a_log() -> None:
    with capture_logs() as logs:
        refused = await _call(
            "/admin/sessions", headers={"X-Admin-Secret": _SECRET[:-1]}
        )
        accepted = await _call("/admin/sessions", headers={"X-Admin-Secret": _SECRET})

    assert _SECRET not in refused.text
    assert _SECRET not in accepted.text
    assert _SECRET not in str(logs)
    assert _SECRET[:-1] not in str(logs)


# --- what an accepted request does ----------------------------------------------------


async def test_the_listing_reports_every_session_oldest_first() -> None:
    first = await _session_id()
    second = await _session_id()

    response = await _call(
        "/admin/sessions", method="GET", headers={"X-Admin-Secret": _SECRET}
    )

    listed = response.json()["sessions"]
    assert [s["session_id"] for s in listed] == [first, second]


async def test_the_listing_carries_what_deleting_a_session_would_take() -> None:
    # What the cascade would remove, counted before the deletion is asked for, so an
    # admin can see the size of what they are about to delete.
    session_id = await _session_id()
    await _seed_one_chat_and_entry(session_id)

    response = await _call(
        "/admin/sessions", method="GET", headers={"X-Admin-Secret": _SECRET}
    )

    listed = next(
        s for s in response.json()["sessions"] if s["session_id"] == session_id
    )
    assert (listed["chats"], listed["faq_entries"]) == (1, 1)
    assert listed["last_message_at"] is not None
    assert listed["created_at"] is not None


async def test_the_listing_answers_an_empty_deployment_with_no_sessions() -> None:
    # An empty list, not a 404: "this service holds none" is an answer, and the route
    # names no resource that could be missing.
    response = await _call(
        "/admin/sessions", method="GET", headers={"X-Admin-Secret": _SECRET}
    )

    assert response.status_code == 200
    assert response.json() == {"sessions": []}


async def test_the_listing_never_calls_the_scheduler() -> None:
    # It reads this service's own stores only, so it stays answerable during an outage
    # - which is exactly when an admin is looking for what to re-run.
    session_id = await _session_id()

    with patch(
        "chat.api.admin.scheduling.delete_session",
        side_effect=AssertionError("the listing must not reach the scheduler"),
    ):
        response = await _call(
            "/admin/sessions", method="GET", headers={"X-Admin-Secret": _SECRET}
        )

    assert [s["session_id"] for s in response.json()["sessions"]] == [session_id]


async def _scheduler_that_clears_everything(
    channel: object, settings: object, *, session_id: str
) -> SessionPurge:
    """A reachable `delete_session` that had nothing of its own to remove."""
    return SessionPurge(
        patients_deleted=0, practitioners_deleted=0, appointments_deleted=0
    )


async def test_a_deleted_session_is_gone_from_the_listing() -> None:
    # The two routes read the same store, so what a sweep reports as deleted must be
    # what the next listing no longer offers.
    kept = await _session_id()
    removed = await _session_id()

    with patch(
        "chat.api.admin.scheduling.delete_session",
        new=_scheduler_that_clears_everything,
    ):
        deletion = await _call(
            f"/admin/sessions/{removed}", headers={"X-Admin-Secret": _SECRET}
        )
    response = await _call(
        "/admin/sessions", method="GET", headers={"X-Admin-Secret": _SECRET}
    )

    assert deletion.json()["results"][0]["status"] == "deleted"
    assert [s["session_id"] for s in response.json()["sessions"]] == [kept]


async def test_deleting_one_session_reports_it_per_session() -> None:
    session_id = await _session_id()

    response = await _call(
        f"/admin/sessions/{session_id}", headers={"X-Admin-Secret": _SECRET}
    )

    body = response.json()
    assert [r["session_id"] for r in body["results"]] == [session_id]
    assert body["results"][0]["status"] in {"deleted", "incomplete"}


async def test_deleting_all_sessions_reports_one_result_per_session() -> None:
    # FR-052: deleting all of them offers exactly the guarantees of deleting one,
    # applied to each - which is why the shape is a list of the same result.
    first = await _session_id()
    second = await _session_id()

    response = await _call("/admin/sessions", headers={"X-Admin-Secret": _SECRET})

    body = response.json()
    assert {r["session_id"] for r in body["results"]} == {first, second}


# --- one session's failure never ends the sweep ---------------------------------------


def _scheduler_that_fails_one(failing_id: str, exc: Exception) -> Callable[..., Any]:
    """A `delete_session` that raises `exc` for one session and clears the rest."""

    async def delete_session(
        channel: object, settings: object, *, session_id: str
    ) -> SessionPurge:
        if session_id == failing_id:
            raise exc
        return SessionPurge(
            patients_deleted=0, practitioners_deleted=0, appointments_deleted=0
        )

    return delete_session


@pytest.mark.parametrize(
    "exc",
    [
        SchedulingRequestError("session_id is required"),
        SchedulingNotFoundError("patient"),
        SchedulingUnavailableError("scheduling is down", outcome_unknown=False),
    ],
)
async def test_one_session_failing_never_ends_the_sweep(exc: Exception) -> None:
    # FR-052: each session is attempted and reported on its own. A status the scheduler
    # answered with is as much a per-session failure as an outage is, and escaping the
    # sweep would lose the report for every session already deleted.
    failing = await _session_id()
    other = await _session_id()

    with patch(
        "chat.api.admin.scheduling.delete_session",
        new=_scheduler_that_fails_one(failing, exc),
    ):
        response = await _call("/admin/sessions", headers={"X-Admin-Secret": _SECRET})

    assert response.status_code == 200
    results = {r["session_id"]: r for r in response.json()["results"]}
    assert set(results) == {failing, other}
    assert results[failing]["status"] == "incomplete"
    assert results[other]["status"] == "deleted"


@pytest.mark.parametrize(
    "exc",
    [
        SchedulingRequestError("session_id is required"),
        SchedulingNotFoundError("patient"),
    ],
)
async def test_a_rejected_deletion_of_one_session_is_reported_not_raised(
    exc: Exception,
) -> None:
    session_id = await _session_id()

    with patch(
        "chat.api.admin.scheduling.delete_session",
        new=_scheduler_that_fails_one(session_id, exc),
    ):
        response = await _call(
            f"/admin/sessions/{session_id}", headers={"X-Admin-Secret": _SECRET}
        )

    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "incomplete"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (
            SchedulingUnavailableError("timed out", outcome_unknown=True),
            "may have deleted",
        ),
        (
            SchedulingUnavailableError("connection refused", outcome_unknown=False),
            "deleted nothing",
        ),
        (SchedulingRequestError("session_id is required"), "deleted nothing"),
        (SchedulingNotFoundError("patient"), "deleted nothing"),
    ],
)
async def test_an_incomplete_deletion_says_what_is_known_of_the_scheduler(
    exc: Exception, expected: str
) -> None:
    # "A timeout never proves the server did nothing": a deletion whose outcome is
    # unknown must not be reported as one that definitely removed nothing, and neither
    # of them may be reported as a success.
    session_id = await _session_id()

    with patch(
        "chat.api.admin.scheduling.delete_session",
        new=_scheduler_that_fails_one(session_id, exc),
    ):
        response = await _call(
            f"/admin/sessions/{session_id}", headers={"X-Admin-Secret": _SECRET}
        )

    result = response.json()["results"][0]
    assert result["status"] == "incomplete"
    assert expected in result["detail"]


async def test_the_sweep_reads_which_failures_prove_nothing_from_one_place() -> None:
    """Which failures the scheduler decides before writing is decided once, not here.

    Widened to cover a kind this report calls unknown, the report follows without being
    edited - the same widening the `/chats` routes follow. A copy of that test kept
    here would have the two disagree about one exception, silently, and each of them
    look right on its own.
    """
    session_id = await _session_id()

    with (
        patch(
            "chat.api.admin.scheduling.delete_session",
            # The base class stands in for a subclass this build has no branch for:
            # neither is one of the two the classification names today.
            new=_scheduler_that_fails_one(session_id, SchedulingError("unplaceable")),
        ),
        patch("chat.clients.scheduling.rejected_before_writing", return_value=True),
    ):
        response = await _call(
            f"/admin/sessions/{session_id}", headers={"X-Admin-Secret": _SECRET}
        )

    result = response.json()["results"][0]
    assert result["status"] == "incomplete"
    assert "deleted nothing - it rejected the request" in result["detail"]
