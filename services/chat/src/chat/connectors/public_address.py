"""Whether the staff connector exists, and every public address it publishes.

Claude's servers call the connector, so it needs an HTTPS address they can reach - the
browser asking for a pairing code is at `localhost`, which they cannot. That address is
configured once, as `PUBLIC_BASE_URL`, and this module is the only reader that decides
what it means: the console tab, the OAuth metadata and the MCP transport all take their
addresses from `connector_config`, so they cannot disagree about whether the connector
is there.
"""

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from chat.core.config import Settings, get_settings

# Where the MCP transport is served, under the configured origin.
MCP_PATH = "/mcp"


class UnavailableReason(StrEnum):
    """Why the connector does not exist in this process."""

    # `PUBLIC_BASE_URL` is blank.
    NOT_CONFIGURED = "not_configured"
    # It is not an `https://` URL naming a host.
    NOT_HTTPS = "not_https"
    # It names more than an origin: a path, a query or a fragment.
    HAS_PATH = "has_path"


@dataclass(frozen=True)
class ConnectorConfig:
    """The connector's public addresses, all derived from one origin.

    `issuer` is the origin itself, with no trailing slash: an OAuth issuer is compared
    as an exact string. `address` is the connector address a staff member pastes into
    Claude. `host` is the `Host` header value (hostname, plus a port if one was
    given) the MCP transport accepts.
    """

    issuer: str
    address: str
    host: str


@dataclass(frozen=True)
class ConnectorUnavailable:
    """The connector does not exist, and why."""

    reason: UnavailableReason


def connector_config(settings: Settings) -> ConnectorConfig | ConnectorUnavailable:
    """Decide from `PUBLIC_BASE_URL` whether the connector exists, and where.

    Available only for a bare `https://` origin. A single trailing slash names no path
    and is dropped; surrounding whitespace is ignored.
    """
    value = settings.PUBLIC_BASE_URL.strip()
    if not value:
        return ConnectorUnavailable(reason=UnavailableReason.NOT_CONFIGURED)
    parts = urlsplit(value)
    if parts.scheme.lower() != "https" or not parts.hostname:
        return ConnectorUnavailable(reason=UnavailableReason.NOT_HTTPS)
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return ConnectorUnavailable(reason=UnavailableReason.HAS_PATH)
    if "?" in value or "#" in value:
        # An empty query or fragment (`https://host?`) parses to nothing, and still
        # names more than an origin.
        return ConnectorUnavailable(reason=UnavailableReason.HAS_PATH)
    issuer = f"https://{parts.netloc}"
    return ConnectorConfig(
        issuer=issuer, address=f"{issuer}{MCP_PATH}", host=parts.netloc
    )


def current_connector() -> ConnectorConfig | ConnectorUnavailable:
    """Return the connector as this process's settings configure it.

    The one runtime reader of `PUBLIC_BASE_URL`: every surface that publishes an
    address or refuses for want of one asks here.
    """
    return connector_config(get_settings())
