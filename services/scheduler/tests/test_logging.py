"""This service's own logging wiring, over the shared chain.

The chain itself is tested in `packages/shared-logging/tests`. What is this service's
to get right - and what these tests hold it to - is that every secret-bearing field it
declares actually reaches the redaction processor.
"""

import structlog
from scheduler.core.config import Settings
from scheduler.core.logging import (
    _SECRET_SETTINGS_FIELDS,
    _SECRET_URL_SETTINGS_FIELDS,
    configure_logging,
)


def _settings() -> Settings:
    return Settings(
        SCHEDULER_DATABASE_URL="postgresql+asyncpg://user:s3cr3t-pass@localhost/db"
    )


def test_every_declared_secret_field_exists_on_settings() -> None:
    # A renamed or removed field would otherwise fail only at startup, inside the
    # redaction processor, on the first log line of a running service.
    settings = _settings()

    for field in (*_SECRET_SETTINGS_FIELDS, *_SECRET_URL_SETTINGS_FIELDS):
        assert hasattr(settings, field)


def test_this_services_database_password_is_redacted() -> None:
    configure_logging(_settings())
    event: dict[str, object] = {
        "event": "rpc.completed",
        "error_detail": "auth failed for s3cr3t-pass",
    }

    for processor in structlog.get_config()["processors"][:-1]:
        event = processor(None, "error", event)

    assert "s3cr3t-pass" not in str(event["error_detail"])
