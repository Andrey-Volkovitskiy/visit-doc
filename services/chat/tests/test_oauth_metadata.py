"""The sign-in server's discovery document, and every OAuth route while unavailable.

A connector whose public address is unusable publishes nothing: it cannot name an
issuer that does not exist, so each route answers `503` with the reason instead.
"""

import pytest
from chat.connectors.public_address import UnavailableReason

from .conftest import CONNECTOR_BASE_URL, connector_api

_UNAVAILABLE_ROUTES = [
    ("GET", "/.well-known/oauth-authorization-server"),
    ("POST", "/oauth/register"),
    ("POST", "/oauth/authorize"),
    ("POST", "/oauth/token"),
]


async def test_the_metadata_document_is_the_contracts() -> None:
    async with connector_api() as client:
        response = await client.get("/.well-known/oauth-authorization-server")

    assert response.status_code == 200
    assert response.json() == {
        "issuer": CONNECTOR_BASE_URL,
        "authorization_endpoint": f"{CONNECTOR_BASE_URL}/oauth/authorize",
        "token_endpoint": f"{CONNECTOR_BASE_URL}/oauth/token",
        "registration_endpoint": f"{CONNECTOR_BASE_URL}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["console:read", "offline_access"],
    }


async def test_the_metadata_does_not_offer_client_metadata_documents() -> None:
    # R8: Claude is to register dynamically, as FR-010 names.
    async with connector_api() as client:
        body = (await client.get("/.well-known/oauth-authorization-server")).json()

    assert "client_id_metadata_document_supported" not in body


@pytest.mark.parametrize(
    ("public_base_url", "reason"),
    [
        ("", UnavailableReason.NOT_CONFIGURED),
        ("http://visitdoc.test", UnavailableReason.NOT_HTTPS),
        ("https://visitdoc.test/chat", UnavailableReason.HAS_PATH),
    ],
)
async def test_every_oauth_route_answers_503_with_the_reason_while_unavailable(
    public_base_url: str, reason: UnavailableReason
) -> None:
    async with connector_api(base_url=public_base_url) as client:
        responses = [
            await client.request(method, path) for method, path in _UNAVAILABLE_ROUTES
        ]
        page = await client.get("/oauth/authorize")

    for response in responses:
        assert response.status_code == 503, response.request.url
        assert response.json() == {
            "error": "temporarily_unavailable",
            "error_description": reason.value,
        }
    assert page.status_code == 503
    assert page.headers["content-type"].startswith("text/html")
    assert "location" not in page.headers


@pytest.mark.parametrize(
    ("public_base_url", "words"),
    [
        ("", "is not set"),
        ("http://visitdoc.test", "is not an https:// address"),
        ("https://visitdoc.test/chat", "must be an origin only, with no path"),
    ],
)
async def test_the_unavailable_sign_in_page_names_its_reason(
    public_base_url: str, words: str
) -> None:
    async with connector_api(base_url=public_base_url) as client:
        page = await client.get("/oauth/authorize")

    assert page.status_code == 503
    assert words in page.text
