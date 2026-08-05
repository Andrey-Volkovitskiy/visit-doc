from unittest.mock import patch

import structlog
from chat.core.config import Settings
from chat.main import app
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
