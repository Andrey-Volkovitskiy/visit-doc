import pytest
from shared_models.scheduling import (
    AppointmentStatus,
    BookingFailureReason,
    ChangeFailureReason,
    Specialty,
    StatusFilter,
    TimeFilter,
    Weekday,
)

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


def test_appointment_status_has_exactly_two_members() -> None:
    assert {s.value for s in AppointmentStatus} == {"standing", "cancelled"}


def test_appointment_status_behaves_like_a_string() -> None:
    assert AppointmentStatus.STANDING == "standing"
    assert AppointmentStatus.CANCELLED == "cancelled"


def test_appointment_status_rejects_a_value_outside_the_two() -> None:
    with pytest.raises(ValueError):
        AppointmentStatus("pending")


def test_change_failure_reason_covers_the_closed_set_of_twelve() -> None:
    assert {r.value for r in ChangeFailureReason} == {
        "appointment_not_found",
        "already_cancelled",
        "already_started",
        "stale_confirmation",
        "practitioner_not_found",
        "patient_not_found",
        "in_past",
        "beyond_horizon",
        "outside_schedule",
        "off_grid",
        "practitioner_busy",
        "patient_busy",
    }


def test_change_failure_reason_declares_the_four_first_in_precedence_order() -> None:
    # The declaration order IS the FR-006 precedence: the four eligibility reasons
    # settle whether the appointment can change at all, before any question of where
    # it may go. A resolver that walks the enum must find them first.
    assert [r.value for r in ChangeFailureReason] == [
        "appointment_not_found",
        "already_cancelled",
        "already_started",
        "stale_confirmation",
        "practitioner_not_found",
        "patient_not_found",
        "in_past",
        "beyond_horizon",
        "outside_schedule",
        "off_grid",
        "practitioner_busy",
        "patient_busy",
    ]


@pytest.mark.parametrize("booking_reason", list(BookingFailureReason))
def test_every_booking_reason_appears_in_change_reasons_with_the_same_string(
    booking_reason: BookingFailureReason,
) -> None:
    # Member by member, not set-to-set: this is the mechanical pin that stops the two
    # vocabularies drifting, so a value renamed on one side fails on exactly that
    # member rather than on an opaque set difference (research #10).
    assert ChangeFailureReason(booking_reason.value).value == booking_reason.value


def test_change_failure_reason_adds_exactly_four_beyond_booking() -> None:
    assert {r.value for r in ChangeFailureReason} - {
        r.value for r in BookingFailureReason
    } == {
        "appointment_not_found",
        "already_cancelled",
        "already_started",
        "stale_confirmation",
    }


def test_the_two_filter_axes_are_independent_types() -> None:
    # A filter is not a status: `StatusFilter.BOTH` has no `AppointmentStatus`
    # counterpart, and letting one type serve both would make "what I asked for" and
    # "what state it is in" the same value.
    assert {f.value for f in TimeFilter} == {"future", "past", "both"}
    assert {f.value for f in StatusFilter} == {"standing", "cancelled", "both"}


def test_each_appointment_status_has_a_filter_of_the_same_name() -> None:
    # The tool schema and the wire both key on these strings, so a status renamed
    # without its filter would silently stop matching.
    for status in AppointmentStatus:
        assert StatusFilter(status.value).value == status.value
