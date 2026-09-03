"""Practitioner administration through the console, against the real scheduler.

The proxy's own tests fake the transport; this one does not. It runs chat's HTTP proxy
against the scheduler's real `/practitioners` API, backed by the real scheduling
database, and then asks the *assistant's* own capability what it now sees — because
FR-037's claim is not that the proxy relayed a request, it is that a schedule edited
from a screen changes the times the assistant offers.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Any

import aiohttp
import grpc
import pytest_asyncio
import uvicorn
from chat.agent.tools.scheduling_tools import derive_idempotency_key
from chat.clients import scheduler_rest, scheduling
from fastapi import FastAPI
from scheduler.api.practitioners import router as practitioners_router
from scheduler.db.session import session_factory
from scheduler.domain.models import Appointment, Patient
from shared_models.scheduling import Weekday
from sqlalchemy import select
from ulid import ULID

from .conftest import new_id
from .test_booking_roundtrip import _chat_settings

_TUESDAY = date(2026, 8, 18)
_TUESDAY_9AM = datetime(2026, 8, 18, 9, 0)
_LOCAL_NOW = datetime(2026, 8, 17, 8, 0)
# `Weekday` is Monday-based and numeric, matching `date.weekday()` - the HTTP surface
# carries the number, not a name, so this is the value a console form sends too. The
# gRPC contract's own `Weekday` numbering differs, reserving zero for "unset"; the
# scheduler maps between the two at that boundary.
_TUESDAY_WEEKDAY = Weekday.TUESDAY.value


def _hours(start: str, end: str) -> dict[str, Any]:
    """One Tuesday working range, in the shape the scheduler accepts."""
    return {"weekday": _TUESDAY_WEEKDAY, "start_time": start, "end_time": end}


@pytest_asyncio.fixture
async def scheduler_http() -> AsyncIterator[str]:
    """Serve the scheduler's real practitioner API on a loopback port.

    Only that router, without the service's own lifespan: the lifespan starts a gRPC
    server on a fixed port, and this tier already runs one of those on a port of its
    own. What is under test is the REST surface the proxy speaks to, and this is that
    surface, unmodified.
    """
    app = FastAPI()
    app.include_router(practitioners_router)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await serving


async def _proxy(
    base_url: str,
    method: str,
    path: str,
    session_id: str,
    body: Any | None = None,
) -> scheduler_rest.ProxiedResponse:
    """Send one request the way the console route does, over a real HTTP session."""
    async with aiohttp.ClientSession() as http:
        return await scheduler_rest.forward(
            http, base_url, method, path, session_id, body
        )


async def _seed_patient(session_id: str) -> str:
    async with session_factory() as session:
        patient = Patient(
            id=str(ULID()),
            session_id=session_id,
            chat_id=new_id(),
            full_name="Ada Lovelace",
        )
        session.add(patient)
        await session.commit()
        return patient.id


async def test_a_blank_create_is_answered_with_the_schedulers_own_defaults(
    scheduler_http: str,
) -> None:
    # Everything defaulted, including the name: the console supplies none of it, which
    # is what keeps one rule in one place.
    session_id = new_id()

    created = await _proxy(scheduler_http, "POST", "/practitioners", session_id, {})

    assert created.status_code == 201
    assert isinstance(created.body, dict)
    assert created.body["full_name"]
    assert created.body["schedule"]


async def test_a_refusal_arrives_with_the_reason_the_scheduler_gave_it(
    scheduler_http: str,
) -> None:
    session_id = new_id()
    first = await _proxy(
        scheduler_http,
        "POST",
        "/practitioners",
        session_id,
        {"full_name": "William Osler"},
    )
    assert first.status_code == 201

    duplicate = await _proxy(
        scheduler_http,
        "POST",
        "/practitioners",
        session_id,
        {"full_name": "William Osler"},
    )

    assert duplicate.status_code == 409
    assert isinstance(duplicate.body, dict)
    assert "already has that name" in duplicate.body["detail"]


async def test_another_sessions_practitioner_is_reported_as_not_existing(
    scheduler_http: str,
) -> None:
    mine = new_id()
    theirs = new_id()
    created = await _proxy(scheduler_http, "POST", "/practitioners", theirs, {})
    assert isinstance(created.body, dict)

    read = await _proxy(
        scheduler_http,
        "PATCH",
        f"/practitioners/{created.body['id']}",
        mine,
        {"full_name": "Someone Else"},
    )

    assert read.status_code == 404


async def test_a_schedule_edited_from_the_console_changes_what_is_offered(
    scheduler_http: str, scheduling_channel: grpc.aio.Channel
) -> None:
    # FR-037/SC-014, and the only thing that proves the proxy is editing the same
    # practitioners the assistant books against.
    session_id = new_id()
    patient_id = await _seed_patient(session_id)
    created = await _proxy(
        scheduler_http,
        "POST",
        "/practitioners",
        session_id,
        {
            "full_name": "William Osler",
            "appointment_duration_minutes": 60,
            "schedule": [
                _hours("09:00", "12:00"),
            ],
        },
    )
    assert isinstance(created.body, dict)
    practitioner_id = created.body["id"]

    before = await scheduling.check_availability(
        scheduling_channel,
        _chat_settings(),
        session_id=session_id,
        practitioner_id=practitioner_id,
        patient_id=patient_id,
        from_date=_TUESDAY,
        to_date=_TUESDAY,
        local_now=_LOCAL_NOW,
    )
    assert _TUESDAY_9AM in before.available_starts

    edited = await _proxy(
        scheduler_http,
        "PATCH",
        f"/practitioners/{practitioner_id}",
        session_id,
        {
            "schedule": [
                _hours("13:00", "17:00"),
            ]
        },
    )
    assert edited.status_code == 200

    after = await scheduling.check_availability(
        scheduling_channel,
        _chat_settings(),
        session_id=session_id,
        practitioner_id=practitioner_id,
        patient_id=patient_id,
        from_date=_TUESDAY,
        to_date=_TUESDAY,
        local_now=_LOCAL_NOW,
    )

    assert _TUESDAY_9AM not in after.available_starts
    assert after.available_starts
    assert all(start.hour >= 13 for start in after.available_starts)


async def test_deleting_a_practitioner_takes_their_appointments_with_them(
    scheduler_http: str, scheduling_channel: grpc.aio.Channel
) -> None:
    session_id = new_id()
    patient_id = await _seed_patient(session_id)
    created = await _proxy(
        scheduler_http,
        "POST",
        "/practitioners",
        session_id,
        {
            "full_name": "William Osler",
            "appointment_duration_minutes": 60,
            "schedule": [
                _hours("09:00", "17:00"),
            ],
        },
    )
    assert isinstance(created.body, dict)
    practitioner_id = created.body["id"]

    booked = await scheduling.book_appointment(
        scheduling_channel,
        _chat_settings(),
        session_id=session_id,
        patient_id=patient_id,
        practitioner_id=practitioner_id,
        starts_at=_TUESDAY_9AM,
        local_now=_LOCAL_NOW,
        idempotency_key=derive_idempotency_key(
            patient_id, practitioner_id, _TUESDAY_9AM
        ),
    )
    assert isinstance(booked, scheduling.BookingSuccess)

    deleted = await _proxy(
        scheduler_http, "DELETE", f"/practitioners/{practitioner_id}", session_id
    )

    assert deleted.status_code == 204
    async with session_factory() as session:
        remaining = await session.execute(
            select(Appointment).where(Appointment.session_id == session_id)
        )
    # The cascade is keyed on the practitioner and is status-blind, so a cancelled
    # appointment goes the same way a standing one does.
    assert remaining.scalars().all() == []
