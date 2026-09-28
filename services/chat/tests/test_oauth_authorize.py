"""`/oauth/authorize`: the page that asks for the pairing code, and its submission.

I3: one sign-in accepts at most five wrong codes, then nothing.
I4: an unverified redirect target is never redirected to.

The page is the one place in the sign-in a person types a secret, and every value on it
comes from the caller, so it is also checked for escaping.
"""

from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

import pytest
from chat.db.session import session_factory
from chat.domain.models import OAuthAuthorizationCode, OAuthAuthorizationRequest
from httpx import AsyncClient, Response
from sqlalchemy import func, select, text

from .conftest import (
    CLAUDE_CALLBACK,
    CONNECTOR_BASE_URL,
    authorize_params,
    connector_api,
    issue_pairing_code,
    new_session_id,
    register_claude,
    start_sign_in,
)

_NOT_VALID = "That code is not valid"
_EXPIRED = "This sign-in has expired. Start again from Claude."


async def _count(model: type[object]) -> int:
    async with session_factory() as session:
        return int(
            (
                await session.execute(select(func.count()).select_from(model))
            ).scalar_one()
        )


def _query(response: Response) -> dict[str, list[str]]:
    location = urlsplit(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == CLAUDE_CALLBACK
    return parse_qs(location.query)


async def _submit(client: AsyncClient, request_id: str, code: str) -> Response:
    return await client.post(
        "/oauth/authorize", data={"request": request_id, "code": code}
    )


# --- GET: validating the request ----------------------------------------------------


async def test_an_unknown_client_gets_the_error_page_and_no_redirect() -> None:
    async with connector_api() as client:
        response = await client.get(
            "/oauth/authorize", params=authorize_params("no-such-client")
        )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert "location" not in response.headers
    assert await _count(OAuthAuthorizationRequest) == 0


async def test_a_mismatched_redirect_uri_gets_the_error_page_and_no_redirect() -> None:
    # I4: the one target an attacker would choose is the one never redirected to.
    async with connector_api() as client:
        client_id = await register_claude(client)
        response = await client.get(
            "/oauth/authorize",
            params=authorize_params(
                client_id, redirect_uri="https://evil.example/callback"
            ),
        )

    assert response.status_code == 400
    assert "location" not in response.headers
    assert "evil.example" not in response.text
    assert await _count(OAuthAuthorizationRequest) == 0


@pytest.mark.parametrize(
    "override",
    [
        {"code_challenge": ""},
        {"code_challenge_method": "plain"},
        {"code_challenge_method": ""},
        {"response_type": "token"},
        {"resource": "https://elsewhere.example/mcp"},
        {"scope": "console:write"},
        {"scope": "console:read admin"},
    ],
)
async def test_any_other_invalid_parameter_redirects_back_with_invalid_request(
    override: dict[str, str],
) -> None:
    async with connector_api() as client:
        client_id = await register_claude(client)
        response = await client.get(
            "/oauth/authorize", params=authorize_params(client_id, **override)
        )

    assert response.status_code == 302
    assert _query(response) == {
        "error": ["invalid_request"],
        "state": ["state-from-claude"],
    }
    assert await _count(OAuthAuthorizationRequest) == 0


async def test_an_invalid_request_without_state_redirects_without_one() -> None:
    async with connector_api() as client:
        client_id = await register_claude(client)
        params = authorize_params(client_id, response_type="token")
        del params["state"]
        response = await client.get("/oauth/authorize", params=params)

    assert response.status_code == 302
    assert _query(response) == {"error": ["invalid_request"]}


@pytest.mark.parametrize(
    "override",
    [{"scope": "console:read"}, {"scope": ""}, {"resource": ""}],
)
async def test_scope_and_resource_may_be_left_to_their_defaults(
    override: dict[str, str],
) -> None:
    async with connector_api() as client:
        client_id = await register_claude(client)
        params = authorize_params(client_id, **override)
        for key, value in override.items():
            if not value:
                del params[key]
        response = await client.get("/oauth/authorize", params=params)

    assert response.status_code == 200


async def test_a_valid_request_renders_the_page_and_stores_the_sign_in() -> None:
    async with connector_api() as client:
        client_id = await register_claude(client, name="Claude")
        response = await client.get(
            "/oauth/authorize", params=authorize_params(client_id)
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    page = response.text
    # FR-011: who is asking, where access goes, and what it covers.
    assert "Claude" in page
    assert "claude.ai" in page
    assert "read-only" in page
    assert "conversations" in page and "booking" in page
    assert 'autocomplete="one-time-code"' in page
    assert await _count(OAuthAuthorizationRequest) == 1

    async with session_factory() as session:
        stored = (await session.execute(select(OAuthAuthorizationRequest))).scalar_one()
    assert stored.client_id == client_id
    assert stored.state == "state-from-claude"
    assert stored.resource == f"{CONNECTOR_BASE_URL}/mcp"
    assert "console:read" in stored.scope.split()


async def test_the_page_escapes_every_value_it_shows() -> None:
    async with connector_api() as client:
        client_id = await register_claude(client, name="<script>alert(1)</script>")
        response = await client.get(
            "/oauth/authorize", params=authorize_params(client_id)
        )

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text


async def test_no_other_site_may_frame_the_pages() -> None:
    # The page asks for a secret: framed invisibly inside another site, it could
    # collect a code typed by someone who cannot see where it goes.
    async with connector_api() as client:
        client_id = await register_claude(client)
        pairing = await client.get(
            "/oauth/authorize", params=authorize_params(client_id)
        )
        error = await client.get(
            "/oauth/authorize", params=authorize_params("not-a-registered-client")
        )

    for response in (pairing, error):
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["content-security-policy"] == "frame-ancestors 'none'"
        assert response.headers["cache-control"] == "no-store"


async def test_the_request_id_on_the_page_is_not_what_is_stored() -> None:
    async with connector_api() as client:
        request_id = await start_sign_in(client, await register_claude(client))

    async with session_factory() as session:
        stored = (await session.execute(select(OAuthAuthorizationRequest))).scalar_one()
    assert request_id not in stored.id_hash
    assert len(stored.id_hash) == 64


# --- POST: the pairing code -----------------------------------------------------------


def _bare_lower(code: str) -> str:
    return code.replace("-", "").lower()


@pytest.mark.parametrize("typed", [str, str.lower, _bare_lower])
async def test_the_right_code_redirects_to_claude_with_a_code_and_the_state(
    typed: Callable[[str], str],
) -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        request_id = await start_sign_in(client, await register_claude(client))
        code = await issue_pairing_code(session_id)
        response = await _submit(client, request_id, typed(code))

    assert response.status_code == 302
    query = _query(response)
    assert query["state"] == ["state-from-claude"]
    assert len(query["code"][0]) >= 32

    async with session_factory() as session:
        stored = (await session.execute(select(OAuthAuthorizationCode))).scalar_one()
    assert stored.session_id == session_id
    assert query["code"][0] not in stored.code_hash


async def test_a_wrong_code_shows_the_page_again_and_counts_the_failure() -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        request_id = await start_sign_in(client, await register_claude(client))
        await issue_pairing_code(session_id)
        response = await _submit(client, request_id, "0000-0000")

    assert response.status_code == 200
    assert _NOT_VALID in response.text
    assert request_id in response.text
    async with session_factory() as session:
        stored = (await session.execute(select(OAuthAuthorizationRequest))).scalar_one()
    assert stored.failed_attempts == 1
    assert stored.completed_at is None


async def test_an_expired_code_is_refused_like_a_wrong_one() -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        request_id = await start_sign_in(client, await register_claude(client))
        code = await issue_pairing_code(session_id)
        async with session_factory() as session:
            await session.execute(
                text("UPDATE mcp_pairing_codes SET expires_at = now()")
            )
            await session.commit()
        response = await _submit(client, request_id, code)

    assert response.status_code == 200
    assert _NOT_VALID in response.text


async def test_a_used_code_is_refused_like_a_wrong_one() -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        client_id = await register_claude(client)
        first = await start_sign_in(client, client_id)
        second = await start_sign_in(client, client_id)
        code = await issue_pairing_code(session_id)
        assert (await _submit(client, first, code)).status_code == 302
        response = await _submit(client, second, code)

    assert response.status_code == 200
    assert _NOT_VALID in response.text


async def test_the_code_a_newer_one_replaced_is_refused() -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        request_id = await start_sign_in(client, await register_claude(client))
        older = await issue_pairing_code(session_id)
        await issue_pairing_code(session_id)
        response = await _submit(client, request_id, older)

    assert response.status_code == 200
    assert _NOT_VALID in response.text


async def test_five_wrong_codes_end_the_sign_in_even_for_the_right_one() -> None:
    # I3, FR-013.
    session_id = await new_session_id()
    async with connector_api() as client:
        request_id = await start_sign_in(client, await register_claude(client))
        code = await issue_pairing_code(session_id)
        wrong = [await _submit(client, request_id, "0000-0000") for _ in range(5)]
        right = await _submit(client, request_id, code)

    assert [r.status_code for r in wrong] == [200, 200, 200, 200, 400]
    assert all(_NOT_VALID in r.text for r in wrong[:4])
    assert _EXPIRED in wrong[4].text
    assert right.status_code == 400
    assert _EXPIRED in right.text
    assert "location" not in right.headers
    # The code was never spent on the dead sign-in, so a fresh one can still use it.
    async with connector_api() as client:
        fresh = await start_sign_in(client, await register_claude(client))
        assert (await _submit(client, fresh, code)).status_code == 302


async def test_a_completed_sign_in_accepts_nothing_more() -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        request_id = await start_sign_in(client, await register_claude(client))
        assert (
            await _submit(client, request_id, await issue_pairing_code(session_id))
        ).status_code == 302
        again = await _submit(client, request_id, await issue_pairing_code(session_id))

    assert again.status_code == 400
    assert _EXPIRED in again.text
    assert await _count(OAuthAuthorizationCode) == 1


async def test_an_unknown_or_expired_sign_in_gets_the_expired_page() -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        request_id = await start_sign_in(client, await register_claude(client))
        code = await issue_pairing_code(session_id)
        unknown = await _submit(client, "no-such-request", code)
        async with session_factory() as session:
            await session.execute(
                text("UPDATE oauth_authorization_requests SET expires_at = now()")
            )
            await session.commit()
        expired = await _submit(client, request_id, code)

    for response in (unknown, expired):
        assert response.status_code == 400
        assert _EXPIRED in response.text
    assert await _count(OAuthAuthorizationCode) == 0


async def test_a_submission_missing_a_field_gets_the_expired_page() -> None:
    async with connector_api() as client:
        response = await client.post("/oauth/authorize", data={"code": "0000-0000"})

    assert response.status_code == 400
    assert _EXPIRED in response.text
