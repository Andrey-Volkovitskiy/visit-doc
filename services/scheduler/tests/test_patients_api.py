"""Tests for the patient admin routes: rename, and the absence of everything else."""

from fastapi.testclient import TestClient
from scheduler.db.session import session_factory
from scheduler.main import app

from .conftest import admin_api, new_id, seed_patient


def _headers(session_id: str) -> dict[str, str]:
    return {"X-Session-Id": session_id}


async def test_a_patient_can_be_renamed() -> None:
    session_id = new_id()
    async with session_factory() as session:
        patient = await seed_patient(session, session_id, full_name="Ada")

    async with admin_api(session_id) as client:
        response = await client.patch(
            f"/patients/{patient.id}", json={"full_name": "Ada Lovelace"}
        )

    assert response.status_code == 200
    assert response.json()["full_name"] == "Ada Lovelace"
    assert response.json()["chat_id"] == patient.chat_id


async def test_a_rename_onto_an_existing_name_in_the_session_is_a_conflict() -> None:
    session_id = new_id()
    async with session_factory() as session:
        await seed_patient(session, session_id, full_name="Taken")
        other = await seed_patient(session, session_id, full_name="Free")

    async with admin_api(session_id) as client:
        response = await client.patch(
            f"/patients/{other.id}", json={"full_name": "Taken"}
        )

    assert response.status_code == 409


async def test_a_rename_matching_another_sessions_patient_is_accepted() -> None:
    session_id = new_id()
    async with session_factory() as session:
        await seed_patient(session, new_id(), full_name="Ada Lovelace")
        mine = await seed_patient(session, session_id, full_name="Someone")

    async with admin_api(session_id) as client:
        response = await client.patch(
            f"/patients/{mine.id}", json={"full_name": "Ada Lovelace"}
        )

    assert response.status_code == 200


async def test_another_sessions_patient_is_not_found() -> None:
    async with session_factory() as session:
        patient = await seed_patient(session, new_id())

    async with admin_api(new_id()) as client:
        response = await client.patch(
            f"/patients/{patient.id}", json={"full_name": "Renamed"}
        )

    assert response.status_code == 404


def test_an_unknown_patient_is_not_found() -> None:
    with TestClient(app) as client:
        response = client.patch(
            f"/patients/{new_id()}",
            json={"full_name": "Renamed"},
            headers=_headers(new_id()),
        )

    assert response.status_code == 404


def test_a_missing_session_header_is_unauthorized() -> None:
    with TestClient(app) as client:
        assert client.get("/patients").status_code == 401
        assert (
            client.patch(f"/patients/{new_id()}", json={"full_name": "x"}).status_code
            == 401
        )


async def test_listing_shows_only_this_sessions_patients() -> None:
    session_id = new_id()
    async with session_factory() as session:
        await seed_patient(session, session_id, full_name="Mine")
        await seed_patient(session, new_id(), full_name="Theirs")

    async with admin_api(session_id) as client:
        listed = (await client.get("/patients")).json()

    assert [p["full_name"] for p in listed] == ["Mine"]


def test_there_is_no_way_to_create_or_delete_a_patient_here() -> None:
    """A patient is created with its chat and deleted with it, never on its own."""
    with TestClient(app) as client:
        created = client.post(
            "/patients", json={"full_name": "x"}, headers=_headers(new_id())
        )
        deleted = client.delete(f"/patients/{new_id()}", headers=_headers(new_id()))

    assert created.status_code == 405
    assert deleted.status_code == 405


def test_an_empty_name_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.patch(
            f"/patients/{new_id()}", json={"full_name": ""}, headers=_headers(new_id())
        )

    assert response.status_code == 422
