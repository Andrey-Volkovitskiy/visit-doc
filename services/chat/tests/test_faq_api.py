import json
from typing import Any
from unittest.mock import patch

import pytest
from chat.main import app
from fastapi.testclient import TestClient

from .conftest import FakeAnthropicStream, fake_embed_texts


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


def test_update_is_reflected_in_chat_retrieval() -> None:
    with TestClient(app) as client:
        entry = _create(client, "Visiting hours are 8am to 5pm.")

        with patch("chat.rag.indexing.embed_texts", fake_embed_texts):
            update_response = client.put(
                f"/faq/{entry['id']}", json={"content": "Visiting hours are now 24/7."}
            )
        assert update_response.status_code == 200
        assert update_response.json()["content"] == "Visiting hours are now 24/7."

        with (
            patch("chat.rag.retriever.embed_texts", fake_embed_texts),
            patch("chat.agent.answer_faq.AsyncAnthropic") as mock_anthropic_cls,
        ):
            # Deliberately a different string than the citation assertion below checks:
            # the streamed answer text comes entirely from this mock, so asserting it
            # equals a value we hardcoded here would prove nothing about retrieval.
            fake_stream = FakeAnthropicStream(["An answer."])
            mock_anthropic_cls.return_value.messages.stream.return_value = fake_stream
            chat_response = client.post("/chat", json={"message": "when can I visit?"})

        lines = [json.loads(line) for line in chat_response.text.strip().splitlines()]
        done_line = lines[-1]
        citations = done_line["citations"]
        assert any(c["chunk_text"] == "Visiting hours are now 24/7." for c in citations)

        client.delete(f"/faq/{entry['id']}")


def test_delete_stops_grounding_and_then_404s() -> None:
    with TestClient(app) as client:
        entry = _create(client, "Visiting hours are 8am to 5pm.")

        delete_response = client.delete(f"/faq/{entry['id']}")
        assert delete_response.status_code == 204

        assert client.get(f"/faq/{entry['id']}").status_code == 404
        assert client.delete(f"/faq/{entry['id']}").status_code == 404

        with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
            chat_response = client.post("/chat", json={"message": "when can I visit?"})
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
