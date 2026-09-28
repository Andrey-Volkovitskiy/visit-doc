"""I10: no pairing code, authorization code, token or verifier is logged or kept plain.

The flow is run whole - pair, ask, refresh, revoke - with every event captured before
redaction, so a credential handed to the logger under any key at all fails here, not
only one the key-name rule would have caught.
"""

import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from chat.db.session import session_factory
from shared_logging import is_secret_key
from sqlalchemy import text
from structlog.testing import capture_logs

from .conftest import (
    CLAUDE_CALLBACK,
    PKCE_CHALLENGE,
    PKCE_VERIFIER,
    authorize_params,
    connector_api,
    issue_pairing_code,
    new_session_id,
    pair_claude,
    register_claude,
    request_id_in,
)

_EXPECTED_EVENTS = {
    "connector.pairing_code_issued",
    "connector.client_registered",
    "connector.authorize_failed",
    "connector.grant_created",
    "connector.tool_called",
    "connector.token_refreshed",
    "connector.refresh_reuse_detected",
    "connector.token_refused",
    "connector.grant_revoked",
}


async def _run_the_whole_flow() -> tuple[list[dict[str, Any]], list[str]]:
    """Pair, ask, fail, refresh, replay and revoke, capturing every event.

    Returns: the captured events, and every secret the flow handled in plain form.
    """
    session_id = await new_session_id()
    secrets: list[str] = [PKCE_VERIFIER]
    # Opened inside `connector_api`: its first use imports `chat.main`, whose
    # `create_app()` configures logging and replaces the processors `capture_logs` set,
    # so a capture opened before it records nothing.
    async with connector_api(session_id) as client:
        with capture_logs() as logs:
            pairing = (
                await client.post("/console/connected-apps/pairing-code")
            ).json()["code"]
            secrets += [pairing, pairing.replace("-", "")]
            client_id = await register_claude(client)
            page = await client.get(
                "/oauth/authorize", params=authorize_params(client_id)
            )
            request_id = request_id_in(page.text)
            secrets.append(request_id)
            await client.post(
                "/oauth/authorize", data={"request": request_id, "code": "0000-0000"}
            )
            done = await client.post(
                "/oauth/authorize", data={"request": request_id, "code": pairing}
            )
            code = parse_qs(urlsplit(done.headers["location"]).query)["code"][0]
            secrets.append(code)
            await client.post(
                "/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": CLAUDE_CALLBACK,
                    "client_id": client_id,
                    "code_verifier": "x" * 43,
                },
            )
            tokens = (
                await client.post(
                    "/oauth/token",
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": CLAUDE_CALLBACK,
                        "client_id": client_id,
                        "code_verifier": PKCE_VERIFIER,
                    },
                )
            ).json()
            secrets += [tokens["access_token"], tokens["refresh_token"]]
            await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "count_conversations_needing_attention",
                        "arguments": {},
                    },
                },
                headers={
                    "accept": "application/json, text/event-stream",
                    "content-type": "application/json",
                    "authorization": f"Bearer {tokens['access_token']}",
                },
            )
            refresh = {
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": client_id,
            }
            renewed = (await client.post("/oauth/token", data=refresh)).json()
            secrets += [renewed["access_token"], renewed["refresh_token"]]
            await client.post("/oauth/token", data=refresh)
            # A second pairing, so there is a live grant left for the console to revoke.
            second = (await client.post("/console/connected-apps/pairing-code")).json()[
                "code"
            ]
            secrets += [second, second.replace("-", "")]
            page = await client.get(
                "/oauth/authorize", params=authorize_params(client_id)
            )
            redirected = await client.post(
                "/oauth/authorize",
                data={"request": request_id_in(page.text), "code": second},
            )
            second_code = parse_qs(urlsplit(redirected.headers["location"]).query)[
                "code"
            ][0]
            secrets.append(second_code)
            paired = (
                await client.post(
                    "/oauth/token",
                    data={
                        "grant_type": "authorization_code",
                        "code": second_code,
                        "redirect_uri": CLAUDE_CALLBACK,
                        "client_id": client_id,
                        "code_verifier": PKCE_VERIFIER,
                    },
                )
            ).json()
            secrets += [paired["access_token"], paired["refresh_token"]]
            [grant] = (await client.get("/console/connected-apps")).json()["grants"]
            await client.post(f"/console/connected-apps/{grant['id']}/revoke")
    return logs, secrets


async def test_no_event_carries_a_credential_under_any_key() -> None:
    # I10, FR-023, SC-005.
    logs, secrets = await _run_the_whole_flow()

    assert _EXPECTED_EVENTS <= {entry["event"] for entry in logs}
    rendered = repr(logs)
    for secret in secrets:
        assert secret not in rendered, secret


async def test_every_connector_event_names_its_session_or_client() -> None:
    # FR-024: each pairing, question, refusal and revocation says what it concerned.
    logs, _ = await _run_the_whole_flow()

    connector_events = [e for e in logs if e["event"].startswith("connector.")]
    assert {e["event"] for e in connector_events} >= _EXPECTED_EVENTS
    for entry in connector_events:
        assert {"session_id", "grant_id", "client_id"} & set(entry), entry


