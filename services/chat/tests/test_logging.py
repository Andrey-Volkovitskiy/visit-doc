"""This service's own logging wiring, over the shared chain.

The chain itself is tested in `packages/shared-logging/tests`. What is this service's
to get right - and what these tests hold it to - is that every secret-bearing field it
declares actually reaches the redaction processor.
"""

import structlog
from chat.core.config import Settings
from chat.core.logging import (
    _SECRET_SETTINGS_FIELDS,
    _SECRET_URL_SETTINGS_FIELDS,
    configure_logging,
)


def _settings() -> Settings:
    return Settings(
        DATABASE_URL="postgresql+asyncpg://user:s3cr3t-pass@localhost/db",
        QDRANT_URL="http://localhost:6333",
        ANTHROPIC_API_KEY="sk-ant-test-key",
        VOYAGE_API_KEY="voyage-test-key",
        ADMIN_SECRET="adm1n-s3cr3t-value",
    )


def _redact(event: dict[str, object]) -> dict[str, object]:
    """Run `event` through the configured chain, stopping before the renderer."""
    configure_logging(_settings())
    for processor in structlog.get_config()["processors"][:-1]:
        event = processor(None, "error", event)
    return event


def test_every_declared_secret_field_exists_on_settings() -> None:
    # A renamed or removed field would otherwise fail only at startup, inside the
    # redaction processor, on the first log line of a running service.
    settings = _settings()

    for field in (*_SECRET_SETTINGS_FIELDS, *_SECRET_URL_SETTINGS_FIELDS):
        assert hasattr(settings, field)


def test_this_services_api_keys_are_redacted_in_the_clear_text_of_a_log_line() -> None:
    result = _redact({"event": "turn.error", "error_detail": "key=sk-ant-test-key"})

    assert "sk-ant-test-key" not in str(result["error_detail"])


def test_this_services_database_password_is_redacted() -> None:
    result = _redact({"event": "turn.error", "error_detail": "auth: s3cr3t-pass"})

    assert "s3cr3t-pass" not in str(result["error_detail"])


def test_the_admin_secret_is_redacted_by_value_under_any_key_name() -> None:
    # The known-value arm of the redaction chain. The secret guards two routes that
    # delete every session, and it travels on a request header - so it can surface
    # inside an exception message under a key nobody thought to name "secret".
    result = _redact(
        {"event": "admin.refused", "detail": "sent adm1n-s3cr3t-value in a header"}
    )

    assert "adm1n-s3cr3t-value" not in str(result["detail"])


def test_the_admin_secret_is_redacted_by_key_name_regardless_of_value() -> None:
    # The key-name arm, which catches a secret introduced before anyone adds it to the
    # known-value list. Neither arm alone is sufficient.
    result = _redact({"event": "admin.refused", "admin_secret": "anything-at-all"})

    assert "anything-at-all" not in str(result["admin_secret"])
