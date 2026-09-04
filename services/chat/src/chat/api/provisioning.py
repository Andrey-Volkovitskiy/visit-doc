"""Creating a chat's patient in the scheduler, and caching its name here."""

import grpc

from chat.clients import scheduling
from chat.clients.scheduling import SchedulingError
from chat.core.config import get_settings
from chat.core.logging import get_logger
from chat.db.session import session_factory
from chat.domain.models import Chat
from chat.repositories import chat_repository


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
    without a call. The name is assigned once, when the patient is created, and never
    changes afterwards, so the cached copy cannot go stale and re-reading it would cost
    a round trip for a value that is already correct.
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
