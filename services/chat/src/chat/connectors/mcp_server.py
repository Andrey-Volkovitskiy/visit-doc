"""The connector's MCP server: two read-only counts, behind this service's tokens.

The SDK carries the protocol and the resource-server half of OAuth - the `401` with its
`WWW-Authenticate` header, the protected-resource document, the bearer check through
`GrantTokenVerifier`. Every address it is given comes from `ConnectorConfig`, so the
issuer it names is the one `api/oauth.py` publishes.

Built once per lifespan rather than at import: the SDK's session manager runs once per
instance, and a test suite runs the lifespan many times.

The tools take no session, chat or grant argument. The session each answers for is the
grant's, read from the verified token, so a tool cannot be pointed at another session
by anything a caller sends. Their results are counts and nothing else: no name, no
message text, no id.
"""

from dataclasses import dataclass
from typing import Annotated, Any
from urllib.parse import urlsplit

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools import Tool
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, WithJsonSchema
from starlette.applications import Starlette

from chat.connectors.authorization import CLAUDE_REDIRECT_URI, CONNECTOR_SCOPE
from chat.connectors.public_address import MCP_PATH, ConnectorConfig
from chat.connectors.recent_changes_text import describe_recent_changes
from chat.connectors.token_verifier import GRANT_ID_CLAIM, GrantTokenVerifier
from chat.core.logging import get_logger
from chat.db.session import session_factory
from chat.repositories import booking_act_repository, chat_repository

SERVER_NAME = "VisitDoc Staff Console"
# The origin of the one client this connector serves, allowed in case Claude's calls to
# `/mcp` carry it - unknown until the live walk; research R4 says how that is settled.
CLAUDE_ORIGIN = "https://" + (urlsplit(CLAUDE_REDIRECT_URI).hostname or "claude.ai")
# The path the SDK serves the protected-resource document at, for the resource
# `{issuer}/mcp` (RFC 9728 §3.1).
PROTECTED_RESOURCE_PATH = f"/.well-known/oauth-protected-resource{MCP_PATH}"


MIN_WINDOW_MINUTES = 1
# Seven days.
MAX_WINDOW_MINUTES = 10080
_WINDOW_ERROR = (
    f"minutes must be a whole number from {MIN_WINDOW_MINUTES} to "
    f"{MAX_WINDOW_MINUTES} (7 days)."
)
_WINDOW_SCHEMA = {
    "type": "integer",
    "minimum": MIN_WINDOW_MINUTES,
    "maximum": MAX_WINDOW_MINUTES,
}

NEEDING_ATTENTION_TOOL = "count_conversations_needing_attention"
RECENT_CHANGES_TOOL = "count_recent_booking_changes"
_NEEDING_ATTENTION_DESCRIPTION = (
    "Returns how many of the clinic's conversations are waiting for a staff member "
    "right now: the ones the Staff Console marks as needing attention. Returns a count "
    "only, never which conversations or what they say."
)
_RECENT_CHANGES_DESCRIPTION = (
    "Returns, for the last `minutes` minutes, how many appointments were booked, "
    "cancelled and rescheduled, one count per kind, and how many changes have an "
    "outcome nobody knows yet (the scheduler's answer never arrived). Convert hours to "
    "minutes yourself: the last 2 hours is `minutes: 120`. Refused and unchanged "
    "attempts are not counted, because they changed nothing. The result includes a "
    "sentence summarizing the counts; answer with that sentence as it is. Returns "
    "counts only."
)
# Both tools read and never write, so an app may call them without asking.
_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


class NeedingAttention(BaseModel):
    """The needs-attention tool's structured result: a count, and nothing else."""

    needing_attention: int


class RecentChanges(BaseModel):
    """The recent-changes tool's structured result: the window and four counts."""

    window_minutes: int
    booked: int
    cancelled: int
    rescheduled: int
    outcome_unknown: int


@dataclass(frozen=True)
class _Caller:
    """Who a tool call speaks for: the verified grant's session, and the grant."""

    session_id: str
    grant_id: str | None


def _caller() -> _Caller:
    """Return the session and grant this call's verified token speaks for.

    Raises: ToolError if the call carries none, which the transport's own check makes
        unreachable - a tool is never run for an unauthenticated request.
    """
    token = get_access_token()
    if token is None or token.subject is None:
        raise ToolError("This connector answers only a paired app.")
    grant_id = (token.claims or {}).get(GRANT_ID_CLAIM)
    return _Caller(
        session_id=token.subject,
        grant_id=grant_id if isinstance(grant_id, str) else None,
    )


