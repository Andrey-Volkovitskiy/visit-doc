import asyncio
import json
from collections.abc import AsyncIterator
from unittest.mock import MagicMock, patch

import pytest
import structlog
from chat.core.config import Settings
from chat.db.session import session_factory
from chat.main import app
from chat.rag.indexing import deindex_faq_entry, index_faq_entry
from chat.repositories import faq_repository
from chat.repositories.qdrant_repository import create_client, ensure_collection
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

from .conftest import fake_anthropic_client, fake_embed_texts

_ENTRY_CONTENT = "Visiting hours are 8am to 5pm."


@pytest.fixture
async def seeded_entry() -> AsyncIterator[int]:
    """Seed one `FaqEntry` directly (bypassing the not-yet-built `/faq` API, per US1's
    Independent Test), indexed into Qdrant with fake embeddings, cleaned up afterward.
    """
    settings = Settings()
    qdrant_client = create_client(settings)
    await ensure_collection(qdrant_client)

    async with session_factory() as session:
        entry = await faq_repository.create(session, _ENTRY_CONTENT)

    with patch("chat.rag.indexing.embed_texts", fake_embed_texts):
        # voyage_client is irrelevant here: embed_texts is faked and ignores it.
        await index_faq_entry(qdrant_client, MagicMock(), entry.id, _ENTRY_CONTENT)

    yield entry.id

    await deindex_faq_entry(qdrant_client, entry.id)
    async with session_factory() as session:
        await faq_repository.delete(session, entry.id)
    await qdrant_client.close()


def test_grounded_answer_streams_tokens_and_citations(seeded_entry: int) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(
            ["Visiting ", "hours are 8am to 5pm."]
        )
        with TestClient(app) as client:
            response = client.post("/chat", json={"message": "when can I visit?"})

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    token_lines = [line for line in lines if line["type"] == "token"]
    done_line = lines[-1]

    assert "".join(t["text"] for t in token_lines) == "Visiting hours are 8am to 5pm."
    assert done_line["type"] == "done"
    assert done_line["grounded"] is True
    assert any(c["entry_id"] == seeded_entry for c in done_line["citations"])


def test_abstention_on_unrelated_question(seeded_entry: int) -> None:
    question = "what is the weather today?"
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        TestClient(app) as client,
    ):
        response = client.post("/chat", json={"message": question})

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]

    assert len(lines) == 1
    assert lines[0]["type"] == "done"
    assert lines[0]["grounded"] is False
    assert lines[0]["citations"] == []


@pytest.mark.parametrize("message", ["", "a" * 2001])
def test_message_validation_rejects_empty_and_oversized(message: str) -> None:
    with TestClient(app) as client:
        response = client.post("/chat", json={"message": message})

    assert response.status_code == 422


def test_grounded_turn_logs_full_trace_under_one_turn_id(seeded_entry: int) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(
            ["Visiting ", "hours are 8am to 5pm."]
        )
        with (
            capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
            TestClient(app) as client,
        ):
            client.post("/chat", json={"message": "when can I visit?"})

    events = {entry["event"]: entry for entry in logs}
    turn_ids = {entry["turn_id"] for entry in logs}

    assert turn_ids == {logs[0]["turn_id"]}  # every entry shares one turn_id
    assert events["turn.message_received"]["message"] == "when can I visit?"
    assert "turn.message_embedded" in events
    chunks = events["turn.retrieval_completed"]["retrieved_chunks"]
    assert any(c["entry_id"] == seeded_entry for c in chunks)
    scores = [c["score"] for c in chunks]
    assert scores == sorted(scores, reverse=True)
    assert events["turn.groundedness_verdict"]["grounded"] is True
    done = events["turn.completed"]
    assert done["outcome"] == "grounded"
    assert done["answer_text"] == "Visiting hours are 8am to 5pm."
    assert any(c["entry_id"] == seeded_entry for c in done["citations"])
    assert all("score" in c for c in done["citations"])


def test_abstained_turn_logs_full_trace_under_one_turn_id(seeded_entry: int) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
        TestClient(app) as client,
    ):
        client.post("/chat", json={"message": "what is the weather today?"})

    events = {entry["event"]: entry for entry in logs}
    turn_ids = {entry["turn_id"] for entry in logs}

    assert turn_ids == {logs[0]["turn_id"]}
    assert "turn.message_received" in events
    assert "turn.message_embedded" in events
    assert "turn.retrieval_completed" in events
    assert events["turn.groundedness_verdict"]["grounded"] is False
    done = events["turn.completed"]
    assert done["outcome"] == "abstained"
    assert "abstention_message" in done


async def _post_two_chat_requests(asgi_app: FastAPI) -> None:
    transport = ASGITransport(app=asgi_app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        await asyncio.gather(
            ac.post("/chat", json={"message": "when can I visit?"}),
            ac.post("/chat", json={"message": "when can I visit?"}),
        )


def test_generation_failure_logs_turn_error_with_step(seeded_entry: int) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(
            stream_error=RuntimeError("boom")
        )
        with (
            capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            client.post("/chat", json={"message": "when can I visit?"})

    events = {entry["event"]: entry for entry in logs}
    turn_ids = {entry["turn_id"] for entry in logs}

    assert turn_ids == {logs[0]["turn_id"]}
    assert events["turn.error"]["pipeline_step"] == "generation"
    assert "boom" in events["turn.error"]["error_detail"]


def test_concurrent_turns_keep_distinct_turn_ids(seeded_entry: int) -> None:
    # capture_logs isn't task-safe (docs/testing-strategy.md), so a plain collector
    # processor is spliced into the real chain instead, ahead of the renderer.
    collected: list[dict[str, object]] = []

    def _collector(
        _logger: object, _method_name: str, event_dict: dict[str, object]
    ) -> dict[str, object]:
        collected.append(dict(event_dict))
        return event_dict

    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(["ok"])
        with TestClient(app):
            processors = structlog.get_config()["processors"]
            processors.insert(-1, _collector)
            try:
                asyncio.run(_post_two_chat_requests(app))
            finally:
                processors.remove(_collector)

    by_turn: dict[object, list[dict[str, object]]] = {}
    for entry in collected:
        by_turn.setdefault(entry.get("turn_id"), []).append(entry)

    assert len(by_turn) == 2
    for turn_id, entries in by_turn.items():
        assert turn_id is not None
        event_names = {entry["event"] for entry in entries}
        assert "turn.message_received" in event_names
        assert "turn.completed" in event_names
        assert all(entry["turn_id"] == turn_id for entry in entries)


def test_anthropic_and_voyage_clients_are_reused_across_chat_requests(
    seeded_entry: int,
) -> None:
    """finding #6: `AsyncAnthropic`/Voyage `AsyncClient` must be constructed once at
    app startup (main.py's lifespan) and reused, not rebuilt on every `/chat` request.
    Two requests in the same app lifespan must still see exactly one constructor call
    each.
    """
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch("chat.main.AsyncClient") as mock_voyage_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(["ok"])
        message = {"message": "when can I visit?"}
        with TestClient(app) as client:
            first_response = client.post("/chat", json=message)
            second_response = client.post("/chat", json=message)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    mock_anthropic_cls.assert_called_once()
    mock_voyage_cls.assert_called_once()
