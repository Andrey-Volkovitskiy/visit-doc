"""`POST /oauth/register`: dynamic client registration, for Claude's callback only.

I4: nothing but Claude's return address can be registered, so no other site can be
handed an authorization code.
"""

from typing import Any

import pytest
from chat.db.session import session_factory
from chat.domain.models import OAuthClient
from sqlalchemy import func, select

from .conftest import CLAUDE_CALLBACK, CLAUDE_REGISTRATION, connector_api


async def _client_count() -> int:
    async with session_factory() as session:
        return int(
            (
                await session.execute(select(func.count()).select_from(OAuthClient))
            ).scalar_one()
        )


async def _register(body: object) -> tuple[int, dict[str, Any]]:
    async with connector_api() as client:
        response = await client.post("/oauth/register", json=body)
    return response.status_code, response.json()


async def test_claudes_registration_is_accepted_as_a_public_client() -> None:
    status, body = await _register(CLAUDE_REGISTRATION)

    assert status == 201
    assert body["client_name"] == "Claude"
    assert body["redirect_uris"] == [CLAUDE_CALLBACK]
    assert body["grant_types"] == ["authorization_code", "refresh_token"]
    assert body["response_types"] == ["code"]
    assert body["token_endpoint_auth_method"] == "none"
    assert isinstance(body["client_id"], str) and len(body["client_id"]) >= 32
    assert isinstance(body["client_id_issued_at"], int)
    assert "client_secret" not in body

    async with session_factory() as session:
        stored = await session.get(OAuthClient, body["client_id"])
    assert stored is not None
    assert stored.redirect_uri == CLAUDE_CALLBACK


async def test_each_registration_gets_its_own_client_id() -> None:
    first = await _register(CLAUDE_REGISTRATION)
    second = await _register(CLAUDE_REGISTRATION)

    assert first[1]["client_id"] != second[1]["client_id"]


@pytest.mark.parametrize(
    "redirect_uris",
    [
        ["https://evil.example/callback"],
        [CLAUDE_CALLBACK, "https://evil.example/callback"],
        ["https://claude.ai/api/mcp/auth_callback/"],
        ["http://claude.ai/api/mcp/auth_callback"],
        [],
        None,
    ],
)
async def test_any_other_return_address_is_refused(redirect_uris: object) -> None:
    # I4, FR-012.
    body = {**CLAUDE_REGISTRATION, "redirect_uris": redirect_uris}
    if redirect_uris is None:
        del body["redirect_uris"]

    status, answer = await _register(body)

    assert (status, answer["error"]) == (400, "invalid_redirect_uri")
    assert await _client_count() == 0


@pytest.mark.parametrize(
    "override",
    [
        {"token_endpoint_auth_method": "client_secret_basic"},
        {"token_endpoint_auth_method": "client_secret_post"},
        {"grant_types": ["authorization_code", "client_credentials"]},
        {"grant_types": ["implicit"]},
        {"response_types": ["token"]},
        {"client_name": 42},
    ],
)
async def test_metadata_the_server_does_not_support_is_refused(
    override: dict[str, object],
) -> None:
    status, answer = await _register({**CLAUDE_REGISTRATION, **override})

    assert (status, answer["error"]) == (400, "invalid_client_metadata")
    assert await _client_count() == 0


async def test_a_body_that_is_not_a_json_object_is_refused() -> None:
    status, answer = await _register(["not", "an", "object"])

    assert (status, answer["error"]) == (400, "invalid_client_metadata")


async def test_the_defaults_are_what_claude_needs() -> None:
    # Absent grant types, response types and auth method mean the RFC 7591 defaults,
    # all of which this server supports.
    status, body = await _register({"redirect_uris": [CLAUDE_CALLBACK]})

    assert status == 201
    assert body["client_name"] == "Unnamed client"
    assert body["token_endpoint_auth_method"] == "none"


async def test_an_over_long_name_is_trimmed_to_100_characters() -> None:
    status, body = await _register({**CLAUDE_REGISTRATION, "client_name": "x" * 250})

    assert status == 201
    assert body["client_name"] == "x" * 100
