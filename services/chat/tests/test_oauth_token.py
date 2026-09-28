"""`POST /oauth/token`: exchanging an authorization code, and refreshing.

I5: an authorization code is exchanged at most once, and only with its verifier, its
client and its redirect URI.
"""

from typing import Any

import pytest
from chat.connectors.authorization import TokenRefusalReason
from chat.db.session import session_factory
from chat.domain.models import OAuthGrant, OAuthToken
from chat.repositories.grant_repository import RefreshRefusal
from httpx import AsyncClient, Response
from sqlalchemy import func, select

from .conftest import (
    CLAUDE_CALLBACK,
    PKCE_VERIFIER,
    authorization_code,
    connector_api,
    new_session_id,
    pair_claude,
    register_claude,
)


async def _grants() -> list[OAuthGrant]:
    async with session_factory() as session:
        return list((await session.execute(select(OAuthGrant))).scalars().all())


async def _token_count() -> int:
    async with session_factory() as session:
        return int(
            (
                await session.execute(select(func.count()).select_from(OAuthToken))
            ).scalar_one()
        )


def _exchange_form(
    authorization: str, client_id: str, **overrides: str
) -> dict[str, str]:
    return {
        "grant_type": "authorization_code",
        "code": authorization,
        "redirect_uri": CLAUDE_CALLBACK,
        "client_id": client_id,
        "code_verifier": PKCE_VERIFIER,
        **overrides,
    }


async def _exchange(client: AsyncClient, form: dict[str, str]) -> Response:
    return await client.post("/oauth/token", data=form)


def _error(response: Response) -> str:
    assert response.status_code == 400, response.text
    assert response.headers["cache-control"] == "no-store"
    body: dict[str, Any] = response.json()
    assert set(body) == {"error", "error_description"}
    return str(body["error"])


# --- authorization code -----------------------------------------------------------


async def test_a_valid_exchange_issues_tokens_and_creates_the_grant() -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        client_id = await register_claude(client)
        code = await authorization_code(client, session_id, client_id)
        response = await _exchange(client, _exchange_form(code, client_id))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {
        "access_token",
        "token_type",
        "expires_in",
        "refresh_token",
        "scope",
    }
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    assert body["scope"] == "console:read"
    assert body["access_token"] != body["refresh_token"]

    [grant] = await _grants()
    assert grant.session_id == session_id
    assert grant.client_id == client_id
    assert grant.client_name == "Claude"
    assert grant.revoked_at is None
    assert await _token_count() == 2


@pytest.mark.parametrize(
    "override",
    [
        {"code_verifier": PKCE_VERIFIER[:-1] + "A"},
        {"redirect_uri": "https://evil.example/callback"},
        {"code": "not-a-code-that-was-ever-issued"},
    ],
)
async def test_a_mismatched_exchange_is_invalid_grant_and_creates_nothing(
    override: dict[str, str],
) -> None:
    # I5.
    session_id = await new_session_id()
    async with connector_api() as client:
        client_id = await register_claude(client)
        code = await authorization_code(client, session_id, client_id)
        response = await _exchange(client, _exchange_form(code, client_id, **override))

    assert _error(response) == "invalid_grant"
    assert await _grants() == []
    assert await _token_count() == 0


async def test_a_refusal_does_not_say_which_check_failed() -> None:
    # The description belongs to the error code, so a caller probing with stolen parts
    # learns nothing from which part was wrong.
    session_id = await new_session_id()
    descriptions: set[str] = set()
    async with connector_api() as client:
        client_id = await register_claude(client)
        for override in (
            {"code_verifier": PKCE_VERIFIER[:-1] + "A"},
            {"redirect_uri": "https://evil.example/callback"},
            {"code": "not-a-code-that-was-ever-issued"},
        ):
            code = await authorization_code(client, session_id, client_id)
            response = await _exchange(
                client, _exchange_form(code, client_id, **override)
            )
            assert _error(response) == "invalid_grant"
            descriptions.add(str(response.json()["error_description"]))

    assert len(descriptions) == 1
    [description] = descriptions
    for leaked in ("pkce", "redirect", "verifier", "mismatch"):
        assert leaked not in description.lower()


