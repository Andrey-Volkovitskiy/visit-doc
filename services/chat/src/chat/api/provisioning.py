"""What a new chat and a new session are given: a patient, and a starter corpus."""

import grpc
from qdrant_client import AsyncQdrantClient
from ulid import ULID
from voyageai.client_async import AsyncClient

from chat.clients import scheduling
from chat.clients.scheduling import SchedulingError
from chat.core.config import get_settings
from chat.core.correlation import bind_operation_id
from chat.core.logging import get_logger
from chat.db.session import session_factory
from chat.domain.models import Chat
from chat.rag.default_corpus import DEFAULT_FAQ_ENTRIES
from chat.rag.indexing import (
    DEPENDENCY_BY_STEP,
    FaqOperationError,
    PendingRevision,
    publish_revisions,
)
from chat.repositories import chat_repository, faq_repository


async def provision_patient(channel: grpc.aio.Channel, chat: Chat) -> str | None:
    """Create this chat's patient (and the session's first practitioner) if needed.

    Returns: the patient's name - the existing one when the chat already has a patient,
        the new one when this call created it - or None if the scheduler did not
        provision one, which is the only thing None means.

    No scheduling failure escapes: chat creation and answering are documented never to
    fail on the scheduler, and a caller cannot honor that while only the *anticipated*
    failure is caught here - a rejected request or an unresolvable chat would surface
    as a 500 on a route that promised otherwise.

    Mutates `chat.patient_id` in place on success as well as persisting it, so a caller
    holding the row sees the new value without re-reading. Safe to call repeatedly: the
    scheduler keys the patient on the chat, so a later attempt after a failure returns
    the same patient rather than creating a second one.

    Creation only - a chat that already has a patient is answered from the cached name
    without a call. Keeping the cache current is the rename route's job, which writes
    both stores in one request; re-reading here instead would put a second writer on
    `patient_name` that could overwrite a rename with the value it read just before it.
    """
    if chat.patient_id is not None:
        return chat.patient_name
    try:
        result = await scheduling.ensure_session_provisioned(
            channel,
            get_settings(),
            session_id=chat.session_id,
            chat_id=chat.id,
        )
    except SchedulingError as exc:
        get_logger().warning(
            "patient.provisioning_failed",
            chat_id=chat.id,
            error_type=type(exc).__name__,
            error_detail=str(exc),
        )
        return None

    async with session_factory() as db_session:
        await chat_repository.set_patient(
            db_session,
            chat.id,
            chat.session_id,
            result.patient.id,
            result.patient.full_name,
        )
    chat.patient_id = result.patient.id
    chat.patient_name = result.patient.full_name
    get_logger().info(
        "patient.provisioned",
        chat_id=chat.id,
        patient_id=result.patient.id,
        created=result.patient_created,
    )
    return result.patient.full_name


async def seed_default_corpus(
    qdrant_client: AsyncQdrantClient,
    voyage_client: AsyncClient,
    session_id: str,
) -> None:
    """Plant the starter corpus in a session that has just been created.

    Never raises, and never reports a failure to the caller: a session whose corpus
    could not be planted starts empty, which is a state the whole system already
    handles - the console shows it as empty, and a question it cannot answer abstains
    and calls staff. Failing chat creation over it instead would deny the visitor the
    conversation as well as the answers. The failure is logged as an operation that
    failed, and as a critical event where a dependency was unreachable, because nobody
    reading the empty corpus later could tell it apart from one a session emptied
    itself.

    Planted the same way a save is: the chunks are written first, under revisions
    nothing yet names live, and one commit publishes every entry at once. So a failure
    at any step leaves the session with the corpus it had a moment ago - no entries -
    rather than some of them, and the cost of the failed attempt is leaked chunks.

    Call it once, on a session that was just created and whose id has not left this
    process yet. Called again on a session already holding this corpus, it would plant a
    second copy of every entry.
    """
    # Never more than the session's own ceiling, so the corpus this hands a brand-new
    # session cannot start out over the limit its own creates are held to.
    contents = DEFAULT_FAQ_ENTRIES[: get_settings().FAQ_MAX_ENTRIES_PER_SESSION]
    if not contents:
        return

    with bind_operation_id():
        try:
            planted = await _plant_corpus(
                qdrant_client, voyage_client, session_id, contents
            )
        except Exception as exc:  # noqa: BLE001 - see the docstring: nothing escapes
            _log_seed_failure(session_id, exc)
            return

        get_logger().info(
            "faq.default_corpus_seeded", session_id=session_id, entry_count=planted
        )


async def _plant_corpus(
    qdrant_client: AsyncQdrantClient,
    voyage_client: AsyncClient,
    session_id: str,
    contents: tuple[str, ...],
) -> int:
    """Write every entry's chunks, then publish all of them with one commit.

    Returns: how many entries were planted.

    Raises: FaqOperationError tagged with the step that failed - "reserve" or "publish"
        for the two database steps here, and whichever sub-step `publish_revisions`
        names for the rest.

    The ids come from the sequence before anything is written, so each entry's chunks
    carry the entry they belong to before the row that publishes them exists - and ids
    reserved by an attempt that then fails are simply never used.
    """
    try:
        async with session_factory() as db_session:
            entry_ids = await faq_repository.reserve_ids(db_session, len(contents))
    except Exception as exc:
        raise FaqOperationError("reserve", exc) from exc

    pending = [
        PendingRevision(entry_id, str(ULID()), content)
        for entry_id, content in zip(entry_ids, contents, strict=True)
    ]
    await publish_revisions(qdrant_client, voyage_client, session_id, pending)

    try:
        async with session_factory() as db_session:
            await faq_repository.create_many(
                db_session,
                session_id,
                [(item.faq_entry_id, item.content, item.revision) for item in pending],
            )
    except Exception as exc:
        raise FaqOperationError("publish", exc) from exc
    return len(pending)


# Which external system each failed step was against. The two database steps are named
# here; the rest come from the module that raises them, so a step and its dependency
# are still named in one place.
_DEPENDENCY_BY_STEP = {
    **DEPENDENCY_BY_STEP,
    "reserve": "postgres",
    "publish": "postgres",
}


def _log_seed_failure(session_id: str, exc: Exception) -> None:
    """Log `faq.default_corpus_seed_failed`, plus a critical event for a dependency.

    Anything that is not a tagged `FaqOperationError` reached here from a defect rather
    than from a dependency, so it is recorded as an untagged failure and raises no
    critical event - which is reserved for naming a system that was unreachable, and
    would name the wrong one if it were guessed from a step this never ran.
    """
    if isinstance(exc, FaqOperationError):
        failed_step, cause = exc.failed_step, exc.cause
    else:
        failed_step, cause = "unexpected", exc
    logger = get_logger()
    logger.error(
        "faq.default_corpus_seed_failed",
        session_id=session_id,
        failed_step=failed_step,
        error_detail=str(cause),
    )
    dependency = _DEPENDENCY_BY_STEP.get(failed_step)
    if dependency is not None:
        logger.critical(
            "critical.dependency_unreachable",
            dependency=dependency,
            error_detail=str(cause),
        )
