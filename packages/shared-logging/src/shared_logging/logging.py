"""Structlog configuration: the one centralized place log entries are shaped/rendered.

Every log call flows through one processor chain (merge correlation id -> add level ->
add timestamp -> truncate -> redact -> render), so switching the rendering later is a
one-line change here, not a rewrite of every call site.
"""

import logging
import re
import sys
from collections.abc import Callable, Sequence
from contextlib import suppress
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

import structlog
from structlog.types import EventDict, WrappedLogger

_LogProcessor = Callable[[WrappedLogger, str, EventDict], EventDict]
_ConsoleRenderer = Callable[[WrappedLogger, str, EventDict], str]

_MAX_STRING_LENGTH = 2000
_TRUNCATION_SUFFIX = "..."
_REDACTED_PLACEHOLDER = "***REDACTED***"
_SECRET_KEY_PATTERN = re.compile(
    r"(password|token|secret|api_key|apikey|credential|authorization)", re.IGNORECASE
)
# Faint grey: debug entries are diagnostic detail sitting between the events that
# describe what the service did, so they have to be skimmable past rather than read.
_DIM = "\033[2m\033[90m"
_RESET = "\033[0m"
_ANSI_SEQUENCE = re.compile(r"\033\[[0-9;]*m")
_LEVEL_STYLES = {
    "debug": _DIM,
    "info": "",
    "warning": "\033[33m",  # yellow foreground
    "error": "\033[31m",  # red foreground
    "critical": "\033[1m\033[41m\033[97m",  # bold, red background, bright white text
}


class LogLevel(StrEnum):
    """The levels a service may be configured to emit, lowest threshold first."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


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


def _known_secret_values(
    settings: object,
    secret_fields: Sequence[str],
    secret_url_fields: Sequence[str],
) -> list[str]:
    """Return the caller's own live secret values, to redact on sight.

    Args:
        secret_fields: Settings field names whose value is itself a secret.
        secret_url_fields: Settings field names holding a URL whose embedded password
            (if any) is the secret.
    """
    values = [getattr(settings, field) for field in secret_fields]
    for field in secret_url_fields:
        password = urlsplit(getattr(settings, field)).password
        if password:
            values.append(password)
    return [value for value in values if value]


def make_redact_secrets_processor(
    settings: object,
    secret_fields: Sequence[str] = (),
    secret_url_fields: Sequence[str] = (),
) -> _LogProcessor:
    """Build a redaction processor bound to `settings`' live secret values."""
    known_secrets = _known_secret_values(settings, secret_fields, secret_url_fields)

    def _processor(
        _logger: WrappedLogger, _method_name: str, event_dict: EventDict
    ) -> EventDict:
        """Replace any of `settings`' live secret values found in `event_dict`."""
        return _redact_secrets(event_dict, known_secrets)

    return _processor


def _build_console_renderer() -> _ConsoleRenderer:
    """Build the one terminal renderer: critical > error > warning > info > debug.

    A debug entry is dimmed whole rather than only in its level column, which is what
    makes it lower-priority to read: at DEBUG the log is mostly diagnostics, and the
    events saying what the service actually did still have to be findable in it. The
    line's own colours are stripped before dimming - each ends in a reset, which would
    otherwise end the dimming at the first key the renderer coloured.
    """
    renderer = structlog.dev.ConsoleRenderer(level_styles=_LEVEL_STYLES, colors=True)

    def _render(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> str:
        """Render one entry, dimming it whole when it was logged at debug level."""
        line = renderer(logger, method_name, event_dict)
        if method_name != "debug":
            return line
        return f"{_DIM}{_ANSI_SEQUENCE.sub('', line)}{_RESET}"

    return _render


def _threshold(level: LogLevel) -> int:
    """Return the numeric filtering threshold for `level`."""
    return logging.getLevelNamesMapping()[level]


def configure_logging(
    settings: object,
    secret_fields: Sequence[str] = (),
    secret_url_fields: Sequence[str] = (),
    log_level: LogLevel = LogLevel.INFO,
) -> None:
    """Configure the shared structlog processor chain.

    Args:
        secret_fields: Settings field names whose value is itself a secret.
        secret_url_fields: Settings field names holding a URL whose embedded password
            is the secret.
        log_level: The lowest level to emit; anything below it is dropped before its
            arguments are rendered, so a debug call costs nothing when off.

    A service must pass every secret-bearing field it has: these two lists are what the
    redaction processor matches live values against, and a field missing from them is
    only caught by the key-name pattern, which an unexpected key name would slip past.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
            _truncate_long_strings,
            make_redact_secrets_processor(settings, secret_fields, secret_url_fields),
            _build_console_renderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_threshold(log_level)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def _log_pipeline_failure(level: str, event: str, exc: Exception) -> None:
    """Best-effort stderr fallback so a dropped log entry isn't entirely invisible.

    Never raises or retries itself - if even this fails, that failure is swallowed
    too, since servicing the caller's request still takes priority.
    """
    with suppress(Exception):
        message = f"[Error: logging pipeline failed] {level} {event}: {exc!r}"
        print(message, file=sys.stderr)


class SafeLogger:
    """Wrap a structlog logger so a processor failure never reaches the caller.

    Servicing the caller's request takes priority over guaranteeing delivery of any
    single log entry - a failure here is never retried or surfaced to the caller, only
    best-effort noted via `_log_pipeline_failure`'s stderr fallback.
    """

    def __init__(self, logger: WrappedLogger) -> None:
        """Hold the logger every level method below logs through."""
        self._logger = logger

    def debug(self, event: str, **kwargs: Any) -> None:
        """Log `event` at debug level."""
        self._log("debug", event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        """Log `event` at info level."""
        self._log("info", event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        """Log `event` at warning level."""
        self._log("warning", event, **kwargs)

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


def get_logger(**initial_values: Any) -> SafeLogger:
    """Return a logger whose level calls never raise out to the caller."""
    return SafeLogger(structlog.get_logger(**initial_values))
