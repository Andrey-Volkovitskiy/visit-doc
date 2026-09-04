"""Tests for the patient admin routes: listing, and the absence of everything else."""

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from scheduler.api.patients import router as patients_router
from scheduler.db.session import session_factory
from scheduler.main import app

from .conftest import admin_api, new_id, seed_patient


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


def test_the_listing_is_the_only_patient_route() -> None:
    """A patient is created with its chat and deleted with it, never on its own.

    Asserted against the routes themselves rather than a response status, because no
    status distinguishes the two: a re-added `DELETE /patients/{patient_id}` answering
    404 for an id that names no patient is indistinguishable, from the outside, from
    the unrouted path that answers 404 today. Both halves are needed - the router sees
    a route kept out of the published schema, the schema sees one declared elsewhere.
    """
    declared = {
        (route.path, method)
        for route in patients_router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    published = {
        (path, method.upper())
        for path, operations in app.openapi()["paths"].items()
        if path == "/patients" or path.startswith("/patients/")
        for method in operations
    }

    assert declared == {("/patients", "GET")}
    assert published == {("/patients", "GET")}
