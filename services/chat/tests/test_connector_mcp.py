"""`/mcp`: the connector's MCP transport, behind a bearer token.

The SDK serves the transport and the protected-resource document; everything it is
given - the issuer, the resource, the accepted host, the token check - comes from this
service. These tests drive it over HTTP with real tokens, because the SDK's in-memory
client skips the HTTP layer, and with it the authentication under test.
"""

from typing import Any

import pytest
from chat.connectors.public_address import UnavailableReason
from chat.db.session import session_factory
from chat.repositories import grant_repository
from httpx import AsyncClient, Response
from sqlalchemy import text

from .conftest import (
    CONNECTOR_BASE_URL,
    connector_api,
    new_session_id,
    pair_claude,
    plant_counts,
)

_RESOURCE_METADATA = f"{CONNECTOR_BASE_URL}/.well-known/oauth-protected-resource/mcp"


# --- authentication ---------------------------------------------------------------


async def test_a_call_without_a_token_is_401_naming_the_resource_metadata() -> None:
    async with connector_api() as client:
        response = await client.post("/mcp", json={})

    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert f'resource_metadata="{_RESOURCE_METADATA}"' in challenge


async def test_an_unknown_token_is_401() -> None:
    async with connector_api() as client:
        response = await client.post(
            "/mcp", json={}, headers={"authorization": "Bearer not-a-token"}
        )

    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


async def test_the_protected_resource_document_is_the_contracts() -> None:
    async with connector_api() as client:
        response = await client.get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 200
    assert response.json() == {
        "resource": f"{CONNECTOR_BASE_URL}/mcp",
        "authorization_servers": [CONNECTOR_BASE_URL],
        "scopes_supported": ["console:read"],
        "bearer_methods_supported": ["header"],
    }


@pytest.mark.parametrize(
    "path",
    [
        "/no-such-route",
        "/mcp/extra",
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/other",
    ],
)
async def test_the_transport_serves_only_its_two_paths(path: str) -> None:
    # R1: an unknown path keeps FastAPI's own 404, rather than the transport's.
    async with connector_api() as client:
        response = await client.get(path)

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


async def test_an_existing_routes_own_404_is_not_shadowed() -> None:
    async with connector_api() as client:
        response = await client.get("/chats/01NOSUCHCHAT00000000000000/messages")

    assert response.status_code == 404
    assert response.json() == {"detail": "chat not found"}
    assert "www-authenticate" not in response.headers


@pytest.mark.parametrize(
    ("public_base_url", "reason"),
    [
        ("", UnavailableReason.NOT_CONFIGURED),
        ("http://visitdoc.test", UnavailableReason.NOT_HTTPS),
    ],
)
async def test_both_paths_answer_503_while_unavailable(
    public_base_url: str, reason: UnavailableReason
) -> None:
    async with connector_api(base_url=public_base_url) as client:
        transport = await client.post("/mcp", json={})
        document = await client.get("/.well-known/oauth-protected-resource/mcp")

    for response in (transport, document):
        assert response.status_code == 503
        assert response.json() == {
            "error": "temporarily_unavailable",
            "error_description": reason.value,
        }


# --- the two tools -------------------------------------------------------------------

_MCP_HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
    "mcp-protocol-version": "2025-06-18",
}
_RANGE_ERROR = "minutes must be a whole number from 1 to 10080 (7 days)."


async def _rpc(
    client: AsyncClient, token: str, method: str, params: dict[str, Any] | None = None
) -> Response:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return await client.post(
        "/mcp",
        json=body,
        headers={**_MCP_HEADERS, "authorization": f"Bearer {token}"},
    )


