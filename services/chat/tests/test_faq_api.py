import json
from typing import Any
from unittest.mock import patch

import pytest
import structlog
from chat.api.session_cookie import COOKIE_NAME
from chat.core.config import Settings
from chat.main import app
from chat.repositories import faq_repository
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from .conftest import (
    LOCAL_NOW,
    chat_id_for,
    fake_anthropic_client,
    fake_embed_texts,
)


def _ensure_session(client: TestClient) -> None:
    """Give `client` a session before it touches `/faq`.

    A corpus belongs to exactly one session, so a client with no session cookie owns
    nothing: its listing is empty and its writes are refused. `POST /chats` is the only
    thing that mints one.
    """
    if COOKIE_NAME not in client.cookies:
        client.post("/chats")


def _create(client: TestClient, content: str) -> dict[str, Any]:
    _ensure_session(client)
    with patch("chat.rag.indexing.embed_texts", fake_embed_texts):
        response = client.post("/faq", json={"content": content})
    assert response.status_code == 201
    body: dict[str, Any] = response.json()
    return body


def test_create_list_and_get_round_trip() -> None:
    with TestClient(app) as client:
        _ensure_session(client)
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
        _ensure_session(client)
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
            _ensure_session(client)
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
    # Patched before `TestClient(app)`, like its siblings above: the turn this test
    # runs would otherwise classify and generate against the live API, which is both
    # paid and non-deterministic - and it is the FAQ path's groundedness, not the
    # classifier's reading of the message, that is under test here.
    with patch("chat.main.AsyncAnthropic") as mock_anthropic_cls:
        mock_anthropic_cls.return_value = fake_anthropic_client(["An answer."])
        with TestClient(app) as client:
            _ensure_session(client)
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
            lines = [
                json.loads(line) for line in chat_response.text.strip().splitlines()
            ]
            assert lines[-1]["grounded"] is False


@pytest.mark.parametrize(
    "content",
    ["", "a" * 20001, "---\n   \n---", "Question:\nAnswer:"],
)
def test_validation_rejects_invalid_content(content: str) -> None:
    with TestClient(app) as client:
        _ensure_session(client)
        response = client.post("/faq", json={"content": content})
    assert response.status_code == 422