def test_every_refresh_refusal_is_a_token_refusal_reason() -> None:
    # The refresh path names its refusal by the repository's value; one without a
    # counterpart here would raise instead of being refused.
    assert {r.value for r in RefreshRefusal} <= {r.value for r in TokenRefusalReason}


async def test_another_clients_code_is_invalid_grant() -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        owner = await register_claude(client)
        other = await register_claude(client)
        code = await authorization_code(client, session_id, owner)
        response = await _exchange(client, _exchange_form(code, other))

    assert _error(response) == "invalid_grant"
    assert await _grants() == []


async def test_an_unknown_client_is_invalid_client() -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        client_id = await register_claude(client)
        code = await authorization_code(client, session_id, client_id)
        response = await _exchange(
            client, _exchange_form(code, "no-such-client-id-at-all")
        )

    assert _error(response) == "invalid_client"
    assert await _grants() == []


async def test_a_code_is_exchanged_at_most_once() -> None:
    # I5.
    session_id = await new_session_id()
    async with connector_api() as client:
        client_id = await register_claude(client)
        code = await authorization_code(client, session_id, client_id)
        first = await _exchange(client, _exchange_form(code, client_id))
        second = await _exchange(client, _exchange_form(code, client_id))

    assert first.status_code == 200
    assert _error(second) == "invalid_grant"
    assert len(await _grants()) == 1


async def test_a_failed_exchange_leaves_the_code_for_the_right_verifier() -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        client_id = await register_claude(client)
        code = await authorization_code(client, session_id, client_id)
        wrong = await _exchange(
            client, _exchange_form(code, client_id, code_verifier="x" * 43)
        )
        right = await _exchange(client, _exchange_form(code, client_id))

    assert _error(wrong) == "invalid_grant"
    assert right.status_code == 200


async def test_an_expired_code_is_invalid_grant() -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        client_id = await register_claude(client)
        code = await authorization_code(client, session_id, client_id)
        async with session_factory() as session:
            await session.execute(
                OAuthGrant.metadata.tables["oauth_authorization_codes"]
                .update()
                .values(expires_at=func.now())
            )
            await session.commit()
        response = await _exchange(client, _exchange_form(code, client_id))

    assert _error(response) == "invalid_grant"


@pytest.mark.parametrize(
    "missing", ["code", "redirect_uri", "client_id", "code_verifier"]
)
async def test_a_missing_field_is_invalid_request(missing: str) -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        client_id = await register_claude(client)
        code = await authorization_code(client, session_id, client_id)
        form = _exchange_form(code, client_id)
        del form[missing]
        response = await _exchange(client, form)

    assert _error(response) == "invalid_request"
    assert await _grants() == []


async def test_a_json_body_is_refused_and_the_form_body_accepted() -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        client_id = await register_claude(client)
        code = await authorization_code(client, session_id, client_id)
        as_json = await client.post(
            "/oauth/token", json=_exchange_form(code, client_id)
        )
        as_form = await _exchange(client, _exchange_form(code, client_id))

    assert _error(as_json) == "invalid_request"
    assert as_form.status_code == 200


@pytest.mark.parametrize("grant_type", ["client_credentials", "password", ""])
async def test_an_unsupported_grant_type_is_refused(grant_type: str) -> None:
    async with connector_api() as client:
        response = await client.post(
            "/oauth/token", data={"grant_type": grant_type, "client_id": "x"}
        )

    assert _error(response) in {"unsupported_grant_type", "invalid_request"}


# --- refresh ------------------------------------------------------------------------


def _refresh_form(refresh_token: str, client_id: str) -> dict[str, str]:
    return {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }


async def _mcp_status(client: AsyncClient, access_token: str) -> int:
    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
            "mcp-protocol-version": "2025-06-18",
            "authorization": f"Bearer {access_token}",
        },
    )
    return response.status_code


