from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import structlog
from chat.core.config import Settings, get_settings
from chat.core.logging import _SECRET_SETTINGS_FIELDS, _SECRET_URL_SETTINGS_FIELDS
from chat.domain.schemas import MAX_SEGMENTS
from chat.main import app
from chat.rag.embeddings import EMBEDDING_MODEL
from chat.repositories.qdrant_repository import COLLECTION_NAME
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient
from structlog.testing import capture_logs


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
