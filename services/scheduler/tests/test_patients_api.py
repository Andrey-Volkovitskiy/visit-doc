"""Tests for the patient admin routes: listing, and the absence of everything else."""

from fastapi.testclient import TestClient
from scheduler.db.session import session_factory
from scheduler.main import app

from .conftest import admin_api, new_id, seed_patient


def _headers(session_id: str) -> dict[str, str]:
    return {"X-Session-Id": session_id}


def test_a_missing_session_header_is_unauthorized() -> None:
    with TestClient(app) as client:
        assert client.get("/patients").status_code == 401


async def test_listing_shows_only_this_sessions_patients() -> None:
    session_id = new_id()
    async with session_factory() as session:
        await seed_patient(session, session_id, full_name="Mine")
        await seed_patient(session, new_id(), full_name="Theirs")

    async with admin_api(session_id) as client:
        listed = (await client.get("/patients")).json()

    assert [p["full_name"] for p in listed] == ["Mine"]


async def test_a_patient_cannot_be_renamed_through_this_surface() -> None:
    """A patient's name is assigned once, at creation, and never edited afterwards."""
    session_id = new_id()
    async with session_factory() as session:
        patient = await seed_patient(session, session_id, full_name="Ada")

    async with admin_api(session_id) as client:
        response = await client.patch(
            f"/patients/{patient.id}", json={"full_name": "Ada Lovelace"}
        )
        still_named = (await client.get("/patients")).json()

    assert response.status_code == 404
    assert [p["full_name"] for p in still_named] == ["Ada"]


def test_there_is_no_way_to_create_or_delete_a_patient_here() -> None:
    """A patient is created with its chat and deleted with it, never on its own."""
    with TestClient(app) as client:
        created = client.post(
            "/patients", json={"full_name": "x"}, headers=_headers(new_id())
        )
        deleted = client.delete(f"/patients/{new_id()}", headers=_headers(new_id()))

    # The collection path is routed, for the listing, so a write to it is refused as a
    # method; there is no per-patient path at all, so a write to one is refused as a
    # path that does not exist.
    assert created.status_code == 405
    assert deleted.status_code == 404
