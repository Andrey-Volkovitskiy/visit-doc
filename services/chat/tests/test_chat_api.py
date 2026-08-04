import json
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from chat.core.config import Settings
from chat.db.session import session_factory
from chat.main import app
from chat.rag.indexing import deindex_faq_entry, index_faq_entry
from chat.repositories import faq_repository
from chat.repositories.qdrant_repository import create_client, ensure_collection
from fastapi.testclient import TestClient

from .conftest import FakeAnthropicStream, fake_embed_texts

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
        await index_faq_entry(qdrant_client, settings, entry.id, _ENTRY_CONTENT)

    yield entry.id

    await deindex_faq_entry(qdrant_client, entry.id)
    async with session_factory() as session:
        await faq_repository.delete(session, entry.id)
    await qdrant_client.close()


def test_grounded_answer_streams_tokens_and_citations(seeded_entry: int) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.agent.answer_faq.AsyncAnthropic") as mock_anthropic_cls,
    ):
        fake_stream = FakeAnthropicStream(["Visiting ", "hours are 8am to 5pm."])
        mock_anthropic_cls.return_value.messages.stream.return_value = fake_stream
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
