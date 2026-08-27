"""Tests for the practitioner admin routes."""

from datetime import datetime
from typing import Any

from fastapi.testclient import TestClient
from scheduler.db.session import session_factory
from scheduler.domain.models import Appointment
from scheduler.domain.name_pools import PHYSICIAN_POOL
from scheduler.main import app
from sqlalchemy import func, select

from .conftest import (
    admin_api,
    make_appointment,
    new_id,
    seed_patient,
    seed_practitioner,
)

_SESSION = new_id()


def _headers(session_id: str = _SESSION) -> dict[str, str]:
    return {"X-Session-Id": session_id}


def _create(client: TestClient, body: dict[str, Any], **kwargs: Any) -> Any:
    return client.post("/practitioners", json=body, headers=_headers(**kwargs))


def test_a_bare_create_yields_an_immediately_bookable_practitioner() -> None:
    with TestClient(app) as client:
        response = _create(client, {})

    assert response.status_code == 201
    body = response.json()
    assert body["full_name"] == PHYSICIAN_POOL[0]
    assert body["specialty"] == "General Practice"
    assert body["appointment_duration_minutes"] == 60
    assert [r["weekday"] for r in body["schedule"]] == [0, 1, 2, 3, 4]
    assert body["schedule"][0]["start_time"] == "09:00"
    assert body["schedule"][0]["end_time"] == "17:00"


def test_a_specialty_outside_the_list_is_rejected() -> None:
    with TestClient(app) as client:
        response = _create(client, {"specialty": "Paediatric dermatology"})

    assert response.status_code == 422


def test_a_split_shift_is_accepted() -> None:
    with TestClient(app) as client:
        response = _create(
            client,
            {
                "specialty": "Dentistry",
                "appointment_duration_minutes": 30,
                "schedule": [
                    {"weekday": 1, "start_time": "08:00", "end_time": "12:00"},
                    {"weekday": 1, "start_time": "13:00", "end_time": "16:00"},
                ],
            },
        )

    assert response.status_code == 201
    assert len(response.json()["schedule"]) == 2


def test_overlapping_ranges_on_one_weekday_are_rejected() -> None:
    with TestClient(app) as client:
        response = _create(
            client,
            {
                "schedule": [
                    {"weekday": 1, "start_time": "08:00", "end_time": "12:00"},
                    {"weekday": 1, "start_time": "11:00", "end_time": "14:00"},
                ]
            },
        )

    assert response.status_code == 422


def test_a_range_that_does_not_end_after_it_starts_is_rejected() -> None:
    with TestClient(app) as client:
        response = _create(
            client,
            {"schedule": [{"weekday": 1, "start_time": "12:00", "end_time": "12:00"}]},
        )

    assert response.status_code == 422


def test_a_time_carrying_an_offset_is_rejected() -> None:
    with TestClient(app) as client:
        response = _create(
            client,
            {
                "schedule": [
                    {"weekday": 1, "start_time": "09:00+02:00", "end_time": "17:00"}
                ]
            },
        )

    assert response.status_code == 422


def test_a_duplicate_name_in_one_session_is_a_conflict() -> None:
    with TestClient(app) as client:
        _create(client, {"full_name": "Dr Someone"})
        response = _create(client, {"full_name": "Dr Someone"})

    assert response.status_code == 409


def test_the_same_name_in_two_sessions_is_accepted() -> None:
    with TestClient(app) as client:
        first = _create(client, {"full_name": "Dr Someone"})
        second = client.post(
            "/practitioners",
            json={"full_name": "Dr Someone"},
            headers=_headers(new_id()),
        )

    assert first.status_code == 201
    assert second.status_code == 201


def test_a_missing_session_header_is_unauthorized() -> None:
    with TestClient(app) as client:
        assert client.get("/practitioners").status_code == 401
        assert client.post("/practitioners", json={}).status_code == 401


