"""`pairing_code_repository`: the one code a session's console most recently issued.

I1: a session has at most one usable code, because the row is keyed by the session.
I2: a code is consumed at most once, because consuming is one conditional `UPDATE`.
"""

import asyncio

from chat.connectors.secrets import digest, normalize_pairing_code
from chat.db.session import session_factory
from chat.domain.models import PairingCode
from chat.repositories import pairing_code_repository
from sqlalchemy import text

from .conftest import issue_pairing_code, new_session_id


async def _consume(code: str) -> str | None:
    async with session_factory() as session:
        consumed = await pairing_code_repository.consume(session, code)
        await session.commit()
    return consumed


async def _live_expiry(session_id: str) -> int | None:
    async with session_factory() as session:
        return await pairing_code_repository.live_expiry(session, session_id)


async def _expire(session_id: str) -> None:
    async with session_factory() as session:
        await session.execute(
            text(
                "UPDATE mcp_pairing_codes SET expires_at = now() - interval '1 second' "
                "WHERE session_id = :s"
            ),
            {"s": session_id},
        )
        await session.commit()


async def test_an_issued_code_consumes_to_its_session() -> None:
    session_id = await new_session_id()
    code = await issue_pairing_code(session_id)

    assert await _consume(code) == session_id


async def test_a_code_consumes_in_lower_case_and_without_its_hyphen() -> None:
    session_id = await new_session_id()
    code = await issue_pairing_code(session_id)

    assert await _consume(code.lower().replace("-", "")) == session_id


async def test_a_code_is_consumed_at_most_once() -> None:
    session_id = await new_session_id()
    code = await issue_pairing_code(session_id)

    assert await _consume(code) == session_id
    assert await _consume(code) is None


async def test_a_new_code_replaces_the_one_before_it() -> None:
    # I1.
    session_id = await new_session_id()
    first = await issue_pairing_code(session_id)
    second = await issue_pairing_code(session_id)

    assert await _consume(first) is None
    assert await _consume(second) == session_id


async def test_an_expired_code_does_not_consume() -> None:
    session_id = await new_session_id()
    code = await issue_pairing_code(session_id)
    await _expire(session_id)

    assert await _consume(code) is None


async def test_an_unknown_code_consumes_nothing() -> None:
    await issue_pairing_code(await new_session_id())

    assert await _consume("0000-0000") is None


async def test_two_concurrent_consumes_of_one_code_pair_exactly_once() -> None:
    # I2, SC-007: the double tap. Each consume runs on its own connection.
    for _ in range(5):
        session_id = await new_session_id()
        code = await issue_pairing_code(session_id)

        results = await asyncio.gather(_consume(code), _consume(code))

        assert sorted(results, key=lambda r: r is None) == [session_id, None]


async def test_each_session_holds_its_own_code() -> None:
    mine = await new_session_id()
    theirs = await new_session_id()
    my_code = await issue_pairing_code(mine)
    their_code = await issue_pairing_code(theirs)

    assert await _consume(their_code) == theirs
    assert await _consume(my_code) == mine


async def test_the_code_is_stored_only_as_its_digest() -> None:
    session_id = await new_session_id()
    code = await issue_pairing_code(session_id)

    async with session_factory() as session:
        row = await session.get(PairingCode, session_id)

    assert row is not None
    assert row.code_hash == digest(normalize_pairing_code(code))
    assert code.replace("-", "") not in row.code_hash.upper()


async def test_live_expiry_reports_the_remaining_seconds_of_an_unused_code() -> None:
    session_id = await new_session_id()
    assert await _live_expiry(session_id) is None

    await issue_pairing_code(session_id)
    remaining = await _live_expiry(session_id)

    assert remaining is not None
    assert 590 <= remaining <= 600


async def test_live_expiry_reports_nothing_once_the_code_is_used_or_expired() -> None:
    used = await new_session_id()
    await _consume(await issue_pairing_code(used))
    expired = await new_session_id()
    await issue_pairing_code(expired)
    await _expire(expired)

    assert await _live_expiry(used) is None
    assert await _live_expiry(expired) is None
