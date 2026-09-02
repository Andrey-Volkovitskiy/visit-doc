"""One session's corpus edits, and everything they must not reach.

Two claims are checked here, against a real Postgres and a real Qdrant. The first is
that a session's corpus is *its own*: another session's answers, its citations and its
groundedness verdicts are untouched by anything done here, because retrieval filters on
the session's own live revisions as a term on the search rather than discarding foreign
results afterwards.

The second is that the write path and the read path agree end to end - an entry added
is answered from, an entry edited is answered from its new text, and an entry deleted
takes its answer with it, which hands the conversation to staff rather than producing an
answer nothing supports.
"""

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from chat.core.config import Settings as ChatSettings
from chat.db.session import engine, session_factory
from chat.domain.models import EscalationReason
from chat.main import app
from chat.repositories import chat_repository
from chat.repositories.qdrant_repository import (
    COLLECTION_NAME,
    ChunkPayload,
    create_client,
    ensure_collection,
)
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient, Response

from .conftest import LOCAL_NOW, fake_anthropic_client, fake_embed_texts

_VISITING = "Visiting hours are 8am to 5pm on weekdays."
_PARKING = "Parking is free for the first hour."
_QUESTION = "when can I visit?"


@pytest.fixture(autouse=True)
def _no_scheduler() -> Iterator[None]:
    """Fake the scheduling boundary for the chat app these tests drive.

    Nothing here is about scheduling, and the app's gRPC channel is bound to its own
    lifespan's loop rather than this test's - so an unfaked provisioning call would
    fail on the loop rather than on anything the test is actually about. Unreachable is
    also the honest default: a chat whose patient record was never created still answers
    FAQ questions, which is the whole subject of this file.
    """
    from chat.clients.scheduling import SchedulingUnavailableError

    with patch(
        "chat.api.provisioning.scheduling.ensure_session_provisioned",
        new=AsyncMock(
            side_effect=SchedulingUnavailableError(
                "no scheduler in this test", outcome_unknown=False
            )
        ),
    ):
        yield


@pytest_asyncio.fixture(autouse=True)
async def _qdrant_collection() -> AsyncIterator[None]:
    qdrant_client = create_client(ChatSettings())
    try:
        await ensure_collection(qdrant_client)
    finally:
        await qdrant_client.close()
    yield
    await engine.dispose()


async def _session_id() -> str:
    async with session_factory() as session:
        session_row = await chat_repository.create_session(session)
    await engine.dispose()
    return session_row.id


