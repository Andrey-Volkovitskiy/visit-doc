import json
from typing import Any
from unittest.mock import patch

import pytest
import structlog
from chat.main import app
from chat.repositories.qdrant_repository import upsert_chunks as real_upsert_chunks
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from .conftest import (
    LOCAL_NOW,
    chat_id_for,
    fake_anthropic_client,
    fake_embed_texts,
)


def _create(client: TestClient, content: str) -> dict[str, Any]:
    with patch("chat.rag.indexing.embed_texts", fake_embed_texts):
        response = client.post("/faq", json={"content": content})
    assert response.status_code == 201
    body: dict[str, Any] = response.json()
    return body


def test_create_list_and_get_round_trip() -> None:
    with TestClient(app) as client:
        entry = _create(client, "Visiting hours are 8am to 5pm.")
        assert entry["content"] == "Visiting hours are 8am to 5pm."
        assert "title" not in entry

        list_response = client.get("/faq")
        assert list_response.status_code == 200
        assert any(e["id"] == entry["id"] for e in list_response.json())

        get_response = client.get(f"/faq/{entry['id']}")
        assert get_response.status_code == 200
        assert get_response.json()["id"] == entry["id"]

        client.delete(f"/faq/{entry['id']}")


def test_get_404_for_unknown_id() -> None:
    with TestClient(app) as client:
        response = client.get("/faq/999999999")
    assert response.status_code == 404


def test_voyage_client_is_reused_across_create_and_update() -> None:
    """finding #6: the Voyage `AsyncClient` must be constructed once at app startup
    (main.py's lifespan) and reused, not rebuilt inside `embed_texts` on every call -
    two indexing operations in the same app lifespan must still see one constructor
    call.
    """
    with (
        patch("chat.rag.indexing.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncClient") as mock_voyage_cls,
        TestClient(app) as client,
    ):
        entry = _create(client, "Visiting hours are 8am to 5pm.")
        update_response = client.put(
            f"/faq/{entry['id']}", json={"content": "New hours."}
        )
        delete_response = client.delete(f"/faq/{entry['id']}")

    assert update_response.status_code == 200
    assert delete_response.status_code == 204
    mock_voyage_cls.assert_called_once()


def test_update_is_reflected_in_chat_retrieval() -> None:
    # The fake Anthropic client must be installed before `TestClient(app)` runs
    # lifespan startup, so lifespan's `AsyncAnthropic(...)` call returns the fake
    # directly - patching `app.state.anthropic_client` afterward instead would leak
    # the real client's connection pool, since lifespan shutdown only closes whatever
    # object `app.state.anthropic_client` currently points to.
    with patch("chat.main.AsyncAnthropic") as mock_anthropic_cls:
        mock_anthropic_cls.return_value = fake_anthropic_client(["An answer."])
        with TestClient(app) as client:
            entry = _create(client, "Visiting hours are 8am to 5pm.")

            with patch("chat.rag.indexing.embed_texts", fake_embed_texts):
                update_response = client.put(
                    f"/faq/{entry['id']}",
                    json={"content": "Visiting hours are now 24/7."},
                )
            assert update_response.status_code == 200
            assert update_response.json()["content"] == "Visiting hours are now 24/7."

            with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
                # Deliberately a different string than the citation assertion below
                # checks: the streamed answer text comes entirely from this fake, so
                # asserting it equals a value we hardcoded here would prove nothing
                # about retrieval.
                chat_response = client.post(
                    "/chat",
                    json={
                        "chat_id": chat_id_for(client),
                        "message": "when can I visit?",
                        "local_now": LOCAL_NOW,
                    },
                )

            lines = [
                json.loads(line) for line in chat_response.text.strip().splitlines()
            ]
            done_line = lines[-1]
            citations = done_line["citations"]
            assert any(
                c["chunk_text"] == "Visiting hours are now 24/7." for c in citations
            )

            client.delete(f"/faq/{entry['id']}")


def test_delete_stops_grounding_and_then_404s() -> None:
    with TestClient(app) as client:
        entry = _create(client, "Visiting hours are 8am to 5pm.")

        delete_response = client.delete(f"/faq/{entry['id']}")
        assert delete_response.status_code == 204

        assert client.get(f"/faq/{entry['id']}").status_code == 404
        assert client.delete(f"/faq/{entry['id']}").status_code == 404

        with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
            chat_response = client.post(
                "/chat",
                json={
                    "chat_id": chat_id_for(client),
                    "message": "when can I visit?",
                    "local_now": LOCAL_NOW,
                },
            )
        lines = [json.loads(line) for line in chat_response.text.strip().splitlines()]
        assert lines[-1]["grounded"] is False


@pytest.mark.parametrize(
    "content",
    ["", "a" * 20001, "---\n   \n---", "Question:\nAnswer:"],
)
def test_validation_rejects_invalid_content(content: str) -> None:
    with TestClient(app) as client:
        response = client.post("/faq", json={"content": content})
    assert response.status_code == 422