async def test_a_refresh_returns_a_new_pair_that_works() -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        first = await pair_claude(client, session_id)
        response = await _exchange(
            client, _refresh_form(first["refresh_token"], first["client_id"])
        )
        second = response.json()
        again = await _exchange(
            client, _refresh_form(second["refresh_token"], first["client_id"])
        )
        status = await _mcp_status(client, again.json()["access_token"])

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert set(second) == {
        "access_token",
        "token_type",
        "expires_in",
        "refresh_token",
        "scope",
    }
    assert second["access_token"] != first["access_token"]
    assert second["refresh_token"] != first["refresh_token"]
    assert again.status_code == 200
    assert status == 200
    [grant] = await _grants()
    assert grant.revoked_at is None


async def test_a_refresh_token_presented_twice_revokes_the_whole_grant() -> None:
    # I6: the second use of a replaced refresh token is the stolen-copy case.
    session_id = await new_session_id()
    async with connector_api() as client:
        first = await pair_claude(client, session_id)
        refreshed = (
            await _exchange(
                client, _refresh_form(first["refresh_token"], first["client_id"])
            )
        ).json()
        replayed = await _exchange(
            client, _refresh_form(first["refresh_token"], first["client_id"])
        )
        newest_access = await _mcp_status(client, refreshed["access_token"])
        newest_refresh = await _exchange(
            client, _refresh_form(refreshed["refresh_token"], first["client_id"])
        )

    assert _error(replayed) == "invalid_grant"
    [grant] = await _grants()
    assert grant.revoked_at is not None
    assert newest_access == 401
    assert _error(newest_refresh) == "invalid_grant"


async def test_a_refresh_for_another_client_is_invalid_grant_and_spends_nothing() -> (
    None
):
    session_id = await new_session_id()
    async with connector_api() as client:
        tokens = await pair_claude(client, session_id)
        other = await register_claude(client)
        wrong = await _exchange(client, _refresh_form(tokens["refresh_token"], other))
        right = await _exchange(
            client, _refresh_form(tokens["refresh_token"], tokens["client_id"])
        )

    assert _error(wrong) == "invalid_grant"
    assert right.status_code == 200
    [grant] = await _grants()
    assert grant.revoked_at is None


async def test_a_revoked_grants_refresh_token_is_invalid_grant() -> None:
    session_id = await new_session_id()
    async with connector_api(session_id) as client:
        tokens = await pair_claude(client, session_id)
        [grant] = (await client.get("/console/connected-apps")).json()["grants"]
        await client.post(f"/console/connected-apps/{grant['id']}/revoke")
        response = await _exchange(
            client, _refresh_form(tokens["refresh_token"], tokens["client_id"])
        )

    assert _error(response) == "invalid_grant"


async def test_an_expired_or_unknown_refresh_token_is_invalid_grant() -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        tokens = await pair_claude(client, session_id)
        unknown = await _exchange(
            client, _refresh_form("never-issued-refresh-token", tokens["client_id"])
        )
        async with session_factory() as session:
            await session.execute(
                OAuthToken.__table__.update()
                .where(OAuthToken.kind == "refresh")
                .values(expires_at=func.now())
            )
            await session.commit()
        expired = await _exchange(
            client, _refresh_form(tokens["refresh_token"], tokens["client_id"])
        )

    assert _error(unknown) == "invalid_grant"
    assert _error(expired) == "invalid_grant"
    # Neither is a reuse: an unknown or expired token revokes nothing.
    [grant] = await _grants()
    assert grant.revoked_at is None


@pytest.mark.parametrize("missing", ["refresh_token", "client_id"])
async def test_a_refresh_missing_a_field_is_invalid_request(missing: str) -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        tokens = await pair_claude(client, session_id)
        form = _refresh_form(tokens["refresh_token"], tokens["client_id"])
        del form[missing]
        response = await _exchange(client, form)

    assert _error(response) == "invalid_request"
