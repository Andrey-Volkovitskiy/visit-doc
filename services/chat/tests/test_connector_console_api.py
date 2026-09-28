"""`/console/connected-apps`: where a staff member pairs an app and sees what is paired.

Scoped to the cookie's session like every console route: another session's grants are
not listed, and its grant ids do not resolve.
"""

from unittest.mock import patch

import pytest
from chat.clients.scheduling import SessionPurge
from chat.connectors.public_address import UnavailableReason
from chat.core.config import Settings
from chat.db.session import session_factory
from sqlalchemy import text

from .conftest import (
    CONNECTOR_BASE_URL,
    connector_api,
    new_session_id,
    pair_claude,
)

_ADDRESS = f"{CONNECTOR_BASE_URL}/mcp"


async def test_the_listing_without_a_session_cookie_is_404() -> None:
    async with connector_api() as client:
        response = await client.get("/console/connected-apps")

    assert response.status_code == 404


async def test_issuing_without_a_session_cookie_is_404() -> None:
    async with connector_api() as client:
        response = await client.post("/console/connected-apps/pairing-code")

    assert response.status_code == 404


async def test_a_new_session_sees_the_address_no_code_and_no_apps() -> None:
    session_id = await new_session_id()
    async with connector_api(session_id) as client:
        response = await client.get("/console/connected-apps")

    assert response.status_code == 200
    assert response.json() == {
        "connector": {"available": True, "address": _ADDRESS},
        "pairing_code": None,
        "grants": [],
    }


async def test_issuing_a_code_returns_it_once_with_its_lifetime_and_the_address() -> (
    None
):
    session_id = await new_session_id()
    async with connector_api(session_id) as client:
        issued = await client.post("/console/connected-apps/pairing-code")
        listed = await client.get("/console/connected-apps")

    assert issued.status_code == 201
    body = issued.json()
    assert set(body) == {"code", "expires_in_seconds", "address"}
    assert body["expires_in_seconds"] == 600
    assert body["address"] == _ADDRESS
    assert len(body["code"]) == 9 and body["code"][4] == "-"

    # FR-008: the listing reports the time left, never the code.
    pairing = listed.json()["pairing_code"]
    assert set(pairing) == {"expires_in_seconds"}
    assert 590 <= pairing["expires_in_seconds"] <= 600
    assert body["code"] not in listed.text


async def test_issuing_again_replaces_the_code() -> None:
    session_id = await new_session_id()
    async with connector_api(session_id) as client:
        first = (await client.post("/console/connected-apps/pairing-code")).json()
        second = (await client.post("/console/connected-apps/pairing-code")).json()

    assert first["code"] != second["code"]


@pytest.mark.parametrize(
    ("public_base_url", "reason"),
    [
        ("", UnavailableReason.NOT_CONFIGURED),
        ("http://visitdoc.test", UnavailableReason.NOT_HTTPS),
        ("https://visitdoc.test/chat", UnavailableReason.HAS_PATH),
    ],
)
async def test_an_unavailable_connector_issues_no_code_and_says_why(
    public_base_url: str, reason: UnavailableReason
) -> None:
    # FR-004.
    session_id = await new_session_id()
    async with connector_api(session_id, base_url=public_base_url) as client:
        issued = await client.post("/console/connected-apps/pairing-code")
        listed = await client.get("/console/connected-apps")

    assert issued.status_code == 409
    assert issued.json() == {"detail": reason.value}
    assert listed.json()["connector"] == {"available": False, "reason": reason.value}
    assert listed.json()["pairing_code"] is None
    async with session_factory() as session:
        count = await session.execute(text("SELECT count(*) FROM mcp_pairing_codes"))
    assert count.scalar_one() == 0


async def test_a_pairing_appears_in_the_list_as_never_used() -> None:
    session_id = await new_session_id()
    async with connector_api(session_id) as client:
        tokens = await pair_claude(client, session_id)
        listed = await client.get("/console/connected-apps")

    [grant] = listed.json()["grants"]
    assert set(grant) == {
        "id",
        "client_name",
        "paired_seconds_ago",
        "last_used_seconds_ago",
    }
    assert grant["client_name"] == "Claude"
    assert 0 <= grant["paired_seconds_ago"] <= 5
    assert grant["last_used_seconds_ago"] is None
    assert tokens["access_token"] not in listed.text
    assert tokens["refresh_token"] not in listed.text


async def test_another_sessions_pairing_is_not_listed() -> None:
    mine = await new_session_id()
    theirs = await new_session_id()
    async with connector_api(theirs) as client:
        await pair_claude(client, theirs)
    async with connector_api(mine) as client:
        listed = await client.get("/console/connected-apps")

    assert listed.json()["grants"] == []


