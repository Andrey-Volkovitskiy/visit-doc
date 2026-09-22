import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog
from chat import observability
from chat.core.config import Settings, get_settings
from chat.core.logging import _SECRET_SETTINGS_FIELDS, _SECRET_URL_SETTINGS_FIELDS
from chat.domain.schemas import MAX_SEGMENTS
from chat.main import app
from chat.observability.client import Tracer
from chat.rag.embeddings import EMBEDDING_MODEL
from chat.repositories.qdrant_repository import COLLECTION_NAME
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from qdrant_client import QdrantClient
from structlog.testing import capture_logs

from .conftest import finished_spans


def test_app_is_a_fastapi_instance() -> None:
    assert isinstance(app, FastAPI)


def test_lifespan_ensures_qdrant_collection_exists() -> None:
    with TestClient(app):
        pass  # entering/exiting the context runs the lifespan startup/shutdown hooks

    client = QdrantClient(url=Settings().QDRANT_URL)
    assert client.collection_exists(COLLECTION_NAME)
    client.close()


def test_lifespan_shares_anthropic_and_voyage_clients_on_state() -> None:
    """finding #6: the Anthropic and Voyage clients must be constructed once at
    startup and shared via `app.state`, mirroring the existing `qdrant_client`
    precedent, instead of being rebuilt inline on every request.
    """
    with TestClient(app):
        assert app.state.anthropic_client is not None
        assert app.state.voyage_client is not None
        assert app.state.http_session is not None


def test_lifespan_failure_logs_critical_event_with_no_correlation_id() -> None:
    failure = RuntimeError("connection refused")
    with (
        patch("chat.main.ensure_collection", side_effect=failure),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        try:
            with TestClient(app):
                pass
        except RuntimeError:
            pass

    events = {entry["event"]: entry for entry in logs}
    critical = events["critical.dependency_unreachable"]

    assert critical["dependency"] == "qdrant"
    assert "connection refused" in critical["error_detail"]
    assert "turn_id" not in critical
    assert "operation_id" not in critical


def test_lifespan_failure_still_closes_the_qdrant_client() -> None:
    """Regression test for the `AsyncExitStack` cleanup ordering in `lifespan`:
    `stack.push_async_callback(client.close)` is registered right after the Qdrant
    client is constructed, before `ensure_collection` is even attempted, so a failed
    `ensure_collection` call must still close it rather than leaking the connection.
    """
    fake_client = AsyncMock()
    with (
        patch("chat.main.create_client", return_value=fake_client),
        patch(
            "chat.main.ensure_collection",
            side_effect=RuntimeError("connection refused"),
        ),
    ):
        try:
            with TestClient(app):
                pass
        except RuntimeError:
            pass

    fake_client.close.assert_called_once()


def _configured_events() -> list[dict[str, Any]]:
    """Run the app's lifespan, returning every `service.configured` entry it logged."""
    with capture_logs() as logs, TestClient(app):
        pass
    return [entry for entry in logs if entry["event"] == "service.configured"]


def _expected_conditions(settings: Settings) -> dict[str, object]:
    """The conditions a run is recorded under, as the service runs with them."""
    return {
        "classification_model": settings.CLASSIFICATION_MODEL,
        "generation_model": settings.GENERATION_MODEL,
        "embedding_model": EMBEDDING_MODEL,
        "rerank_model": settings.RERANK_MODEL,
        "retrieval_pool_size": settings.RETRIEVAL_POOL_SIZE,
        "similarity_floor": settings.SIMILARITY_FLOOR,
        "similarity_cap": settings.SIMILARITY_CAP,
        "rerank_floor": settings.RERANK_FLOOR,
        "rerank_cap": settings.RERANK_CAP,
        "max_segments": MAX_SEGMENTS,
        "context_turns": settings.CONTEXT_TURNS,
    }


def test_lifespan_states_the_conditions_it_runs_under_exactly_once() -> None:
    # The golden harness records a run under these values, so they have to come from
    # the process that answered - a harness reading its own .env describes itself.
    events = _configured_events()

    assert len(events) == 1
    event = events[0]
    assert event["log_level"] == "info"
    fields = {key: value for key, value in event.items() if key != "log_level"}
    assert fields == {
        "event": "service.configured",
        **_expected_conditions(get_settings()),
        "tracing_enabled": get_settings().tracing_enabled,
    }


def test_the_stated_conditions_follow_an_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RERANK_FLOOR", "0.61")
    monkeypatch.setenv("CLASSIFICATION_MODEL", "claude-overridden")

    with patch("chat.main.get_settings", Settings):
        (event,) = _configured_events()

    assert event["rerank_floor"] == 0.61
    assert event["classification_model"] == "claude-overridden"


def test_the_conditions_are_stated_before_any_client_is_built() -> None:
    # A service that cannot reach Qdrant still said what it was configured with, so the
    # log of a failed start is not also silent about the settings it failed under.
    with (
        patch("chat.main.create_client", side_effect=RuntimeError("boom")),
        capture_logs() as logs,
        pytest.raises(RuntimeError),
        TestClient(app),
    ):
        pass

    assert [entry["event"] for entry in logs] == ["service.configured"]


def test_the_stated_conditions_carry_no_secret_bearing_setting() -> None:
    (event,) = _configured_events()
    secret_fields = {
        field.lower()
        for field in (*_SECRET_SETTINGS_FIELDS, *_SECRET_URL_SETTINGS_FIELDS)
    }

    assert not secret_fields & {key.lower() for key in event}


def test_the_stated_conditions_say_whether_the_service_traces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stated so a run can record whether its turns could have been traced; the keys
    # themselves never appear, only whether both are set.
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-stated")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-stated")

    with (
        patch("chat.main.get_settings", Settings),
        patch("chat.main.build_tracer", return_value=MagicMock(spec=Tracer)),
    ):
        (event,) = _configured_events()

    assert event["tracing_enabled"] is True
    assert "sk-lf-stated" not in str(event)
    assert "pk-lf-stated" not in str(event)


