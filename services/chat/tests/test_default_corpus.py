"""The starter corpus a new session is created with.

Every test here carries `seeds_default_corpus`: the rest of the suite runs with the
seeding step faked out (`conftest._new_sessions_start_empty`), so these are the only
tests in which `POST /chats` really plants anything.
"""

import json
from unittest.mock import patch

import pytest
import structlog
from chat.core.config import Settings
from chat.main import app
from chat.rag.default_corpus import DEFAULT_FAQ_ENTRIES
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from .conftest import (
    LOCAL_NOW,
    chat_id_for,
    fake_anthropic_client,
    fake_embed_texts,
)

pytestmark = pytest.mark.seeds_default_corpus


def test_a_new_session_is_created_holding_the_default_corpus() -> None:
    with (
        patch("chat.rag.indexing.embed_texts", fake_embed_texts),
        TestClient(app) as client,
    ):
        created = client.post("/chats")
        listed = client.get("/faq")

    assert created.status_code == 201
    # Compared against the production constant rather than a re-typed copy, so an entry
    # added to the corpus is covered here without anyone remembering to add it.
    assert [entry["content"] for entry in listed.json()] == list(DEFAULT_FAQ_ENTRIES)


def test_the_seeded_corpus_grounds_the_sessions_first_question() -> None:
    # The point of planting it: a first-time visitor gets an answer from the corpus
    # instead of the abstention an empty one produces. The citations come from a real
    # Qdrant search over what the seeding actually wrote, so they would disappear if
    # the chunks or the revisions naming them were wrong.
    with (
        patch("chat.rag.indexing.embed_texts", fake_embed_texts),
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(["An answer."])
        with TestClient(app) as client:
            client.post("/chats")
            response = client.post(
                "/chat",
                json={
                    "chat_id": chat_id_for(client),
                    "message": "what are your clinic hours?",
                    "local_now": LOCAL_NOW,
                },
            )

    done = _done_event(response.text)
    assert done["citations"]
    for citation in done["citations"]:
        assert any(citation["chunk_text"] in entry for entry in DEFAULT_FAQ_ENTRIES), (
            citation
        )


def test_a_returning_visitor_is_not_given_a_second_copy() -> None:
    with (
        patch("chat.rag.indexing.embed_texts", fake_embed_texts),
        TestClient(app) as client,
    ):
        client.post("/chats")
        client.post("/chats")
        listed = client.get("/faq")

    assert [entry["content"] for entry in listed.json()] == list(DEFAULT_FAQ_ENTRIES)


def test_a_seeded_entry_is_edited_and_deleted_like_any_other() -> None:
    with (
        patch("chat.rag.indexing.embed_texts", fake_embed_texts),
        TestClient(app) as client,
    ):
        client.post("/chats")
        seeded = client.get("/faq").json()
        edited = client.put(
            f"/faq/{seeded[0]['id']}", json={"content": "A referral is required."}
        )
        deleted = client.delete(f"/faq/{seeded[1]['id']}")
        listed = client.get("/faq").json()

    assert edited.status_code == 200
    assert deleted.status_code == 204
    assert [entry["content"] for entry in listed] == [
        "A referral is required.",
        *DEFAULT_FAQ_ENTRIES[2:],
    ]


def test_an_unreachable_embedder_leaves_the_session_empty_and_says_so() -> None:
    # Chat creation does not fail on it: the session and the chat are real, and an
    # empty corpus is a state the rest of the system already handles. What it must not
    # do is pass silently, since nothing about the empty corpus itself says why.
    with TestClient(app) as client:
        with (
            patch(
                "chat.rag.indexing.embed_texts",
                side_effect=RuntimeError("voyage is down"),
            ),
            capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
        ):
            created = client.post("/chats")
        listed = client.get("/faq")

    assert created.status_code == 201
    assert listed.json() == []

    events = {entry["event"]: entry for entry in logs}
    failed = events["faq.default_corpus_seed_failed"]
    assert failed["failed_step"] == "embedding"
    assert "voyage is down" in failed["error_detail"]
    assert events["critical.dependency_unreachable"]["dependency"] == "voyage"
    # The seeding's own entries are one operation, correlated with each other and with
    # nothing else the request logged.
    seed_events = [e for e in logs if "operation_id" in e]
    assert [e["event"] for e in seed_events] == [
        "faq.content_chunked",
        "faq.default_corpus_seed_failed",
        "critical.dependency_unreachable",
    ]
    assert len({e["operation_id"] for e in seed_events}) == 1


def test_nothing_is_published_when_one_entrys_chunks_cannot_be_written() -> None:
    # The corpus is planted by one commit, so a store that failed part way through
    # leaks chunks and publishes nothing - never a session holding some of it.
    with TestClient(app) as client:
        with (
            patch("chat.rag.indexing.embed_texts", fake_embed_texts),
            patch(
                "chat.rag.indexing.upsert_chunks",
                side_effect=[None, RuntimeError("qdrant is down")],
            ),
            capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
        ):
            created = client.post("/chats")
        listed = client.get("/faq")

    assert created.status_code == 201
    assert listed.json() == []

    events = {entry["event"]: entry for entry in logs}
    assert events["faq.default_corpus_seed_failed"]["failed_step"] == "persist"
    assert events["critical.dependency_unreachable"]["dependency"] == "qdrant"


def test_a_failure_no_step_tagged_names_no_dependency_and_still_creates_the_chat() -> (
    None
):
    # Nothing escapes the seeding, whether or not it arrived tagged with the step it
    # failed at. An untagged one is a defect rather than a system being unreachable, so
    # it is recorded as a failure and raises no critical event: that event names a
    # dependency, and the only name available here would be a guess.
    with TestClient(app) as client:
        with (
            patch(
                "chat.api.provisioning.publish_revisions",
                side_effect=RuntimeError("something else broke"),
            ),
            capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
        ):
            created = client.post("/chats")
        listed = client.get("/faq")

    assert created.status_code == 201
    assert listed.json() == []

    events = {entry["event"]: entry for entry in logs}
    failed = events["faq.default_corpus_seed_failed"]
    assert failed["failed_step"] == "unexpected"
    assert "something else broke" in failed["error_detail"]
    assert "critical.dependency_unreachable" not in events


def test_the_starter_corpus_never_exceeds_the_sessions_own_cap() -> None:
    capped = Settings(FAQ_MAX_ENTRIES_PER_SESSION=2)
    with (
        patch("chat.api.provisioning.get_settings", return_value=capped),
        patch("chat.rag.indexing.embed_texts", fake_embed_texts),
        TestClient(app) as client,
    ):
        client.post("/chats")
        listed = client.get("/faq")

    assert [entry["content"] for entry in listed.json()] == list(
        DEFAULT_FAQ_ENTRIES[:2]
    )


def _done_event(body: str) -> dict[str, object]:
    """Return the terminal event of a streamed turn."""
    return dict(json.loads(body.strip().splitlines()[-1]))
