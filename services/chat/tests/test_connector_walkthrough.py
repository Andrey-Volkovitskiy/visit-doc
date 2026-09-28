"""The whole pairing, driven as the console and Claude drive it, in order.

The executable form of the spec's user stories: a code issued through the console,
Claude registering, the pairing page, the code typed, the token exchange, and the
pairing listed back in the console.
"""

from typing import Any
from urllib.parse import parse_qs, urlsplit

from chat.main import app
from httpx import ASGITransport, AsyncClient

from .conftest import (
    CLAUDE_CALLBACK,
    CLAUDE_REGISTRATION,
    CONNECTOR_BASE_URL,
    PKCE_VERIFIER,
    authorize_params,
    connector_api,
    new_session_id,
    plant_counts,
    request_id_in,
)


def _claude() -> AsyncClient:
    """A client on the same running app as the console, carrying no VisitDoc cookie.

    One lifespan for both sides: a second `connector_api` would run a second lifespan
    over the same app, replacing the transport the first one's requests use.
    """
    return AsyncClient(transport=ASGITransport(app=app), base_url=CONNECTOR_BASE_URL)


async def test_a_code_from_the_console_pairs_claude_and_the_console_lists_it() -> None:
    session_id = await new_session_id()
    async with connector_api(session_id) as console:
        issued = (await console.post("/console/connected-apps/pairing-code")).json()

        async with _claude() as claude:
            registered = await claude.post("/oauth/register", json=CLAUDE_REGISTRATION)
            client_id = registered.json()["client_id"]
            page = await claude.get(
                "/oauth/authorize", params=authorize_params(client_id)
            )
            submitted = await claude.post(
                "/oauth/authorize",
                data={
                    "request": request_id_in(page.text),
                    "code": issued["code"].lower().replace("-", ""),
                },
            )
            code = parse_qs(urlsplit(submitted.headers["location"]).query)["code"][0]
            exchanged = await claude.post(
                "/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": CLAUDE_CALLBACK,
                    "client_id": client_id,
                    "code_verifier": PKCE_VERIFIER,
                },
            )

        listed = (await console.get("/console/connected-apps")).json()

    assert registered.status_code == 201
    assert page.status_code == 200
    assert submitted.status_code == 302
    assert exchanged.status_code == 200
    assert listed["pairing_code"] is None
    assert [g["client_name"] for g in listed["grants"]] == ["Claude"]


async def test_a_paired_claude_asks_both_questions_and_gets_the_planted_state() -> None:
    session_id = await new_session_id()
    await plant_counts(session_id, needing_attention=2, booked=3, cancelled=1)
    async with connector_api(session_id) as console:
        issued = (await console.post("/console/connected-apps/pairing-code")).json()
        async with _claude() as claude:
            token = await _pair_with(claude, issued["code"])
            attention = await _tool(
                claude, token, "count_conversations_needing_attention", {}
            )
            changes = await _tool(
                claude, token, "count_recent_booking_changes", {"minutes": 60}
            )

    assert attention["structuredContent"] == {"needing_attention": 2}
    assert changes["structuredContent"] == {
        "window_minutes": 60,
        "booked": 3,
        "cancelled": 1,
        "rescheduled": 0,
        "outcome_unknown": 0,
    }


async def _pair_with(claude: AsyncClient, pairing_code: str) -> str:
    """Walk Claude's side of pairing with `pairing_code`; return the access token."""
    registered = await claude.post("/oauth/register", json=CLAUDE_REGISTRATION)
    client_id = registered.json()["client_id"]
    page = await claude.get("/oauth/authorize", params=authorize_params(client_id))
    submitted = await claude.post(
        "/oauth/authorize",
        data={"request": request_id_in(page.text), "code": pairing_code},
    )
    code = parse_qs(urlsplit(submitted.headers["location"]).query)["code"][0]
    exchanged = await claude.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CLAUDE_CALLBACK,
            "client_id": client_id,
            "code_verifier": PKCE_VERIFIER,
        },
    )
    assert exchanged.status_code == 200, exchanged.text
    return str(exchanged.json()["access_token"])


async def _tool(
    claude: AsyncClient, token: str, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    response = await claude.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers={
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
            "mcp-protocol-version": "2025-06-18",
            "authorization": f"Bearer {token}",
        },
    )
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()["result"]
    return result


async def test_revoking_in_the_console_cuts_claude_off() -> None:
    session_id = await new_session_id()
    async with connector_api(session_id) as console:
        issued = (await console.post("/console/connected-apps/pairing-code")).json()
        async with _claude() as claude:
            token = await _pair_with(claude, issued["code"])
            answered = await _tool(
                claude, token, "count_conversations_needing_attention", {}
            )
            [grant] = (await console.get("/console/connected-apps")).json()["grants"]
            revoked = await console.post(
                f"/console/connected-apps/{grant['id']}/revoke"
            )
            refused = await claude.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "count_conversations_needing_attention",
                        "arguments": {},
                    },
                },
                headers={"authorization": f"Bearer {token}"},
            )

    assert answered["isError"] is False
    assert revoked.status_code == 204
    assert refused.status_code == 401