def test_the_test_environment_states_tracing_off() -> None:
    (event,) = _configured_events()

    assert event["tracing_enabled"] is False


def test_the_lifespan_installs_the_tracer_it_builds_and_shuts_it_down() -> None:
    tracer = MagicMock(spec=Tracer)

    with patch("chat.main.build_tracer", return_value=tracer) as build:
        with TestClient(app):
            installed = observability.installed()
        after = observability.installed()

    build.assert_called_once_with(get_settings())
    assert installed is tracer
    assert after is None
    tracer.shutdown.assert_called_once_with()


def test_a_failed_start_still_uninstalls_and_shuts_the_tracer_down() -> None:
    tracer = MagicMock(spec=Tracer)

    with (
        patch("chat.main.build_tracer", return_value=tracer),
        patch("chat.main.create_client", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError),
        TestClient(app),
    ):
        pass

    assert observability.installed() is None
    tracer.shutdown.assert_called_once_with()


def test_the_lifespan_bridges_the_sdks_warnings_into_the_log() -> None:
    with TestClient(app), capture_logs() as logs:
        logging.getLogger("opentelemetry").warning("Failed to export batch")

    (entry,) = [log for log in logs if log["event"] == "tracing.export_failed"]
    assert entry["message"] == "Failed to export batch"


def test_an_app_started_under_the_span_exporter_fixture_traces_into_it(
    span_exporter: InMemorySpanExporter,
) -> None:
    # The test environment's settings build a disabled tracer, which the lifespan would
    # otherwise install over the fixture's - and every app-driven tracing test would
    # then assert against an exporter nothing reaches.
    with TestClient(app), observability.step("probe"):
        pass

    assert list(finished_spans(span_exporter)) == ["probe"]
