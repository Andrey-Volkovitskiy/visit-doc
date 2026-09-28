"""Postgres `PairingCode` repository: the one code a session's console issued last.

None of these functions commit: consuming a code is one step of completing a sign-in,
which commits it together with the steps around it, so the caller owns the
transaction.
"""

from datetime import timedelta

from sqlalchemy import Integer, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from chat.connectors.secrets import digest, new_pairing_code, normalize_pairing_code
from chat.domain.models import PairingCode

PAIRING_CODE_LIFETIME = timedelta(minutes=10)


async def issue(session: AsyncSession, session_id: str) -> str:
    """Issue `session_id` a new pairing code, replacing any it held.

    Returns: the code as a person is shown it. This is the only time it exists in
        plain form; only its digest is stored.

    The row is keyed by the session, so the upsert overwrites the earlier code's digest
    and that code stops matching with no separate step to invalidate it.
    """
    code = new_pairing_code()
    values = {
        "code_hash": digest(normalize_pairing_code(code)),
        "expires_at": func.now() + PAIRING_CODE_LIFETIME,
        "used_at": None,
        "created_at": func.now(),
    }
    await session.execute(
        insert(PairingCode)
        .values(session_id=session_id, **values)
        .on_conflict_do_update(index_elements=[PairingCode.session_id], set_=values)
    )
    return code


async def consume(session: AsyncSession, typed: str) -> str | None:
    """Spend the code a person typed, if it is live.

    Returns: the session the code pairs to, or None when it matches no unused,
        unexpired code. One answer for wrong, expired and used alike, so a guesser
        learns nothing from which it was.

    One conditional `UPDATE`: of two concurrent consumes of one code, the second waits
    on the first's row lock and then finds `used_at` set, so exactly one gets a row.
    """
    result = await session.execute(
        update(PairingCode)
        .where(
            PairingCode.code_hash == digest(normalize_pairing_code(typed)),
            PairingCode.used_at.is_(None),
            PairingCode.expires_at > func.now(),
        )
        .values(used_at=func.now())
        .returning(PairingCode.session_id)
    )
    return result.scalars().first()


async def live_expiry(session: AsyncSession, session_id: str) -> int | None:
    """Return how many whole seconds `session_id`'s code has left, or None if none.

    None when the session holds no code, or its code is used or expired. The code
    itself is never read back.
    """
    remaining = func.ceil(
        func.extract("epoch", PairingCode.expires_at - func.now())
    ).cast(Integer)
    result = await session.execute(
        select(remaining).where(
            PairingCode.session_id == session_id,
            PairingCode.used_at.is_(None),
            PairingCode.expires_at > func.now(),
        )
    )
    return result.scalars().first()
