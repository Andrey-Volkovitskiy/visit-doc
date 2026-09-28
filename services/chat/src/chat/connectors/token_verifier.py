"""The MCP SDK's token check, answered from this service's grants.

The one place the SDK learns who is calling: the grant's session goes into the
`AccessToken`'s `subject`, and a tool reads it from there and from nowhere else.
"""

from mcp.server.auth.provider import AccessToken

from chat.connectors.public_address import ConnectorConfig
from chat.core.logging import get_logger
from chat.db.session import session_factory
from chat.repositories import grant_repository

# The claim under which a verified token carries its grant's id.
GRANT_ID_CLAIM = "grant_id"


class GrantTokenVerifier:
    """A `TokenVerifier` that looks a bearer token up among this service's grants."""

    def __init__(self, config: ConnectorConfig) -> None:
        self._config = config

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return what `token` speaks for, or None if it speaks for nothing.

        None for an unknown, expired or refresh token and for a revoked grant's, which
        the SDK answers with `401`. Each call is one lookup, so a revocation takes
        effect on the next call rather than when a token would have expired. A refusal
        of a token this service issued is logged against its grant and session; one it
        never issued concerns no session and is not.
        """
        async with session_factory() as session:
            verified = await grant_repository.verify_access(session, token)
            refused = (
                await grant_repository.find_refused_access(session, token)
                if verified is None
                else None
            )
            await session.commit()
        if refused is not None:
            get_logger().info(
                "connector.token_rejected",
                session_id=refused.session_id,
                grant_id=refused.grant_id,
                reason=refused.reason,
            )
        if verified is None:
            return None
        return AccessToken(
            token=token,
            client_id=verified.client_id,
            scopes=verified.scope.split(),
            expires_at=verified.expires_at,
            resource=self._config.address,
            subject=verified.session_id,
            claims={GRANT_ID_CLAIM: verified.grant_id},
        )