async def test_every_secret_column_holds_only_64_character_digests() -> None:
    # I10: nothing stored can be read back as the credential it came from.
    _, secrets = await _run_the_whole_flow()

    columns = (
        ("mcp_pairing_codes", "code_hash"),
        ("oauth_authorization_requests", "id_hash"),
        ("oauth_authorization_codes", "code_hash"),
        ("oauth_tokens", "token_hash"),
    )
    async with session_factory() as session:
        for table, column in columns:
            values = (
                (await session.execute(text(f"SELECT {column} FROM {table}")))
                .scalars()
                .all()
            )
            assert values, table
            for value in values:
                assert re.fullmatch(r"[0-9a-f]{64}", value), (table, value)
                assert value not in secrets


def test_the_code_keys_are_redacted_by_name() -> None:
    # A pairing or authorization code logged by mistake under its own name is caught
    # by the key-name rule, as a token already is.
    for key in (
        "code",
        "pairing_code",
        "authorization_code",
        "code_verifier",
        "access_token",
        "refresh_token",
    ):
        assert is_secret_key(key), key


def test_adding_them_redacts_no_existing_field() -> None:
    # R14's open question, answered: `status_code` is logged across both services, and
    # a bare `code` substring would have redacted every one of them.
    for key in ("status_code", "error_code", "encoded", "code_challenge", "reason"):
        assert not is_secret_key(key), key


def test_the_pkce_challenge_is_not_a_secret() -> None:
    # Public by design - it travels in the authorize URL - so it may be logged.
    assert PKCE_CHALLENGE and not is_secret_key("code_challenge")


# --- refusals name what they concerned (FR-024) --------------------------------------


async def _refused_call(session_id: str, *, expire: bool) -> list[dict[str, Any]]:
    """Pair, make the token unusable, call once; return the events of that call."""
    async with connector_api(session_id) as client:
        tokens = await pair_claude(client, session_id)
        if expire:
            async with session_factory() as session:
                await session.execute(
                    text(
                        "UPDATE oauth_tokens SET expires_at = now() "
                        "WHERE kind = 'access'"
                    )
                )
                await session.commit()
        else:
            [grant] = (await client.get("/console/connected-apps")).json()["grants"]
            await client.post(f"/console/connected-apps/{grant['id']}/revoke")
        with capture_logs() as logs:
            response = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"authorization": f"Bearer {tokens['access_token']}"},
            )
    assert response.status_code == 401
    assert tokens["access_token"] not in repr(logs)
    return logs


async def test_a_call_refused_for_a_revoked_pairing_names_its_session_and_grant() -> (
    None
):
    session_id = await new_session_id()

    logs = await _refused_call(session_id, expire=False)

    [event] = [e for e in logs if e["event"] == "connector.token_rejected"]
    assert event["session_id"] == session_id
    assert event["grant_id"]
    assert event["reason"] == "grant_revoked"


async def test_a_call_refused_for_an_expired_token_names_its_session_and_grant() -> (
    None
):
    session_id = await new_session_id()

    logs = await _refused_call(session_id, expire=True)

    [event] = [e for e in logs if e["event"] == "connector.token_rejected"]
    assert event["session_id"] == session_id
    assert event["grant_id"]
    assert event["reason"] == "token_expired"


async def test_a_refresh_from_another_client_is_logged_with_the_grant_it_targeted() -> (
    None
):
    session_id = await new_session_id()
    async with connector_api(session_id) as client:
        tokens = await pair_claude(client, session_id)
        other = await register_claude(client)
        with capture_logs() as logs:
            await client.post(
                "/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                    "client_id": other,
                },
            )

    [event] = [e for e in logs if e["event"] == "connector.token_refused"]
    assert event["reason"] == "client_mismatch"
    assert event["session_id"] == session_id
    assert event["grant_id"]
    assert tokens["refresh_token"] not in repr(logs)


async def test_a_question_refused_for_its_window_names_its_session_and_grant() -> None:
    session_id = await new_session_id()
    async with connector_api(session_id) as client:
        tokens = await pair_claude(client, session_id)
        with capture_logs() as logs:
            response = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "count_recent_booking_changes",
                        "arguments": {"minutes": 0},
                    },
                },
                headers={
                    "accept": "application/json, text/event-stream",
                    "content-type": "application/json",
                    "authorization": f"Bearer {tokens['access_token']}",
                },
            )

    assert response.json()["result"]["isError"] is True
    [event] = [e for e in logs if e["event"] == "connector.tool_refused"]
    assert event["session_id"] == session_id
    assert event["grant_id"]
    assert event["tool"] == "count_recent_booking_changes"
    assert event["reason"] == "window_out_of_range"
    assert not [e for e in logs if e["event"] == "connector.tool_called"]


async def test_a_refused_exchange_of_a_matched_code_names_its_session() -> None:
    session_id = await new_session_id()
    async with connector_api() as client:
        client_id = await register_claude(client)
        page = await client.get("/oauth/authorize", params=authorize_params(client_id))
        pairing = await issue_pairing_code(session_id)
        done = await client.post(
            "/oauth/authorize",
            data={"request": request_id_in(page.text), "code": pairing},
        )
        code = parse_qs(urlsplit(done.headers["location"]).query)["code"][0]
        with capture_logs() as logs:
            await client.post(
                "/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": CLAUDE_CALLBACK,
                    "client_id": client_id,
                    "code_verifier": "x" * 43,
                },
            )

    [event] = [e for e in logs if e["event"] == "connector.token_refused"]
    assert event["reason"] == "pkce_mismatch"
    assert event["session_id"] == session_id
    assert code not in repr(logs)
