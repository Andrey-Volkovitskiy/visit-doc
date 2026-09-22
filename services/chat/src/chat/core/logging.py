"""This service's logging entry points, over the shared processor chain.

The chain itself - truncation, redaction, rendering, and the never-raise wrapper - lives
in `shared_logging`, so it cannot drift between services. All that belongs here is what
is genuinely this service's own: which of its `Settings` fields hold secrets.
"""

from shared_logging import SafeLogger, get_logger
from shared_logging import configure_logging as _configure_logging
from shared_logging import known_secret_values as _known_secret_values

from chat.core.config import Settings

# Settings fields whose value is itself a secret (FR-017 known-value matching). Add new
# secret fields here as they're introduced - both lists feed the redaction processor.
_SECRET_SETTINGS_FIELDS = (
    "ANTHROPIC_API_KEY",
    "VOYAGE_API_KEY",
    "ADMIN_SECRET",
    "LANGFUSE_SECRET_KEY",
)
# Settings fields holding a URL whose embedded password (if any) is the secret.
_SECRET_URL_SETTINGS_FIELDS = ("DATABASE_URL", "QDRANT_URL")


def configure_logging(settings: Settings) -> None:
    """Configure the shared structlog processor chain for this service."""
    _configure_logging(
        settings,
        secret_fields=_SECRET_SETTINGS_FIELDS,
        secret_url_fields=_SECRET_URL_SETTINGS_FIELDS,
        log_level=settings.LOG_LEVEL,
        log_format=settings.LOG_FORMAT,
    )


def known_secret_values(settings: Settings) -> list[str]:
    """Return this service's live secret values: what redaction matches by value.

    The same two field lists the log's processor is built from, so anything else that
    redacts - the trace export - matches exactly the values the log does.
    """
    return _known_secret_values(
        settings, _SECRET_SETTINGS_FIELDS, _SECRET_URL_SETTINGS_FIELDS
    )


__all__ = ["SafeLogger", "configure_logging", "get_logger", "known_secret_values"]
