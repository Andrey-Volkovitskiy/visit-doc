"""Tests for the one processor chain every service logs through.

Driven through a stand-in settings object rather than any one service's `Settings`:
what is under test is the chain itself, which is service-agnostic by construction.
Each service's own wiring - that it declares every secret-bearing field it has - is
tested on that service's side.
"""

from dataclasses import dataclass
from typing import Any

import pytest
import structlog
from shared_logging.logging import (
    SafeLogger,
    _build_console_renderer,
    _redact_secrets,
    _truncate_long_strings,
    configure_logging,
    make_redact_secrets_processor,
)

_SECRET_FIELDS = ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY")
_SECRET_URL_FIELDS = ("DATABASE_URL", "QDRANT_URL")


@dataclass(frozen=True)
class _FakeSettings:
    """The two shapes of secret-bearing field the redaction processor reads."""

    DATABASE_URL: str = "postgresql+asyncpg://user:s3cr3t-pass@localhost/db"
    QDRANT_URL: str = "http://localhost:6333"
    ANTHROPIC_API_KEY: str = "sk-ant-test-key"
    VOYAGE_API_KEY: str = "voyage-test-key"


def _settings() -> _FakeSettings:
    return _FakeSettings()


def _processor() -> Any:
    return make_redact_secrets_processor(
        _settings(), _SECRET_FIELDS, _SECRET_URL_FIELDS
    )


def test_truncate_long_strings_clips_over_2000_chars() -> None:
    long_value = "a" * 2500
    event = {"event": "x", "chunk_text": long_value}

    result = _truncate_long_strings(None, "info", event)

    assert result["chunk_text"] == "a" * 2000 + "..."


def test_truncate_long_strings_leaves_short_strings_untouched() -> None:
    result = _truncate_long_strings(None, "info", {"event": "x", "message": "short"})

    assert result["message"] == "short"


def test_truncate_long_strings_recurses_into_lists_of_dicts() -> None:
    long_value = "b" * 2100
    chunks = [{"chunk_text": long_value, "score": 0.9}]
    event = {"event": "x", "retrieved_chunks": chunks}

    result = _truncate_long_strings(None, "info", event)

    assert result["retrieved_chunks"][0]["chunk_text"] == "b" * 2000 + "..."
    assert result["retrieved_chunks"][0]["score"] == 0.9


def test_redact_secrets_replaces_known_secret_value_anywhere_in_a_string() -> None:
    processor = _processor()
    detail = "connection failed: password=s3cr3t-pass"

    result = processor(None, "error", {"event": "x", "error_detail": detail})

    assert "s3cr3t-pass" not in result["error_detail"]
    assert "connection failed" in result["error_detail"]


def test_redact_secrets_replaces_known_api_key_value() -> None:
    processor = _processor()
    detail = "key=sk-ant-test-key"

    result = processor(None, "error", {"event": "x", "error_detail": detail})

    assert "sk-ant-test-key" not in result["error_detail"]


def test_redact_secrets_replaces_secret_named_key_regardless_of_value() -> None:
    event = {
        "api_key": "anything-at-all",
        "Authorization": "Bearer whatever",
        "safe": "kept",
    }

    result = _redact_secrets(event, known_secrets=[])

    assert result["api_key"] != "anything-at-all"
    assert result["Authorization"] != "Bearer whatever"
    assert result["safe"] == "kept"


def test_redact_secrets_key_matching_is_case_insensitive_and_substring() -> None:
    result = _redact_secrets({"DB_PASSWORD": "hunter2"}, known_secrets=[])

    assert result["DB_PASSWORD"] != "hunter2"


def test_configured_processor_chain_adds_timestamp_level_event() -> None:
    configure_logging(_settings(), _SECRET_FIELDS, _SECRET_URL_FIELDS)
    processors = structlog.get_config()["processors"]
    event: dict[str, Any] = {"event": "turn.message_received", "message": "hi"}

    for processor in processors[:-1]:  # every processor except the final renderer
        event = processor(None, "info", event)

    assert event["event"] == "turn.message_received"
    assert event["level"] == "info"
    assert "timestamp" in event


def test_safe_logger_swallows_processor_failures() -> None:
    class _RaisingLogger:
        def info(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("boom")

        def error(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("boom")

        def critical(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("boom")

    logger = SafeLogger(_RaisingLogger())

    logger.info("turn.message_received", message="hi")
    logger.error("turn.error", pipeline_step="retrieval")
    logger.critical("critical.dependency_unreachable", dependency="qdrant")


def test_safe_logger_fallback_notes_the_failure_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _RaisingLogger:
        def error(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("boom")

    SafeLogger(_RaisingLogger()).error("turn.error", pipeline_step="retrieval")

    captured = capsys.readouterr()
    assert "turn.error" in captured.err
    assert "boom" in captured.err


def test_console_renderer_error_more_prominent_than_info() -> None:
    renderer = _build_console_renderer()

    info_line = renderer(None, "info", {"event": "turn.completed", "level": "info"})
    error_line = renderer(None, "error", {"event": "turn.error", "level": "error"})

    assert "\x1b[31m" in error_line
    assert "\x1b[31m" not in info_line


def test_console_renderer_abstained_turn_carries_no_problem_styling() -> None:
    renderer = _build_console_renderer()
    abstained_event = {
        "event": "turn.completed",
        "level": "info",
        "outcome": "abstained",
    }
    error_line = renderer(None, "error", {"event": "turn.error", "level": "error"})

    abstained_line = renderer(None, "info", abstained_event)

    assert "\x1b[31m" not in abstained_line
    assert "\x1b[41m" not in abstained_line
    assert "\x1b[31m" in error_line


def test_console_renderer_critical_more_prominent_than_error() -> None:
    renderer = _build_console_renderer()
    critical_event = {"event": "critical.dependency_unreachable", "level": "critical"}

    error_line = renderer(None, "error", {"event": "turn.error", "level": "error"})
    critical_line = renderer(None, "critical", critical_event)

    assert "\x1b[41m" in critical_line
    assert "\x1b[41m" not in error_line
