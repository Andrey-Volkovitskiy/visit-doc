"""The bridge from the SDK's stdlib loggers into this service's log.

A trace that fails to export fails on the exporter's own thread, and the only trace of
it is a stdlib log record. Forwarded into structlog, it reaches `.run/chat.log` beside
the turn it belonged to - through the same redaction every other entry passes, since
the exporter's own error text is where its credentials could surface.
"""

import logging
from collections.abc import Iterator
from unittest.mock import patch

import pytest
import structlog
from chat.core.config import Settings
from chat.core.logging import configure_logging
from chat.observability import log_bridge
from chat.observability.log_bridge import install_log_bridge, uninstall_log_bridge
from shared_logging import LogFormat
from structlog.testing import capture_logs


@pytest.fixture(autouse=True)
def _bridged() -> Iterator[None]:
    saved = structlog.get_config()
    install_log_bridge()
    try:
        yield
    finally:
        uninstall_log_bridge()
        structlog.configure(**saved)


@pytest.mark.parametrize(
    "logger_name",
    ["opentelemetry.sdk._shared_internal", "opentelemetry", "langfuse"],
)
def test_a_warning_from_the_sdk_is_logged_as_an_export_failure(
    logger_name: str,
) -> None:
    with capture_logs() as logs:
        logging.getLogger(logger_name).warning("Failed to export %s spans", 3)

    (entry,) = [log for log in logs if log["event"] == "tracing.export_failed"]
    assert entry["logger"] == logger_name
    assert entry["message"] == "Failed to export 3 spans"
    assert entry["log_level"] == "warning"


def test_an_error_from_the_sdk_is_logged_too() -> None:
    with capture_logs() as logs:
        logging.getLogger("langfuse").error("Masking error: dropping export batch")

    assert [log["event"] for log in logs] == ["tracing.export_failed"]


def test_an_info_record_from_the_sdk_is_not_bridged() -> None:
    with capture_logs() as logs:
        logging.getLogger("langfuse").info("Startup: tracer initialized")

    assert logs == []


def test_a_record_from_an_unrelated_logger_is_not_bridged() -> None:
    with capture_logs() as logs:
        logging.getLogger("httpx").warning("not ours")

    assert logs == []


def test_installing_twice_bridges_each_record_once() -> None:
    install_log_bridge()

    with capture_logs() as logs:
        logging.getLogger("langfuse").warning("once")

    assert len(logs) == 1


def test_a_secret_in_the_records_message_is_redacted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(
        Settings(
            LANGFUSE_SECRET_KEY="sk-lf-br1dg3d-s3cr3t",
            LOG_FORMAT=LogFormat.JSON,
        )
    )

    logging.getLogger("opentelemetry").warning("401 for sk-lf-br1dg3d-s3cr3t")

    output = capsys.readouterr().out
    assert "tracing.export_failed" in output
    assert "sk-lf-br1dg3d-s3cr3t" not in output


def test_a_logger_an_earlier_logging_config_disabled_is_bridged_again() -> None:
    # `logging.config.fileConfig` - which alembic's env runs - disables every logger
    # that exists when it is applied, and a disabled logger drops its records before
    # any handler sees them.
    child = logging.getLogger("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    child.disabled = True
    logging.getLogger("langfuse").disabled = True

    install_log_bridge()
    with capture_logs() as logs:
        child.warning("Failed to export batch code: 401")
        logging.getLogger("langfuse").warning("Masking error")

    assert [log["logger"] for log in logs] == [child.name, "langfuse"]


def test_a_bridge_that_fails_to_log_hands_the_record_to_handle_error() -> None:
    # Never raising is the point: a raise here would reach the SDK's exporter thread.
    with (
        patch.object(log_bridge, "get_logger", side_effect=RuntimeError("broken")),
        patch.object(log_bridge._HANDLER, "handleError") as handle_error,
    ):
        logging.getLogger("langfuse").warning("Failed to export batch")

    ((record,), _) = handle_error.call_args
    assert record.getMessage() == "Failed to export batch"
