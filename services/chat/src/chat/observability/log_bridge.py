"""Forward the tracing SDK's own warnings into this service's log.

An export fails on the SDK's background thread, far from any turn, and all it leaves is
a record on a stdlib logger - which nothing in this service reads. Bridged into
structlog, it reaches `.run/chat.log` as `tracing.export_failed`, through the same
processor chain as every other entry, redaction included: the exporter's error text is
exactly where its credentials could surface.
"""

import logging

from chat.core.logging import get_logger

# The SDK logs under `langfuse`; the OTLP exporter and the batch processor under
# `opentelemetry.*`. Handlers attached to the parents see their children's records.
_BRIDGED_LOGGERS = ("langfuse", "opentelemetry")


class _ExportFailureHandler(logging.Handler):
    """Re-emit each record it receives as a `tracing.export_failed` log event."""

    def emit(self, record: logging.LogRecord) -> None:
        """Log `record` through structlog, never raising into the SDK's thread.

        A failure - a record whose arguments do not fit its format, or a broken log
        pipeline - goes to `handleError`, the stdlib's own path for a handler that could
        not emit, rather than into the exporter thread or a turn that logged it.
        """
        try:
            get_logger().warning(
                "tracing.export_failed",
                logger=record.name,
                message=record.getMessage(),
            )
        except Exception:  # noqa: BLE001 - reported by handleError, never raised
            self.handleError(record)


_HANDLER = _ExportFailureHandler(level=logging.WARNING)


def install_log_bridge() -> None:
    """Attach the bridge to the SDK's loggers. Installing it again changes nothing.

    Also re-enables those loggers and their existing children. A logging config applied
    earlier in the process disables every logger that existed when it ran - alembic's
    `fileConfig` does, by default - and a disabled logger drops its records before any
    handler sees them, which would silence exactly the failures this bridge surfaces.
    """
    for name in _BRIDGED_LOGGERS:
        logger = logging.getLogger(name)
        if _HANDLER not in logger.handlers:
            logger.addHandler(_HANDLER)
        for existing in _self_and_children(name):
            existing.disabled = False


def uninstall_log_bridge() -> None:
    """Detach the bridge from the SDK's loggers, if it is attached."""
    for name in _BRIDGED_LOGGERS:
        logging.getLogger(name).removeHandler(_HANDLER)


def _self_and_children(name: str) -> list[logging.Logger]:
    """Return the logger `name` and every logger created under it so far."""
    prefix = f"{name}."
    return [logging.getLogger(name)] + [
        logger
        for key, logger in logging.root.manager.loggerDict.items()
        if key.startswith(prefix) and isinstance(logger, logging.Logger)
    ]
