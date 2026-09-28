"""`connector_config`: whether the staff connector exists, and what it publishes.

One pure function decides availability from `PUBLIC_BASE_URL`, so the console tab, the
OAuth metadata and the MCP transport cannot disagree about it. Claude connects only
over HTTPS, and everything public is the origin plus a fixed path, so anything but a
bare `https://` origin is unavailable, with the reason the tab words.
"""

import pytest
from chat.connectors.public_address import (
    MCP_PATH,
    ConnectorConfig,
    ConnectorUnavailable,
    connector_config,
)
from chat.core.config import Settings
from chat.domain.schemas import UnavailableReason
from mcp.server.auth.settings import AuthSettings


def _config(value: str) -> ConnectorConfig | ConnectorUnavailable:
    return connector_config(Settings().model_copy(update={"PUBLIC_BASE_URL": value}))


@pytest.mark.parametrize(
    ("value", "issuer", "address", "host"),
    [
        (
            "https://visitdoc.ngrok.app",
            "https://visitdoc.ngrok.app",
            "https://visitdoc.ngrok.app/mcp",
            "visitdoc.ngrok.app",
        ),
        (
            "https://visitdoc.ngrok.app:8443",
            "https://visitdoc.ngrok.app:8443",
            "https://visitdoc.ngrok.app:8443/mcp",
            "visitdoc.ngrok.app:8443",
        ),
        # A trailing slash is how an origin is often pasted; it names no path, and the
        # issuer is compared as an exact string, so it is published without one.
        (
            "https://visitdoc.ngrok.app/",
            "https://visitdoc.ngrok.app",
            "https://visitdoc.ngrok.app/mcp",
            "visitdoc.ngrok.app",
        ),
        (
            "  https://visitdoc.ngrok.app  ",
            "https://visitdoc.ngrok.app",
            "https://visitdoc.ngrok.app/mcp",
            "visitdoc.ngrok.app",
        ),
        # A client sends the host in lower case and leaves the default port out, and
        # the SDK names the issuer the same way, so both are published normalized.
        (
            "https://VisitDoc.Ngrok.App",
            "https://visitdoc.ngrok.app",
            "https://visitdoc.ngrok.app/mcp",
            "visitdoc.ngrok.app",
        ),
        (
            "https://visitdoc.ngrok.app:443",
            "https://visitdoc.ngrok.app",
            "https://visitdoc.ngrok.app/mcp",
            "visitdoc.ngrok.app",
        ),
        (
            "https://[::1]:8443",
            "https://[::1]:8443",
            "https://[::1]:8443/mcp",
            "[::1]:8443",
        ),
    ],
)
def test_a_bare_https_origin_makes_the_connector_available(
    value: str, issuer: str, address: str, host: str
) -> None:
    assert _config(value) == ConnectorConfig(issuer=issuer, address=address, host=host)


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("", UnavailableReason.NOT_CONFIGURED),
        ("   ", UnavailableReason.NOT_CONFIGURED),
        ("http://visitdoc.ngrok.app", UnavailableReason.NOT_HTTPS),
        ("visitdoc.ngrok.app", UnavailableReason.NOT_HTTPS),
        ("ftp://visitdoc.ngrok.app", UnavailableReason.NOT_HTTPS),
        ("https://", UnavailableReason.NOT_HTTPS),
        ("https://visitdoc.ngrok.app/chat", UnavailableReason.HAS_PATH),
        ("https://visitdoc.ngrok.app/mcp", UnavailableReason.HAS_PATH),
        ("https://visitdoc.ngrok.app?x=1", UnavailableReason.HAS_PATH),
        ("https://visitdoc.ngrok.app#top", UnavailableReason.HAS_PATH),
        # User info would be published inside the issuer.
        ("https://user:secret@visitdoc.ngrok.app", UnavailableReason.HAS_PATH),
        # Values the URL parser refuses: left available, each crashed the lifespan
        # when the MCP SDK validated the issuer built from it.
        ("https://visitdoc.ngrok.app:abc", UnavailableReason.NOT_HTTPS),
        ("https://visitdoc.ngrok.app:99999", UnavailableReason.NOT_HTTPS),
        ("https://visit doc.ngrok.app", UnavailableReason.NOT_HTTPS),
        ("https://[::1", UnavailableReason.NOT_HTTPS),
    ],
)
def test_anything_but_a_bare_https_origin_is_unavailable_with_its_reason(
    value: str, reason: UnavailableReason
) -> None:
    assert _config(value) == ConnectorUnavailable(reason=reason)


@pytest.mark.parametrize(
    "value",
    [
        "https://visitdoc.ngrok.app",
        "https://VisitDoc.Ngrok.App:443/",
        "https://visitdoc.ngrok.app:8443",
        "https://[::1]:8443",
        "https://bücher.example",
    ],
)
def test_the_issuer_is_the_one_the_sdk_names(value: str) -> None:
    # The protected-resource document lists the issuer as the SDK normalizes it, and a
    # client compares that string with the metadata's `issuer` exactly.
    config = _config(value)
    assert isinstance(config, ConnectorConfig)

    settings = AuthSettings(
        issuer_url=config.issuer,
        resource_server_url=config.address,
        validate_token_resource=False,
    )

    assert str(settings.issuer_url) == config.issuer
    assert str(settings.resource_server_url) == config.address
    assert config.address == f"{config.issuer}{MCP_PATH}"


def test_the_reasons_are_the_three_the_console_words() -> None:
    # The SPA words each value; one added here without a wording there would reach the
    # tab as a raw token.
    assert {reason.value for reason in UnavailableReason} == {
        "not_configured",
        "not_https",
        "has_path",
    }
