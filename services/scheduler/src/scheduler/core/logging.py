"""This service's logging entry points, over the shared processor chain.

The chain itself - truncation, redaction, rendering, and the never-raise wrapper - lives
in `shared_logging`, so it cannot drift between services. All that belongs here is what
is genuinely this service's own: which of its `Settings` fields hold secrets.
"""

from shared_logging import SafeLogger, get_logger
from shared_logging import configure_logging as _configure_logging

from scheduler.core.config import Settings

# Settings fields whose value is itself a secret. Add new secret fields here as they're
# introduced - both lists feed the redaction processor. Empty today: this service holds
# no bare-token credential, only the password embedded in its database URL below.
_SECRET_SETTINGS_FIELDS: tuple[str, ...] = ()
# Settings fields holding a URL whose embedded password (if any) is the secret.
_SECRET_URL_SETTINGS_FIELDS = ("SCHEDULER_DATABASE_URL",)


def configure_logging(settings: Settings) -> None:
    """Configure the shared structlog processor chain for this service."""
    _configure_logging(
        settings,
        secret_fields=_SECRET_SETTINGS_FIELDS,
        secret_url_fields=_SECRET_URL_SETTINGS_FIELDS,
        log_level=settings.LOG_LEVEL,
    )


__all__ = ["SafeLogger", "configure_logging", "get_logger"]
