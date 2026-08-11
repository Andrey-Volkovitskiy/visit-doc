"""Structlog configuration: the one centralized place log entries are shaped/rendered.

Every log call flows through one processor chain (merge correlation id -> add level ->
add timestamp -> truncate -> redact -> render), so switching the rendering later is a
one-line change here, not a rewrite of every call site.
"""

import re
import sys
from collections.abc import Callable
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit

import structlog
from structlog.types import EventDict, WrappedLogger

from chat.core.config import Settings

_LogProcessor = Callable[[WrappedLogger, str, EventDict], EventDict]

# Settings fields whose value is itself a secret (FR-017 known-value matching). Add new
# secret fields here as they're introduced - _known_secret_values() reads both lists.
_SECRET_SETTINGS_FIELDS = ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY")
# Settings fields holding a URL whose embedded password (if any) is the secret.
_SECRET_URL_SETTINGS_FIELDS = ("DATABASE_URL", "QDRANT_URL")

_MAX_STRING_LENGTH = 2000
_TRUNCATION_SUFFIX = "..."
_REDACTED_PLACEHOLDER = "***REDACTED***"
_SECRET_KEY_PATTERN = re.compile(
    r"(password|token|secret|api_key|apikey|credential|authorization)", re.IGNORECASE
)
_LEVEL_STYLES = {
    "info": "",
    "error": "\033[31m",  # red foreground
    "critical": "\033[1m\033[41m\033[97m",  # bold, red background, bright white text
}


def _truncate_value(value: Any) -> Any:
    """Recursively truncate any string over 2,000 characters."""
    if isinstance(value, str) and len(value) > _MAX_STRING_LENGTH:
        return value[:_MAX_STRING_LENGTH] + _TRUNCATION_SUFFIX
    if isinstance(value, list):
        return [_truncate_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _truncate_value(item) for key, item in value.items()}
    return value


def _truncate_long_strings(
    _logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    """Structlog processor applying `_truncate_value` to every field."""
    return {key: _truncate_value(value) for key, value in event_dict.items()}


def _redact_secrets(event_dict: EventDict, known_secrets: list[str]) -> EventDict:
    """Redact `event_dict` by key name and by known live secret value.

    Two independent checks, since neither alone is sufficient: a key whose name looks
    like a secret is redacted regardless of its value, and any string value matching a
    known live secret is redacted regardless of its key name.
    """
    result: dict[str, Any] = {}
    for key, value in event_dict.items():
        if _SECRET_KEY_PATTERN.search(key):
            result[key] = _REDACTED_PLACEHOLDER
            continue
        result[key] = _redact_value(value, known_secrets)
    return result


def _redact_value(value: Any, known_secrets: list[str]) -> Any:
    """Recursively replace any occurrence of a known secret value in `value`."""
    if isinstance(value, str):
        for secret in known_secrets:
            value = value.replace(secret, _REDACTED_PLACEHOLDER)
        return value
    if isinstance(value, list):
        return [_redact_value(item, known_secrets) for item in value]
    if isinstance(value, dict):
        return _redact_secrets(value, known_secrets)
    return value


def _known_secret_values(settings: Settings) -> list[str]:
    """Return the service's own live secret values, to redact on sight."""
    values = [getattr(settings, field) for field in _SECRET_SETTINGS_FIELDS]
    for field in _SECRET_URL_SETTINGS_FIELDS:
        password = urlsplit(getattr(settings, field)).password
        if password:
            values.append(password)
    return [value for value in values if value]


def make_redact_secrets_processor(settings: Settings) -> _LogProcessor:
    """Build a redaction processor bound to `settings`' live secret values."""
    known_secrets = _known_secret_values(settings)

    def _processor(
        _logger: WrappedLogger, _method_name: str, event_dict: EventDict
    ) -> EventDict:
        return _redact_secrets(event_dict, known_secrets)

    return _processor


def _build_console_renderer() -> structlog.dev.ConsoleRenderer:
    """Build the one terminal renderer: critical > error > info."""
    return structlog.dev.ConsoleRenderer(level_styles=_LEVEL_STYLES, colors=True)


def configure_logging(settings: Settings) -> None:
    """Configure the shared structlog processor chain."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
            _truncate_long_strings,
            make_redact_secrets_processor(settings),
            _build_console_renderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def _log_pipeline_failure(level: str, event: str, exc: Exception) -> None:
    """Best-effort stderr fallback so a dropped log entry isn't entirely invisible.

    Never raises or retries itself - if even this fails, that failure is swallowed
    too, since servicing the visitor's request still takes priority.
    """
    with suppress(Exception):
        message = f"[Error: logging pipeline failed] {level} {event}: {exc!r}"
        print(message, file=sys.stderr)


class _SafeLogger:
    """Wrap a structlog logger so a processor failure never reaches the caller.

    Servicing the visitor's request takes priority over guaranteeing delivery of any
    single log entry - a failure here is never retried or surfaced to the caller, only
    best-effort noted via `_log_pipeline_failure`'s stderr fallback.
    """

    def __init__(self, logger: WrappedLogger) -> None:
        self._logger = logger

    def info(self, event: str, **kwargs: Any) -> None:
        """Log `event` at info level."""
        self._log("info", event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        """Log `event` at error level."""
        self._log("error", event, **kwargs)

    def critical(self, event: str, **kwargs: Any) -> None:
        """Log `event` at critical level."""
        self._log("critical", event, **kwargs)

    def _log(self, level: str, event: str, **kwargs: Any) -> None:
        """Log `event` at `level`, swallowing any processor-chain failure."""
        try:
            getattr(self._logger, level)(event, **kwargs)
        except Exception as exc:  # noqa: BLE001 - a dropped entry is an accepted tradeoff
            _log_pipeline_failure(level, event, exc)


def get_logger(**initial_values: Any) -> _SafeLogger:
    """Return a logger whose `.info`/`.error`/`.critical` calls never raise."""
    return _SafeLogger(structlog.get_logger(**initial_values))
