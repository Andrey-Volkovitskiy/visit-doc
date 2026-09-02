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

from unittest.mock import patch

import pytest
from chat.core.config import Settings
from chat.db.session import engine, session_factory
from chat.main import app
from chat.repositories import chat_repository
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient, Response
from structlog.testing import capture_logs

from .conftest import fake_anthropic_client

_SECRET = "a-secret-nobody-should-see-in-a-log"


def _settings(secret: str) -> Settings:
    base = Settings()
    return base.model_copy(update={"ADMIN_SECRET": secret})


async def _session_id() -> str:
    async with session_factory() as session:
        session_row = await chat_repository.create_session(session)
    await engine.dispose()
    return session_row.id


async def _call(
    path: str,
    *,
    configured: str = _SECRET,
    headers: dict[str, str] | None = None,
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
                return await http.delete(path, headers=headers or {}, params=params)


# --- the four properties -------------------------------------------------------------


@pytest.mark.parametrize("path", ["/admin/sessions", "/admin/sessions/01WHATEVER"])
async def test_the_secret_is_read_from_the_header(path: str) -> None:
    response = await _call(path, headers={"X-Admin-Secret": _SECRET})

    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/admin/sessions", "/admin/sessions/01WHATEVER"])
async def test_the_secret_is_never_accepted_from_a_query_string(path: str) -> None:
    # A query string reaches access logs and browser history, where the redaction that
    # covers a log event does not follow.
    response = await _call(path, params={"secret": _SECRET, "admin_secret": _SECRET})

    assert response.status_code == 403


async def test_the_secret_is_never_accepted_from_a_path_segment() -> None:
    response = await _call(f"/admin/sessions/{_SECRET}")

    assert response.status_code == 403


@pytest.mark.parametrize("path", ["/admin/sessions", "/admin/sessions/01WHATEVER"])
async def test_the_comparison_is_constant_time(path: str) -> None:
    # A refusal that returned faster for a wrong first character would say how much of
    # the secret was right, one request at a time.
    with patch("chat.api.admin.hmac.compare_digest", return_value=True) as compare:
        await _call(path, headers={"X-Admin-Secret": "wrong"})

    compare.assert_called_once()


async def test_neither_route_appears_in_the_published_schema() -> None:
    # Declared on the decorators: a router cannot retroactively hide its routes from
    # `/openapi.json`.
    await engine.dispose()
    with patch("chat.main.AsyncAnthropic") as mock_anthropic_cls:
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app) as client:
            schema = client.get("/openapi.json").json()

    assert not [path for path in schema["paths"] if path.startswith("/admin")]


@pytest.mark.parametrize("configured", ["", "   "])
@pytest.mark.parametrize("path", ["/admin/sessions", "/admin/sessions/01WHATEVER"])
async def test_an_unconfigured_secret_refuses_every_request(
    path: str, configured: str
) -> None:
    # A deployment that has not configured one has no admin, not an open door.
    empty_header = await _call(path, configured=configured, headers={})
    matching_header = await _call(
        path, configured=configured, headers={"X-Admin-Secret": configured}
    )
    anything = await _call(
        path, configured=configured, headers={"X-Admin-Secret": "anything"}
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
