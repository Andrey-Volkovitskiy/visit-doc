from fastapi.testclient import TestClient
from scheduler.main import app

_EXPECTED = [
    "Cardiology",
    "Dentistry",
    "Dermatology",
    "General Practice",
    "Gynecology",
    "Neurology",
    "Ophthalmology",
    "Orthopedics",
    "Pediatrics",
    "Psychiatry",
]


def test_every_specialty_is_returned_name_sorted() -> None:
    with TestClient(app) as client:
        response = client.get("/specialties")

    assert response.status_code == 200
    assert response.json() == _EXPECTED


def test_the_list_is_closed_at_ten() -> None:
    with TestClient(app) as client:
        assert len(client.get("/specialties").json()) == 10


def test_no_session_header_is_required() -> None:
    """The only route in this API without one: the list holds no session data."""
    with TestClient(app) as client:
        response = client.get("/specialties")

    assert response.status_code == 200