async def test_pairings_are_listed_newest_first() -> None:
    session_id = await new_session_id()
    async with connector_api(session_id) as client:
        await pair_claude(client, session_id)
        async with session_factory() as session:
            await session.execute(
                text(
                    "UPDATE oauth_grants SET created_at = now() - interval '2 hours', "
                    "client_name = 'Older'"
                )
            )
            await session.commit()
        await pair_claude(client, session_id)
        grants = (await client.get("/console/connected-apps")).json()["grants"]

    assert [g["client_name"] for g in grants] == ["Claude", "Older"]
    assert 7195 <= grants[1]["paired_seconds_ago"] <= 7205


# --- revoking ----------------------------------------------------------------------


async def test_revoking_a_pairing_removes_it_and_is_idempotent() -> None:
    session_id = await new_session_id()
    async with connector_api(session_id) as client:
        await pair_claude(client, session_id)
        [grant] = (await client.get("/console/connected-apps")).json()["grants"]
        first = await client.post(f"/console/connected-apps/{grant['id']}/revoke")
        listed = (await client.get("/console/connected-apps")).json()["grants"]
        second = await client.post(f"/console/connected-apps/{grant['id']}/revoke")

    assert first.status_code == 204
    assert listed == []
    assert second.status_code == 204


async def test_another_sessions_pairing_cannot_be_revoked_from_here() -> None:
    mine = await new_session_id()
    theirs = await new_session_id()
    async with connector_api(theirs) as client:
        await pair_claude(client, theirs)
        [grant] = (await client.get("/console/connected-apps")).json()["grants"]
    async with connector_api(mine) as client:
        refused = await client.post(f"/console/connected-apps/{grant['id']}/revoke")
        unknown = await client.post(
            "/console/connected-apps/01NOSUCHGRANT0000000000000/revoke"
        )
    async with connector_api(theirs) as client:
        still = (await client.get("/console/connected-apps")).json()["grants"]

    assert refused.status_code == 404
    assert unknown.status_code == 404
    assert [g["id"] for g in still] == [grant["id"]]


async def test_revoking_without_a_session_cookie_is_404() -> None:
    async with connector_api() as client:
        response = await client.post(
            "/console/connected-apps/01NOSUCHGRANT0000000000000/revoke"
        )

    assert response.status_code == 404


async def test_a_pairing_idle_past_its_refresh_token_is_not_listed() -> None:
    session_id = await new_session_id()
    async with connector_api(session_id) as client:
        await pair_claude(client, session_id)
        async with session_factory() as session:
            await session.execute(
                text(
                    "UPDATE oauth_tokens SET expires_at = now() WHERE kind = 'refresh'"
                )
            )
            await session.commit()
        listed = (await client.get("/console/connected-apps")).json()["grants"]

    assert listed == []


async def _connector_rows(session_id: str) -> dict[str, int]:
    async with session_factory() as session:
        return {
            table: int(
                (
                    await session.execute(
                        text(f"SELECT count(*) FROM {table} WHERE {where}"),
                        {"s": session_id},
                    )
                ).scalar_one()
            )
            for table, where in (
                ("mcp_pairing_codes", "session_id = :s"),
                ("oauth_authorization_codes", "session_id = :s"),
                ("oauth_grants", "session_id = :s"),
                (
                    "oauth_tokens",
                    "grant_id IN (SELECT id FROM oauth_grants WHERE session_id = :s)",
                ),
            )
        }


async def test_deleting_the_session_ends_every_pairing_it_owned() -> None:
    # I9, FR-022: by cascade, with no connector code in the deletion.
    session_id = await new_session_id()
    async with connector_api(session_id) as client:
        tokens = await pair_claude(client, session_id)
        await client.post("/console/connected-apps/pairing-code")
        before = await _connector_rows(session_id)
        with (
            patch(
                "chat.api.admin.get_settings",
                return_value=Settings().model_copy(update={"ADMIN_SECRET": "s3cret"}),
            ),
            patch(
                "chat.api.admin.scheduling.delete_session",
                return_value=SessionPurge(
                    patients_deleted=0, practitioners_deleted=0, appointments_deleted=0
                ),
            ),
        ):
            deleted = await client.delete(
                f"/admin/sessions/{session_id}", headers={"X-Admin-Secret": "s3cret"}
            )
        after = await _connector_rows(session_id)
        refused = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"authorization": f"Bearer {tokens['access_token']}"},
        )

    assert deleted.status_code == 200, deleted.text
    assert before["oauth_grants"] == 1 and before["oauth_tokens"] == 2
    assert before["mcp_pairing_codes"] == 1
    assert after == dict.fromkeys(after, 0)
    assert refused.status_code == 401