async def _result(
    client: AsyncClient, token: str, method: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = await _rpc(client, token, method, params)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()["result"]
    return result


async def _call(
    client: AsyncClient, token: str, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    return await _result(
        client, token, "tools/call", {"name": name, "arguments": arguments}
    )


async def test_the_tool_list_is_exactly_the_two_read_only_tools() -> None:
    # FR-015.
    session_id = await new_session_id()
    async with connector_api() as client:
        token = (await pair_claude(client, session_id))["access_token"]
        tools = (await _result(client, token, "tools/list"))["tools"]

    assert sorted(tool["name"] for tool in tools) == [
        "count_conversations_needing_attention",
        "count_recent_booking_changes",
    ]
    for tool in tools:
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["inputSchema"]["additionalProperties"] is False
    by_name = {tool["name"]: tool for tool in tools}
    assert by_name["count_recent_booking_changes"]["inputSchema"]["properties"] == {
        "minutes": {"type": "integer", "minimum": 1, "maximum": 10080}
    }
    assert by_name["count_recent_booking_changes"]["inputSchema"]["required"] == [
        "minutes"
    ]
    assert (
        by_name["count_conversations_needing_attention"]["inputSchema"]["properties"]
        == {}
    )


async def test_each_tool_answers_with_its_counts_and_a_sentence() -> None:
    session_id = await new_session_id()
    await plant_counts(session_id, needing_attention=2, booked=3, cancelled=1)
    async with connector_api() as client:
        token = (await pair_claude(client, session_id))["access_token"]
        attention = await _call(
            client, token, "count_conversations_needing_attention", {}
        )
        changes = await _call(
            client, token, "count_recent_booking_changes", {"minutes": 60}
        )

    assert attention["isError"] is False
    assert attention["structuredContent"] == {"needing_attention": 2}
    assert attention["content"] == [
        {"type": "text", "text": "2 conversations need staff attention."}
    ]
    assert changes["isError"] is False
    assert changes["structuredContent"] == {
        "window_minutes": 60,
        "booked": 3,
        "cancelled": 1,
        "rescheduled": 0,
        "outcome_unknown": 0,
    }
    assert changes["content"] == [
        {"type": "text", "text": "Three appointments were booked and one cancelled."}
    ]


@pytest.mark.parametrize(
    ("planted", "text"),
    [
        (0, "No conversations need staff attention."),
        (1, "1 conversation needs staff attention."),
    ],
)
async def test_the_attention_sentence_agrees_with_its_count(
    planted: int, text: str
) -> None:
    session_id = await new_session_id()
    await plant_counts(session_id, needing_attention=planted, booked=0, cancelled=0)
    async with connector_api() as client:
        token = (await pair_claude(client, session_id))["access_token"]
        result = await _call(client, token, "count_conversations_needing_attention", {})

    assert result["content"][0]["text"] == text


@pytest.mark.parametrize("minutes", [0, 10081, -5, 1.5, "60", None])
async def test_a_window_outside_one_minute_to_seven_days_is_refused(
    minutes: object,
) -> None:
    # FR-018: refused with the range named, never clamped to another window.
    session_id = await new_session_id()
    async with connector_api() as client:
        token = (await pair_claude(client, session_id))["access_token"]
        result = await _call(
            client, token, "count_recent_booking_changes", {"minutes": minutes}
        )

    assert result["isError"] is True
    assert "structuredContent" not in result or result["structuredContent"] is None
    assert _RANGE_ERROR in result["content"][0]["text"]


@pytest.mark.parametrize("minutes", [1, 10080])
async def test_the_window_bounds_are_accepted_and_echoed(minutes: int) -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        token = (await pair_claude(client, session_id))["access_token"]
        result = await _call(
            client, token, "count_recent_booking_changes", {"minutes": minutes}
        )

    assert result["isError"] is False
    assert result["structuredContent"]["window_minutes"] == minutes


async def test_each_token_sees_only_its_own_sessions_counts() -> None:
    # I7, SC-006: the session comes from the token, and no argument can name another.
    mine = await new_session_id()
    theirs = await new_session_id()
    await plant_counts(mine, needing_attention=1, booked=1, cancelled=0)
    await plant_counts(theirs, needing_attention=4, booked=0, cancelled=3)
    async with connector_api() as client:
        my_token = (await pair_claude(client, mine))["access_token"]
        their_token = (await pair_claude(client, theirs))["access_token"]
        mine_attention = await _call(
            client, my_token, "count_conversations_needing_attention", {}
        )
        theirs_attention = await _call(
            client, their_token, "count_conversations_needing_attention", {}
        )
        mine_changes = await _call(
            client,
            my_token,
            "count_recent_booking_changes",
            {"minutes": 60, "session_id": theirs},
        )
        theirs_changes = await _call(
            client, their_token, "count_recent_booking_changes", {"minutes": 60}
        )

    assert mine_attention["structuredContent"] == {"needing_attention": 1}
    assert theirs_attention["structuredContent"] == {"needing_attention": 4}
    assert (
        mine_changes["structuredContent"]["booked"],
        mine_changes["structuredContent"]["cancelled"],
    ) == (1, 0)
    assert (
        theirs_changes["structuredContent"]["booked"],
        theirs_changes["structuredContent"]["cancelled"],
    ) == (0, 3)


async def test_neither_result_carries_anything_but_counts() -> None:
    # I11, FR-019: no name, no text, no id - structurally, not by what was planted.
    session_id = await new_session_id()
    await plant_counts(session_id, needing_attention=1, booked=1, cancelled=1)
    async with connector_api() as client:
        token = (await pair_claude(client, session_id))["access_token"]
        tools = {
            tool["name"]: tool
            for tool in (await _result(client, token, "tools/list"))["tools"]
        }
        attention = await _call(
            client, token, "count_conversations_needing_attention", {}
        )
        changes = await _call(
            client, token, "count_recent_booking_changes", {"minutes": 60}
        )

    for name, result in (
        ("count_conversations_needing_attention", attention),
        ("count_recent_booking_changes", changes),
    ):
        schema = tools[name]["outputSchema"]
        assert all(prop["type"] == "integer" for prop in schema["properties"].values())
        assert set(result["structuredContent"]) == set(schema["properties"])
        assert all(isinstance(v, int) for v in result["structuredContent"].values())
        assert "William Osler" not in str(result)
        assert "book me in" not in str(result)
        assert session_id not in str(result)


async def test_a_tool_call_marks_the_pairing_used() -> None:
    # FR-005, R7: the console's "last used" is the verification's stamp.
    session_id = await new_session_id()
    async with connector_api(session_id) as client:
        token = (await pair_claude(client, session_id))["access_token"]
        before = (await client.get("/console/connected-apps")).json()["grants"][0]
        await _call(client, token, "count_conversations_needing_attention", {})
        after = (await client.get("/console/connected-apps")).json()["grants"][0]

    assert before["last_used_seconds_ago"] is None
    assert after["last_used_seconds_ago"] is not None
    assert 0 <= after["last_used_seconds_ago"] <= 5


# --- a pairing that no longer works ---------------------------------------------------


async def test_a_revoked_pairings_token_is_refused_on_its_very_next_call() -> None:
    # I8, SC-004: every call looks the token up, so a revocation needs no expiry.
    session_id = await new_session_id()
    async with connector_api(session_id) as client:
        token = (await pair_claude(client, session_id))["access_token"]
        before = await _rpc(client, token, "tools/list")
        [grant] = (await client.get("/console/connected-apps")).json()["grants"]
        await client.post(f"/console/connected-apps/{grant['id']}/revoke")
        after = await _rpc(client, token, "tools/list")

    assert before.status_code == 200
    assert after.status_code == 401
    assert 'error="invalid_token"' in after.headers["www-authenticate"]


async def test_an_expired_access_token_is_refused() -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        token = (await pair_claude(client, session_id))["access_token"]
        async with session_factory() as session:
            await session.execute(
                text("UPDATE oauth_tokens SET expires_at = now() WHERE kind = 'access'")
            )
            await session.commit()
        response = await _rpc(client, token, "tools/list")

    assert response.status_code == 401


async def test_a_token_expiring_after_2038_still_verifies() -> None:
    # The expiry is read as a Unix timestamp, which outgrows 32 bits in January 2038.
    session_id = await new_session_id()
    async with connector_api() as client:
        token = (await pair_claude(client, session_id))["access_token"]
    async with session_factory() as session:
        await session.execute(
            text(
                "UPDATE oauth_tokens SET expires_at = '2040-01-01T00:00:00Z' "
                "WHERE kind = 'access'"
            )
        )
        await session.commit()
        verified = await grant_repository.verify_access(session, token)

    assert verified is not None
    assert verified.expires_at == 2208988800


async def test_a_refresh_token_is_not_an_access_token() -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        refresh = (await pair_claude(client, session_id))["refresh_token"]
        response = await _rpc(client, refresh, "tools/list")

    assert response.status_code == 401