async def _call(
    session_id: str, method: str, path: str, body: Any | None = None
) -> Response:
    """Drive one chat-service route for `session_id`, with fake embeddings."""
    await engine.dispose()
    with (
        patch("chat.rag.indexing.embed_texts", fake_embed_texts),
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(
            ["Visiting hours are as the clinic's documents say."]
        )
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                http.cookies.set("visitdoc_session_id", session_id)
                return await http.request(method, path, json=body)


async def _ask(
    session_id: str, chat_id: str, message: str = _QUESTION
) -> dict[str, Any]:
    """Ask one question and return the turn's terminal event."""
    response = await _call(
        session_id,
        "POST",
        "/chat",
        {"chat_id": chat_id, "message": message, "local_now": LOCAL_NOW},
    )
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    return dict(lines[-1])


async def _chat_for(session_id: str) -> str:
    return str((await _call(session_id, "POST", "/chats")).json()["id"])


async def _add(session_id: str, content: str) -> int:
    response = await _call(session_id, "POST", "/faq", {"content": content})
    assert response.status_code == 201
    return int(response.json()["id"])


async def _points() -> list[ChunkPayload]:
    qdrant_client = create_client(ChatSettings())
    try:
        records, _ = await qdrant_client.scroll(
            collection_name=COLLECTION_NAME, limit=1000, with_payload=True
        )
    finally:
        await qdrant_client.close()
    return [ChunkPayload.model_validate(r.payload) for r in records if r.payload]


def _cited_texts(done: dict[str, Any]) -> list[str]:
    return [c["chunk_text"] for c in done.get("citations", [])]


# --- one session's corpus, and the other's answers -----------------------------------


async def test_one_sessions_edits_change_nothing_the_other_answers() -> None:
    # SC-011a. The filter is a term on the search, so another session's chunks are not
    # retrieved rather than retrieved and discarded - which is the difference between
    # a leak that a test catches and one that only shows up in the app.
    mine = await _session_id()
    theirs = await _session_id()
    my_entry = await _add(mine, _VISITING)
    await _add(theirs, _VISITING)
    their_chat = await _chat_for(theirs)

    before = await _ask(theirs, their_chat)
    assert before["grounded"] is True
    cited_before = _cited_texts(before)

    # Everything one session can do to a corpus, done to the other's.
    await _call(mine, "PUT", f"/faq/{my_entry}", {"content": "Something else."})
    await _add(mine, _PARKING)
    await _call(mine, "DELETE", f"/faq/{my_entry}")

    their_second_chat = await _chat_for(theirs)
    after = await _ask(theirs, their_second_chat)

    assert after["grounded"] is True
    assert _cited_texts(after) == cited_before
    assert all(_VISITING in text for text in _cited_texts(after))


async def test_no_citation_ever_names_another_sessions_entry() -> None:
    mine = await _session_id()
    theirs = await _session_id()
    my_entry = await _add(mine, _VISITING)
    their_entry = await _add(theirs, _VISITING)

    my_answer = await _ask(mine, await _chat_for(mine))
    their_answer = await _ask(theirs, await _chat_for(theirs))

    assert [c["entry_id"] for c in my_answer["citations"]] == [my_entry]
    assert [c["entry_id"] for c in their_answer["citations"]] == [their_entry]


async def test_a_session_with_an_empty_corpus_answers_from_nobody_elses() -> None:
    # The starting state of every session, and the one an implementation that forgot
    # the filter would quietly answer from a stranger's documents.
    theirs = await _session_id()
    await _add(theirs, _VISITING)
    mine = await _session_id()

    done = await _ask(mine, await _chat_for(mine))

    assert done["grounded"] is False
    assert done["citations"] == []


async def test_a_delete_leaves_the_other_sessions_chunks_in_place() -> None:
    mine = await _session_id()
    theirs = await _session_id()
    my_entry = await _add(mine, _VISITING)
    await _add(theirs, _VISITING)

    await _call(mine, "DELETE", f"/faq/{my_entry}")

    sessions = {c.session_id for c in await _points()}
    assert sessions == {theirs}


# --- the write path and the read path agree ------------------------------------------


async def test_an_added_entry_is_what_the_next_answer_cites() -> None:
    session_id = await _session_id()
    entry_id = await _add(session_id, _VISITING)

    done = await _ask(session_id, await _chat_for(session_id))

    assert done["grounded"] is True
    assert [c["entry_id"] for c in done["citations"]] == [entry_id]
    assert _cited_texts(done) == [_VISITING]


async def test_an_edited_entry_is_cited_by_its_new_text() -> None:
    # The superseded revision is swept, so there is no version of this entry the
    # assistant could still answer from.
    session_id = await _session_id()
    entry_id = await _add(session_id, _VISITING)
    edited = "Visiting hours are 9am to 6pm on weekdays."

    await _call(session_id, "PUT", f"/faq/{entry_id}", {"content": edited})
    done = await _ask(session_id, await _chat_for(session_id))

    assert _cited_texts(done) == [edited]
    assert _VISITING not in _cited_texts(done)


async def test_a_deleted_entrys_question_abstains_and_calls_staff() -> None:
    # SC-016 and FR-003 together: the answer goes, and what replaces it is a person -
    # not an unsupported answer, and not a dead end.
    session_id = await _session_id()
    entry_id = await _add(session_id, _VISITING)
    chat_id = await _chat_for(session_id)

    await _call(session_id, "DELETE", f"/faq/{entry_id}")
    done = await _ask(session_id, chat_id)

    assert done["grounded"] is False
    assert done["citations"] == []
    async with session_factory() as session:
        state = await chat_repository.get_conversation_state(
            session, chat_id, session_id
        )
    assert state is not None
    assert state.escalation_reason == EscalationReason.CORPUS_COULD_NOT_ANSWER
    assert state.may_assistant_reply is False
