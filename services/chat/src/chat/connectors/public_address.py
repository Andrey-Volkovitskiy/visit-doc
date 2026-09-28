"""Whether the staff connector exists, and every public address it publishes.

Claude's servers call the connector, so it needs an HTTPS address they can reach - the
browser asking for a pairing code is at `localhost`, which they cannot. That address is
configured once, as `PUBLIC_BASE_URL`, and this module is the only reader that decides
what it means: the console tab, the OAuth metadata and the MCP transport all take their
addresses from `connector_config`, so they cannot disagree about whether the connector
is there.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

from chat.core.config import Settings, get_settings
from chat.domain.schemas import UnavailableReason

# Where the MCP transport is served, under the configured origin.
MCP_PATH = "/mcp"
_DEFAULT_HTTPS_PORT = 443
# The parser the MCP SDK validates its issuer and resource URLs with, so the origin
# published here is the one it names.
_ORIGIN: TypeAdapter[AnyHttpUrl] = TypeAdapter(AnyHttpUrl)


@dataclass(frozen=True)
class ConnectorConfig:
    """The connector's public addresses, all derived from one origin.

    `issuer` is the origin itself, with no trailing slash: an OAuth issuer is compared
    as an exact string. `address` is the connector address a staff member pastes into
    Claude. `host` is the `Host` header value (hostname, plus the port unless it is
    443) the MCP transport accepts.
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

    The origin is published in the form a URL parser normalizes it to - a lower-case
    (and, for an international name, punycode) host, and no port when it is the
    default 443 - because the MCP SDK normalizes the issuer it names in the
    protected-resource document that way, an issuer is compared as an exact string,
    and a client sends that host, not the spelling configured. A value the parser
    rejects (a port that is not a number in range, a host with a space) is
    unavailable rather than left for the SDK to reject at startup.
    """
    value = settings.PUBLIC_BASE_URL.strip()
    if not value:
        return ConnectorUnavailable(reason=UnavailableReason.NOT_CONFIGURED)
    try:
        parts = urlsplit(value)
    except ValueError:
        # An unclosed IPv6 bracket (`https://[::1`) is refused by the split itself.
        return ConnectorUnavailable(reason=UnavailableReason.NOT_HTTPS)
    if parts.scheme.lower() != "https" or not parts.hostname:
        return ConnectorUnavailable(reason=UnavailableReason.NOT_HTTPS)
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return ConnectorUnavailable(reason=UnavailableReason.HAS_PATH)
    if "?" in value or "#" in value or "@" in parts.netloc:
        # An empty query or fragment (`https://host?`) parses to nothing, and still
        # names more than an origin; so does user info, which would otherwise be
        # published as part of the issuer.
        return ConnectorUnavailable(reason=UnavailableReason.HAS_PATH)
    try:
        origin = _ORIGIN.validate_python(f"https://{parts.netloc}")
    except ValidationError:
        return ConnectorUnavailable(reason=UnavailableReason.NOT_HTTPS)
    if origin.host is None:
        return ConnectorUnavailable(reason=UnavailableReason.NOT_HTTPS)
    host = (
        origin.host
        if origin.port in (None, _DEFAULT_HTTPS_PORT)
        else f"{origin.host}:{origin.port}"
    )
    issuer = f"https://{host}"
    return ConnectorConfig(issuer=issuer, address=f"{issuer}{MCP_PATH}", host=host)


def current_connector() -> ConnectorConfig | ConnectorUnavailable:
    """Return the connector as this process's settings configure it.

    The one runtime reader of `PUBLIC_BASE_URL`: every surface that publishes an
    address or refuses for want of one asks here.
    """
    return connector_config(get_settings())
