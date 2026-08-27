import pytest
from shared_models.scheduling import BookingFailureReason, Specialty, Weekday

_EXPECTED_SPECIALTIES = (
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
)


def test_specialty_has_exactly_ten_members() -> None:
    assert len(Specialty) == 10


def test_specialty_values_are_the_display_names() -> None:
    assert {s.value for s in Specialty} == set(_EXPECTED_SPECIALTIES)


def test_specialty_sorted_by_value_matches_the_contract_order() -> None:
    assert sorted(s.value for s in Specialty) == list(_EXPECTED_SPECIALTIES)


def test_specialty_behaves_like_a_string() -> None:
    assert Specialty.GENERAL_PRACTICE == "General Practice"


def test_specialty_membership_rejects_a_value_outside_the_list() -> None:
    with pytest.raises(ValueError):
        Specialty("Paediatric dermatology")


def test_weekday_is_monday_zero_through_sunday_six() -> None:
    assert Weekday.MONDAY == 0
    assert Weekday.SUNDAY == 6
    assert [w.value for w in Weekday] == [0, 1, 2, 3, 4, 5, 6]


def test_booking_failure_reason_covers_the_closed_set_of_eight() -> None:
    assert {r.value for r in BookingFailureReason} == {
        "practitioner_busy",
        "patient_busy",
        "outside_schedule",
        "off_grid",
        "in_past",
        "beyond_horizon",
        "practitioner_not_found",
        "patient_not_found",
    }
