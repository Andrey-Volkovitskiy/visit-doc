"""A practitioner's week, read through the console against the real scheduler.

The unit tiers stop at a boundary each: chat's route test fakes the transport, and the
scheduler's API test never sees the bounds chat computes. This one runs chat's real
console route, with its real HTTP transport, against the scheduler's real
`/practitioners` API on a loopback port and the real scheduling database - so the
window chat derives from `local_now` is the window the scheduler's own `WHERE` applies,
and a status the scheduler filters on is the status its write paths store.

The bookings are made through the assistant's own gRPC capability rather than inserted,
so the cancelled one is cancelled the way a patient's would be.
"""

from datetime import datetime
from unittest.mock import patch

import aiohttp
import grpc
from chat.agent.tools.scheduling_tools import derive_idempotency_key
from chat.clients import scheduling
from chat.core.config import Settings as ChatSettings
from chat.db.session import engine
from chat.main import app
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient, Response

from .conftest import fake_anthropic_client, new_id
from .test_booking_roundtrip import _chat_settings, _seed

_TUESDAY_9AM = datetime(2026, 8, 18, 9, 0)
_TUESDAY_11AM = datetime(2026, 8, 18, 11, 0)
_LOCAL_NOW = datetime(2026, 8, 17, 8, 0)


async def _book(
    channel: grpc.aio.Channel,
    session_id: str,
    patient_id: str,
    practitioner_id: str,
    starts_at: datetime,
) -> scheduling.BookingSuccess:
    outcome = await scheduling.book_appointment(
        channel,
        _chat_settings(),
        session_id=session_id,
        patient_id=patient_id,
        practitioner_id=practitioner_id,
        starts_at=starts_at,
        local_now=_LOCAL_NOW,
        idempotency_key=derive_idempotency_key(patient_id, practitioner_id, starts_at),
    )
    assert isinstance(outcome, scheduling.BookingSuccess)
    return outcome


async def _read_week(
    scheduler_http: str, session_id: str, practitioner_id: str
) -> Response:
    """Read the practitioner's week through chat's console route, as a browser does."""
    settings = ChatSettings().model_copy(
        update={"SCHEDULING_HTTP_BASE_URL": scheduler_http}
    )
    await engine.dispose()
    with (
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch("chat.api.console.get_settings", return_value=settings),
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app):
            # The lifespan's HTTP session is bound to the TestClient's own loop; the
            # scheduler here is served on this test's loop, so the request goes out
            # through a session made on it.
            async with aiohttp.ClientSession() as http_session:
                app.state.http_session = http_session
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://t"
                ) as client:
                    client.cookies.set("visitdoc_session_id", session_id)
                    return await client.get(
                        f"/console/practitioners/{practitioner_id}/appointments",
                        params={"local_now": _LOCAL_NOW.isoformat()},
                    )


async def test_a_standing_booking_is_read_and_a_cancelled_one_is_not(
    scheduler_http: str, scheduling_channel: grpc.aio.Channel
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    standing = await _book(
        scheduling_channel, session_id, patient_id, practitioner_id, _TUESDAY_9AM
    )
    to_cancel = await _book(
        scheduling_channel, session_id, patient_id, practitioner_id, _TUESDAY_11AM
    )
    cancelled = await scheduling.cancel_appointment(
        scheduling_channel,
        _chat_settings(),
        session_id=session_id,
        patient_id=patient_id,
        appointment_id=to_cancel.appointment.id,
        expected_starts_at=_TUESDAY_11AM,
        expected_practitioner_id=practitioner_id,
        local_now=_LOCAL_NOW,
    )
    assert isinstance(cancelled, scheduling.ChangeApplied)

    response = await _read_week(scheduler_http, session_id, practitioner_id)

    assert response.status_code == 200
    assert response.json() == {
        "appointments": [
            {
                "id": standing.appointment.id,
                "patient_full_name": "Ada Lovelace",
                "starts_at": "2026-08-18T09:00:00",
                "ends_at": "2026-08-18T10:00:00",
            }
        ]
    }


async def test_another_sessions_practitioner_reads_as_not_found(
    scheduler_http: str,
) -> None:
    theirs = new_id()
    practitioner_id, _ = await _seed(theirs)

    response = await _read_week(scheduler_http, new_id(), practitioner_id)

    assert response.status_code == 404