def test_create_logs_content_chunked_then_embedded_then_entry_created() -> None:
    with (
        patch("chat.rag.indexing.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
        TestClient(app) as client,
    ):
        entry = _create(client, "Visiting hours are 8am to 5pm.")
        client.delete(f"/faq/{entry['id']}")

    create_logs = [entry for entry in logs if entry.get("event") != "faq.entry_deleted"]
    operation_ids = {entry["operation_id"] for entry in create_logs}
    event_order = [entry["event"] for entry in create_logs]

    assert len(operation_ids) == 1  # every event from this one create shares one id
    assert event_order == [
        "faq.content_chunked",
        "faq.chunks_embedded",
        "faq.entry_created",
    ]
    chunked, embedded, created = create_logs
    assert chunked["chunk_count"] == embedded["chunk_count"]
    assert created["entry_id"] == entry["id"]


def test_update_and_delete_each_get_their_own_operation_id() -> None:
    with (
        patch("chat.rag.indexing.embed_texts", fake_embed_texts),
        TestClient(app) as client,
    ):
        entry = _create(client, "Visiting hours are 8am to 5pm.")

        with capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as update_logs:
            client.put(f"/faq/{entry['id']}", json={"content": "New hours."})

        with capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as delete_logs:
            client.delete(f"/faq/{entry['id']}")

    update_operation_ids = {entry["operation_id"] for entry in update_logs}
    delete_operation_ids = {entry["operation_id"] for entry in delete_logs}

    assert len(update_operation_ids) == 1
    assert len(delete_operation_ids) == 1
    assert update_operation_ids != delete_operation_ids  # distinct operations
    assert [e["event"] for e in update_logs] == [
        "faq.content_chunked",
        "faq.chunks_embedded",
        "faq.entry_updated",
    ]
    assert [e["event"] for e in delete_logs] == ["faq.entry_deleted"]


def test_create_failure_logs_operation_failed_and_critical_event() -> None:
    with (
        patch("chat.rag.indexing.embed_texts", fake_embed_texts),
        patch(
            "chat.rag.indexing.upsert_chunks", side_effect=RuntimeError("qdrant down")
        ),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        content = "Visiting hours are 8am to 5pm."
        response = client.post("/faq", json={"content": content})

    assert response.status_code == 500
    events = {entry["event"]: entry for entry in logs}
    operation_ids = {entry["operation_id"] for entry in logs}

    assert len(operation_ids) == 1  # correlated, not two unrelated entries (FR-018)
    failed = events["faq.operation_failed"]
    assert failed["operation"] == "create"
    assert failed["failed_step"] == "persist"
    assert "qdrant down" in failed["error_detail"]
    critical = events["critical.dependency_unreachable"]
    assert critical["dependency"] == "qdrant"
    assert "qdrant down" in critical["error_detail"]


def test_create_failure_rolls_back_the_postgres_row() -> None:
    with TestClient(app, raise_server_exceptions=False) as client:
        with (
            patch("chat.rag.indexing.embed_texts", fake_embed_texts),
            patch(
                "chat.rag.indexing.upsert_chunks",
                side_effect=RuntimeError("qdrant down"),
            ),
            capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
        ):
            content = "Visiting hours are 8am to 5pm."
            response = client.post("/faq", json={"content": content})

        assert response.status_code == 500
        failed = next(e for e in logs if e["event"] == "faq.operation_failed")
        entry_id = failed["entry_id"]

        get_response = client.get(f"/faq/{entry_id}")
    assert get_response.status_code == 404


def test_update_failure_reverts_content_and_reindexes_previous() -> None:
    # See test_update_is_reflected_in_chat_retrieval for why the fake Anthropic client
    # must be installed before `TestClient(app)` runs lifespan startup.
    with patch("chat.main.AsyncAnthropic") as mock_anthropic_cls:
        mock_anthropic_cls.return_value = fake_anthropic_client(["An answer."])
        with TestClient(app, raise_server_exceptions=False) as client:
            entry = _create(client, "Visiting hours are 8am to 5pm.")

            calls = {"n": 0}

            async def _upsert_fail_once(*args: Any, **kwargs: Any) -> None:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("qdrant down")
                await real_upsert_chunks(*args, **kwargs)

            with (
                patch("chat.rag.indexing.embed_texts", fake_embed_texts),
                patch("chat.rag.indexing.upsert_chunks", side_effect=_upsert_fail_once),
            ):
                update_response = client.put(
                    f"/faq/{entry['id']}",
                    json={"content": "Visiting hours are now 24/7."},
                )
            assert update_response.status_code == 500

            get_response = client.get(f"/faq/{entry['id']}")
            assert get_response.status_code == 200
            assert get_response.json()["content"] == "Visiting hours are 8am to 5pm."

            with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
                chat_response = client.post(
                    "/chat",
                    json={
                        "chat_id": chat_id_for(client),
                        "message": "when can I visit?",
                        "local_now": LOCAL_NOW,
                    },
                )

            lines = [
                json.loads(line) for line in chat_response.text.strip().splitlines()
            ]
            citations = lines[-1]["citations"]
            assert any(
                c["chunk_text"] == "Visiting hours are 8am to 5pm." for c in citations
            )

            client.delete(f"/faq/{entry['id']}")


def test_list_faq_entries_failure_logs_critical_event_uncorrelated() -> None:
    with (
        patch(
            "chat.repositories.faq_repository.list_all",
            side_effect=RuntimeError("connection refused"),
        ),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        response = client.get("/faq")

    assert response.status_code == 500
    critical = next(e for e in logs if e["event"] == "critical.dependency_unreachable")
    assert critical["dependency"] == "postgres"
    assert "connection refused" in critical["error_detail"]
    assert "operation_id" not in critical
    assert "turn_id" not in critical
