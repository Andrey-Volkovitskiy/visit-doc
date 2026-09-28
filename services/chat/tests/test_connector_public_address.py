"""`connector_config`: whether the staff connector exists, and what it publishes.

One pure function decides availability from `PUBLIC_BASE_URL`, so the console tab, the
OAuth metadata and the MCP transport cannot disagree about it. Claude connects only
over HTTPS, and everything public is the origin plus a fixed path, so anything but a
bare `https://` origin is unavailable, with the reason the tab words.
"""

import pytest
from chat.connectors.public_address import (
    ConnectorConfig,
    ConnectorUnavailable,
    UnavailableReason,
    connector_config,
)
from chat.core.config import Settings


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
    ],
)
def test_anything_but_a_bare_https_origin_is_unavailable_with_its_reason(
    value: str, reason: UnavailableReason
) -> None:
    assert _config(value) == ConnectorUnavailable(reason=reason)


def test_the_reasons_are_the_three_the_console_words() -> None:
    # The SPA words each value; one added here without a wording there would reach the
    # tab as a raw token.
    assert {reason.value for reason in UnavailableReason} == {
        "not_configured",
        "not_https",
        "has_path",
    }