def _log_call(caller: _Caller, tool: str) -> None:
    get_logger().info(
        "connector.tool_called",
        session_id=caller.session_id,
        grant_id=caller.grant_id,
        tool=tool,
    )


def _attention_sentence(count: int) -> str:
    if count == 0:
        return "No conversations need staff attention."
    if count == 1:
        return "1 conversation needs staff attention."
    return f"{count} conversations need staff attention."


async def count_conversations_needing_attention() -> Annotated[
    CallToolResult, NeedingAttention
]:
    """Count the caller's session's conversations that need a person right now."""
    caller = _caller()
    async with session_factory() as session:
        count = await chat_repository.count_needing_attention(
            session, caller.session_id
        )
    _log_call(caller, NEEDING_ATTENTION_TOOL)
    return CallToolResult(
        content=[TextContent(type="text", text=_attention_sentence(count))],
        structured_content=NeedingAttention(needing_attention=count).model_dump(),
    )


async def count_recent_booking_changes(
    minutes: Annotated[Any, WithJsonSchema(_WINDOW_SCHEMA)],
) -> Annotated[CallToolResult, RecentChanges]:
    """Count the caller's session's schedule changes in the last `minutes`.

    Raises: ToolError naming the range when `minutes` is not a whole number from 1 to
        10080; the window is never clamped to a different one.

    `minutes` is declared as any value and checked here, so a refusal says the range in
    this connector's words rather than a validation library's.
    """
    caller = _caller()
    if (
        not isinstance(minutes, int)
        or isinstance(minutes, bool)
        or not MIN_WINDOW_MINUTES <= minutes <= MAX_WINDOW_MINUTES
    ):
        get_logger().info(
            "connector.tool_refused",
            session_id=caller.session_id,
            grant_id=caller.grant_id,
            tool=RECENT_CHANGES_TOOL,
            reason="window_out_of_range",
        )
        raise ToolError(_WINDOW_ERROR)
    async with session_factory() as session:
        changes = await booking_act_repository.count_recent_changes(
            session, caller.session_id, minutes
        )
    _log_call(caller, RECENT_CHANGES_TOOL)
    return CallToolResult(
        content=[TextContent(type="text", text=describe_recent_changes(changes))],
        structured_content=RecentChanges(
            window_minutes=changes.window_minutes,
            booked=changes.booked,
            cancelled=changes.cancelled,
            rescheduled=changes.rescheduled,
            outcome_unknown=changes.outcome_unknown,
        ).model_dump(),
    )


def _closed_tool(
    fn: Any, name: str, description: str, properties: dict[str, Any]
) -> Tool:
    """Build a read-only tool whose input schema is exactly `properties`, closed.

    The SDK derives a schema from the signature that titles every field and leaves
    extra properties open; the one published here is the contract's.
    """
    tool = Tool.from_function(
        fn,
        name=name,
        description=description,
        annotations=_READ_ONLY,
        structured_output=True,
    )
    return tool.model_copy(
        update={
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": sorted(properties),
                "additionalProperties": False,
            }
        }
    )


def build_tools() -> list[Tool]:
    """The connector's two tools, and no others."""
    return [
        _closed_tool(
            count_conversations_needing_attention,
            NEEDING_ATTENTION_TOOL,
            _NEEDING_ATTENTION_DESCRIPTION,
            {},
        ),
        _closed_tool(
            count_recent_booking_changes,
            RECENT_CHANGES_TOOL,
            _RECENT_CHANGES_DESCRIPTION,
            {"minutes": _WINDOW_SCHEMA},
        ),
    ]


def build_server(config: ConnectorConfig) -> MCPServer:
    """Build the MCP server for `config`'s addresses, holding the two tools."""
    return MCPServer(
        SERVER_NAME,
        tools=build_tools(),
        token_verifier=GrantTokenVerifier(config),
        auth=AuthSettings(
            issuer_url=config.issuer,
            resource_server_url=config.address,
            required_scopes=[CONNECTOR_SCOPE],
            # The verifier's lookup is the audience check: every token this service
            # issues is for its one resource, so there is no other audience to refuse.
            validate_token_resource=False,
        ),
    )


def build_transport(server: MCPServer, config: ConnectorConfig) -> Starlette:
    """Build the ASGI app serving `/mcp` and its protected-resource document.

    Stateless, answering in plain JSON: each call is one count query, so there is no
    session to keep and nothing to stream. The transport accepts only the configured
    host, which is what ngrok forwards. A request with no `Origin` - a server-to-server
    call - passes the origin check; one with an `Origin` must be this service's own or
    Claude's.
    """
    return server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[config.host],
            allowed_origins=[config.issuer, CLAUDE_ORIGIN],
        ),
    )