def test_listing_shows_only_this_sessions_practitioners() -> None:
    session_id = new_id()
    other_session = new_id()
    with TestClient(app) as client:
        client.post("/practitioners", json={}, headers=_headers(session_id))
        client.post("/practitioners", json={}, headers=_headers(other_session))

        listed = client.get("/practitioners", headers=_headers(session_id)).json()

    assert len(listed) == 1


def test_another_sessions_practitioner_is_not_found() -> None:
    with TestClient(app) as client:
        created = _create(client, {}, session_id=new_id()).json()

        patched = client.patch(
            f"/practitioners/{created['id']}",
            json={"full_name": "Renamed"},
            headers=_headers(new_id()),
        )
        deleted = client.delete(
            f"/practitioners/{created['id']}", headers=_headers(new_id())
        )

    assert patched.status_code == 404
    assert deleted.status_code == 404


def test_a_patch_leaves_omitted_fields_untouched() -> None:
    with TestClient(app) as client:
        created = _create(client, {"appointment_duration_minutes": 30}).json()

        patched = client.patch(
            f"/practitioners/{created['id']}",
            json={"specialty": "Cardiology"},
            headers=_headers(),
        ).json()

    assert patched["specialty"] == "Cardiology"
    assert patched["appointment_duration_minutes"] == 30
    assert patched["full_name"] == created["full_name"]


def test_a_rename_onto_an_existing_name_is_a_conflict() -> None:
    session_id = new_id()
    with TestClient(app) as client:
        client.post(
            "/practitioners", json={"full_name": "Taken"}, headers=_headers(session_id)
        )
        other = client.post(
            "/practitioners", json={"full_name": "Free"}, headers=_headers(session_id)
        ).json()

        response = client.patch(
            f"/practitioners/{other['id']}",
            json={"full_name": "Taken"},
            headers=_headers(session_id),
        )

    assert response.status_code == 409


async def test_narrowing_a_schedule_past_an_appointment_succeeds_and_keeps_it() -> None:
    session_id = new_id()
    async with session_factory() as session:
        practitioner = await seed_practitioner(session, session_id)
        patient = await seed_patient(session, session_id)
        session.add(
            make_appointment(
                session_id,
                patient.id,
                practitioner.id,
                datetime(2026, 8, 18, 9, 0),
                datetime(2026, 8, 18, 10, 0),
            )
        )
        await session.commit()

    async with admin_api(session_id) as client:
        response = await client.patch(
            f"/practitioners/{practitioner.id}",
            json={
                "schedule": [{"weekday": 1, "start_time": "14:00", "end_time": "16:00"}]
            },
        )

    assert response.status_code == 200
    assert response.json()["schedule"] == [
        {"weekday": 1, "start_time": "14:00", "end_time": "16:00"}
    ]

    async with session_factory() as session:
        stored = await session.execute(select(Appointment))
        appointments = list(stored.scalars().all())
    assert len(appointments) == 1
    assert appointments[0].starts_at == datetime(2026, 8, 18, 9, 0)


async def test_deleting_a_practitioner_removes_only_their_appointments() -> None:
    session_id = new_id()
    async with session_factory() as session:
        doomed = await seed_practitioner(session, session_id, full_name="Doomed")
        kept = await seed_practitioner(session, session_id, full_name="Kept")
        patient = await seed_patient(session, session_id)
        session.add(
            make_appointment(
                session_id,
                patient.id,
                doomed.id,
                datetime(2026, 8, 18, 9, 0),
                datetime(2026, 8, 18, 10, 0),
            )
        )
        session.add(
            make_appointment(
                session_id,
                patient.id,
                kept.id,
                datetime(2026, 8, 18, 11, 0),
                datetime(2026, 8, 18, 12, 0),
            )
        )
        await session.commit()

    async with admin_api(session_id) as client:
        response = await client.delete(f"/practitioners/{doomed.id}")

    assert response.status_code == 204
    async with session_factory() as session:
        remaining = await session.execute(select(func.count()).select_from(Appointment))
        assert remaining.scalar_one() == 1