def test_create_logs_content_chunked_then_embedded_then_entry_created() -> None:
    with TestClient(app) as client:
        # Outside `capture_logs`: minting the session is setup, and its events are not
        # what this test is asserting about the FAQ operation's own.
        _ensure_session(client)
        with (
            patch("chat.rag.indexing.embed_texts", fake_embed_texts),
            capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
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
    with TestClient(app, raise_server_exceptions=False) as client:
        # The session has to exist before the failure patches, or `POST /chats` fails
        # too and the request under test is refused for the wrong reason.
        _ensure_session(client)
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

    assert response.status_code == 503
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


def test_a_failed_create_leaves_no_row_to_roll_back() -> None:
    # The row is the last thing written and the only thing that publishes anything, so
    # a create that failed before it has nothing to undo - there is no window in which
    # an entry exists without the chunks it answers from.
    with TestClient(app, raise_server_exceptions=False) as client:
        _ensure_session(client)
        with (
            patch("chat.rag.indexing.embed_texts", fake_embed_texts),
            patch(
                "chat.rag.indexing.upsert_chunks",
                side_effect=RuntimeError("qdrant down"),
            ),
        ):
            response = client.post(
                "/faq", json={"content": "Visiting hours are 8am to 5pm."}
            )
        listed = client.get("/faq").json()

    assert response.status_code == 503
    assert listed == []


def test_a_failed_update_leaves_the_entry_answering_and_repairs_nothing() -> None:
    # `_revert_faq_update` is deleted rather than repaired: a best-effort compensating
    # write that half-succeeds and swallows its own failure is what left the two stores
    # silently disagreeing. Under additive revisions there is nothing to compensate for
    # - the previous revision is still there, still live, still answered from.
    with patch("chat.main.AsyncAnthropic") as mock_anthropic_cls:
        mock_anthropic_cls.return_value = fake_anthropic_client(["An answer."])
        with TestClient(app, raise_server_exceptions=False) as client:
            _ensure_session(client)
            entry = _create(client, "Visiting hours are 8am to 5pm.")

            with (
                patch("chat.rag.indexing.embed_texts", fake_embed_texts),
                patch(
                    "chat.rag.indexing.upsert_chunks",
                    side_effect=RuntimeError("qdrant down"),
                ),
            ):
                update_response = client.put(
                    f"/faq/{entry['id']}",
                    json={"content": "Visiting hours are now 24/7."},
                )

            after = client.get(f"/faq/{entry['id']}").json()
            with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
                chat_response = client.post(
                    "/chat",
                    json={
                        "chat_id": chat_id_for(client),
                        "message": "when can I visit?",
                        "local_now": LOCAL_NOW,
                    },
                )

    assert update_response.status_code == 503
    assert after["content"] == "Visiting hours are 8am to 5pm."
    lines = [json.loads(line) for line in chat_response.text.strip().splitlines()]
    citations = lines[-1]["citations"]
    assert any(c["chunk_text"] == "Visiting hours are 8am to 5pm." for c in citations)


def test_list_faq_entries_failure_logs_critical_event_uncorrelated() -> None:
    with TestClient(app, raise_server_exceptions=False) as client:
        # Without a session the listing is an empty corpus rather than a read at all,
        # so the failure this test induces would never be reached.
        _ensure_session(client)
        with (
            patch(
                "chat.repositories.faq_repository.list_all",
                side_effect=RuntimeError("connection refused"),
            ),
            capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
        ):
            response = client.get("/faq")

    assert response.status_code == 500
    critical = next(e for e in logs if e["event"] == "critical.dependency_unreachable")
    assert critical["dependency"] == "postgres"
    assert "connection refused" in critical["error_detail"]
    assert "operation_id" not in critical
    assert "turn_id" not in critical


# --- 007: the corpus belongs to a session, and it has a ceiling -------------------


def test_a_new_sessions_corpus_is_plainly_empty() -> None:
    # FR-039d: the ordinary starting state of every session. Not an error, and not
    # somebody else's entries.
    with TestClient(app) as client:
        _ensure_session(client)
        response = client.get("/faq")

    assert response.status_code == 200
    assert response.json() == []


def test_a_request_with_no_session_at_all_lists_nothing_and_creates_nothing() -> None:
    with TestClient(app) as client:
        listed = client.get("/faq")
        with patch("chat.rag.indexing.embed_texts", fake_embed_texts):
            created = client.post("/faq", json={"content": "Parking is free."})

    assert listed.json() == []
    assert created.status_code == 404


def test_a_create_beyond_the_cap_is_refused_before_either_store_is_touched() -> None:
    # FR-039f: retrieval carries the session's live revisions as a filter term on every
    # FAQ turn, so corpus size sits on that hot path. The refusal names the reason, and
    # it happens before anything is chunked, embedded or written.
    with (
        patch("chat.core.config.get_settings") as settings,
        patch("chat.api.faq.get_settings") as faq_settings,
    ):
        capped = Settings(FAQ_MAX_ENTRIES_PER_SESSION=2)
        settings.return_value = capped
        faq_settings.return_value = capped
        with TestClient(app) as client:
            _ensure_session(client)
            _create(client, "Visiting hours are 8am to 5pm.")
            _create(client, "Parking is free for the first hour.")
            with (
                patch("chat.rag.indexing.embed_texts") as embed,
                patch("chat.rag.indexing.upsert_chunks") as upsert,
                capture_logs() as logs,
            ):
                refused = client.post("/faq", json={"content": "One too many."})
            listed = client.get("/faq").json()

    assert refused.status_code == 409
    assert "full" in refused.json()["detail"]
    assert "2" in refused.json()["detail"]
    embed.assert_not_called()
    upsert.assert_not_called()
    assert len(listed) == 2
    refusal = next(e for e in logs if e["event"] == "faq.create_refused")
    assert refusal["entry_count"] == 2
    assert refusal["cap"] == 2


def test_editing_and_deleting_still_work_on_a_full_corpus() -> None:
    # FR-039g: the cap is on creating entries, and nothing else a session accumulates
    # is refused for count.
    with (
        patch("chat.core.config.get_settings") as settings,
        patch("chat.api.faq.get_settings") as faq_settings,
    ):
        capped = Settings(FAQ_MAX_ENTRIES_PER_SESSION=2)
        settings.return_value = capped
        faq_settings.return_value = capped
        with TestClient(app) as client:
            _ensure_session(client)
            first = _create(client, "Visiting hours are 8am to 5pm.")
            second = _create(client, "Parking is free for the first hour.")

            with patch("chat.rag.indexing.embed_texts", fake_embed_texts):
                edited = client.put(
                    f"/faq/{first['id']}", json={"content": "Visiting hours changed."}
                )
            deleted = client.delete(f"/faq/{second['id']}")
            # Deleting one makes room immediately.
            with patch("chat.rag.indexing.embed_texts", fake_embed_texts):
                created = client.post("/faq", json={"content": "Room for one more."})

    assert edited.status_code == 200
    assert deleted.status_code == 204
    assert created.status_code == 201


def test_a_delete_removes_the_row_before_it_touches_the_index() -> None:
    # FR-042f: removing the row un-publishes every revision it named, so the entry is
    # unanswerable at that instant. The index is housekeeping from then on.
    order: list[str] = []
    real_delete = faq_repository.delete

    async def _recording_row_delete(*args: Any, **kwargs: Any) -> bool:
        order.append("row")
        return await real_delete(*args, **kwargs)

    async def _recording_chunk_delete(*_args: Any, **_kwargs: Any) -> None:
        order.append("chunks")

    with TestClient(app) as client:
        _ensure_session(client)
        entry = _create(client, "Visiting hours are 8am to 5pm.")
        with (
            patch("chat.api.faq.faq_repository.delete", _recording_row_delete),
            patch("chat.rag.indexing.delete_by_entry", _recording_chunk_delete),
        ):
            response = client.delete(f"/faq/{entry['id']}")

    assert response.status_code == 204
    assert order == ["row", "chunks"]


def test_a_failed_chunk_removal_is_not_a_failed_delete() -> None:
    # The rows that vouched for those chunks are already gone, so they are unreachable.
    # Reporting the leak as an incomplete delete would send a staff member back to
    # re-run something that already achieved every observable effect.
    with TestClient(app) as client:
        _ensure_session(client)
        entry = _create(client, "Visiting hours are 8am to 5pm.")
        with (
            patch(
                "chat.rag.indexing.delete_by_entry",
                side_effect=RuntimeError("qdrant is down"),
            ),
            capture_logs() as logs,
        ):
            response = client.delete(f"/faq/{entry['id']}")
        listed = client.get("/faq").json()

    assert response.status_code == 204
    assert listed == []
    events = [e["event"] for e in logs]
    assert "critical.dependency_unreachable" not in events
    assert "faq.operation_failed" not in events


def test_a_deleted_entry_is_never_citable_again() -> None:
    # Whatever happened to its chunks: nothing names their revision live, so retrieval
    # cannot reach them.
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(["unused"])
        with TestClient(app) as client:
            _ensure_session(client)
            entry = _create(client, "Visiting hours are 8am to 5pm.")
            with patch(
                "chat.rag.indexing.delete_by_entry",
                side_effect=RuntimeError("qdrant is down"),
            ):
                client.delete(f"/faq/{entry['id']}")
            response = client.post(
                "/chat",
                json={
                    "chat_id": chat_id_for(client),
                    "message": "when can I visit?",
                    "local_now": LOCAL_NOW,
                },
            )

    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[-1]["grounded"] is False
    assert lines[-1]["citations"] == []
