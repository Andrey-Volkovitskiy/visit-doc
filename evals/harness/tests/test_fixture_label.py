"""The scheduling fixture label: its schema, the booking-iff-fixture rule, planting."""

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from golden_harness.cases import (
    SEEDED_PRACTITIONER_NAMES,
    Case,
    LabelError,
    SchedulingFixture,
    label_digests,
    load_cases,
    validate_plantable,
)
from golden_harness.driver.run import DEFAULT_CLOCK
from jsonschema import Draft202012Validator

_GOLDEN = Path(__file__).resolve().parents[2] / "golden"
_CASES = _GOLDEN / "cases.json"
_SCHEMA = _GOLDEN / "schema.json"

_BOOKING_CASES = [
    "G042",
    "G044",
    "G048",
    "G049",
    "G050",
    "G051",
    "G053",
    "G062",
    "G087",
    "G088",
    "G089",
    "G090",
    "G091",
    "G092",
    "G093",
    "G094",
    "G095",
    "G102",
]

# The contract's G102 example, verbatim.
_G102: dict[str, Any] = {
    "id": "G102",
    "family": "segmentation-edges",
    "message": "Can you cancel Friday and book Wednesday with William Osler instead?",
    "requests": [
        {"intent": "booking", "gist": "cancel Friday", "tools": ["cancel_appointment"]},
        {
            "intent": "booking",
            "gist": "book Wednesday",
            "tools": ["book_appointment"],
        },
    ],
    "scheduling": {
        "given": [{"practitioner": "William Osler", "day": "+4d", "time": "10:00"}],
        "expect": [
            {
                "practitioner": "William Osler",
                "day": "+4d",
                "time": "10:00",
                "status": "cancelled",
            },
            {"practitioner": "William Osler", "day": "+2d", "status": "standing"},
        ],
        "reply": "Yes, please cancel Friday. The earliest Wednesday time is fine.",
    },
    "source": "010/multi#C9",
}

# The eight cases whose message asks for a write the booking loop confirms first, and
# the patient's scripted answer to that confirmation (FR-037b).
_REPLIES = {
    "G042": "Yes, please go ahead.",
    "G051": "Yes, please go ahead.",
    "G062": "The earliest Wednesday time is fine, yes please book it.",
    "G088": "Yes, please book it.",
    "G090": "Yes, please go ahead.",
    "G093": "Yes, please go ahead.",
    "G095": "The earliest time you have on Friday is fine, yes please book it.",
    "G102": "Yes, please cancel Friday. The earliest Wednesday time is fine.",
}


def _schema_errors(case: dict[str, Any]) -> list[str]:
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    return [error.message for error in Draft202012Validator(schema).iter_errors([case])]


def _g102(**scheduling: Any) -> dict[str, Any]:
    case = copy.deepcopy(_G102)
    case["scheduling"].update(scheduling)
    return case


def _write(tmp_path: Path, raw: list[dict[str, Any]]) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _fixture(given: list[dict[str, Any]]) -> SchedulingFixture:
    expect = [{**entry, "status": "standing"} for entry in given]
    return SchedulingFixture.model_validate({"given": given, "expect": expect})


def _osler(day: str, time: str) -> dict[str, Any]:
    return {"practitioner": "William Osler", "day": day, "time": time}


def _vesalius(day: str, time: str) -> dict[str, Any]:
    return {"practitioner": "Andreas Vesalius", "day": day, "time": time}


# --- the schema ---------------------------------------------------------------------


def test_the_schema_accepts_the_contracts_g102_example_verbatim() -> None:
    assert _schema_errors(_G102) == []


def test_the_schema_rejects_a_status_on_a_given_entry() -> None:
    given = [{**_G102["scheduling"]["given"][0], "status": "standing"}]

    assert _schema_errors(_g102(given=given)) != []


def test_the_schema_rejects_an_expect_entry_without_a_status() -> None:
    expect = [{"practitioner": "William Osler", "day": "+7d"}]

    assert _schema_errors(_g102(expect=expect)) != []


def test_the_schema_rejects_a_given_entry_without_a_time() -> None:
    given = [{"practitioner": "William Osler", "day": "+4d"}]

    assert _schema_errors(_g102(given=given)) != []


@pytest.mark.parametrize("day", ["4d", "+4", "+d", "tomorrow", "+4days", " +4d"])
def test_the_schema_rejects_a_day_not_written_as_a_signed_offset(day: str) -> None:
    given = [{"practitioner": "William Osler", "day": day, "time": "10:00"}]

    assert _schema_errors(_g102(given=given)) != []


# "24:00", "25:99" and "10:60" have the HH:MM shape but name no clock time, so a
# shape-only pattern would pass them on to fail later as a bare ValueError.
@pytest.mark.parametrize(
    "time", ["10", "10:0", "1000", "10:00:00", "ten", "24:00", "25:99", "10:60"]
)
def test_the_schema_rejects_a_time_not_written_as_hh_mm(time: str) -> None:
    expect = [
        {
            "practitioner": "William Osler",
            "day": "+7d",
            "time": time,
            "status": "standing",
        }
    ]

    assert _schema_errors(_g102(expect=expect)) != []


@pytest.mark.parametrize(
    "location", ["scheduling", "given", "expect", "fixture"], ids=lambda x: x
)
def test_the_schema_rejects_an_unknown_key(location: str) -> None:
    case = copy.deepcopy(_G102)
    if location == "scheduling":
        case["scheduling"]["surprise"] = True
    elif location == "given":
        case["scheduling"]["given"][0]["surprise"] = True
    elif location == "expect":
        case["scheduling"]["expect"][0]["surprise"] = True
    else:
        case["scheduling"] = {"given": [], "expect": [], "unchanged": True}

    assert _schema_errors(case) != []


def test_the_schema_accepts_a_fixture_with_no_reply() -> None:
    case = copy.deepcopy(_G102)
    del case["scheduling"]["reply"]

    assert _schema_errors(case) == []


@pytest.mark.parametrize(
    "reply", ["", 5, None, True, ["Yes."], {"text": "Yes."}], ids=repr
)
def test_the_schema_rejects_a_reply_that_is_not_a_non_empty_string(
    reply: object,
) -> None:
    assert _schema_errors(_g102(reply=reply)) != []


def test_the_schema_rejects_a_reply_on_an_appointment_rather_than_the_fixture() -> None:
    given = [{**_G102["scheduling"]["given"][0], "reply": "Yes."}]

    assert _schema_errors(_g102(given=given)) != []


def test_the_schema_rejects_an_unknown_status() -> None:
    expect = [{"practitioner": "William Osler", "day": "+7d", "status": "moved"}]

    assert _schema_errors(_g102(expect=expect)) != []


# --- a fixture exactly on the booking cases -----------------------------------------


def test_a_booking_case_with_no_fixture_is_refused_by_name(tmp_path: Path) -> None:
    case = copy.deepcopy(_G102)
    del case["scheduling"]

    with pytest.raises(LabelError, match="G102"):
        load_cases(_write(tmp_path, [case]), _SCHEMA)


def test_a_fixture_on_a_case_with_no_booking_request_is_refused_by_name(
    tmp_path: Path,
) -> None:
    case = copy.deepcopy(_G102)
    case["id"] = "G103"
    case["requests"] = [{"intent": "small_talk", "gist": "thanks"}]

    with pytest.raises(LabelError, match="G103"):
        load_cases(_write(tmp_path, [case]), _SCHEMA)


def test_a_loaded_fixture_is_typed_on_its_case(tmp_path: Path) -> None:
    (case,) = load_cases(_write(tmp_path, [_G102]), _SCHEMA)

    assert case.scheduling is not None
    assert [entry.practitioner for entry in case.scheduling.given] == ["William Osler"]
    assert [entry.status for entry in case.scheduling.expect] == [
        "cancelled",
        "standing",
    ]
    assert case.scheduling.expect[1].time is None
    assert case.scheduling.reply == _G102["scheduling"]["reply"]


def test_a_loaded_fixture_with_no_reply_carries_none(tmp_path: Path) -> None:
    case = copy.deepcopy(_G102)
    del case["scheduling"]["reply"]

    (loaded,) = load_cases(_write(tmp_path, [case]), _SCHEMA)

    assert loaded.scheduling is not None
    assert loaded.scheduling.reply is None


def test_the_fixture_model_refuses_an_empty_reply() -> None:
    # A reply the model did not declare would be refused too, as an extra key; the
    # match is on the length rule so that refusal cannot pass for this one.
    with pytest.raises(ValueError, match="at least 1 character"):
        SchedulingFixture.model_validate({"given": [], "expect": [], "reply": ""})


def test_a_reply_is_a_scored_field_so_changing_it_changes_the_label_digest() -> None:
    with_reply = Case.model_validate(_G102)
    without = copy.deepcopy(_G102)
    del without["scheduling"]["reply"]
    reworded = _g102(reply="Yes, cancel Friday and book Wednesday.")

    digests = [
        label_digests([Case.model_validate(raw)])["G102"]
        for raw in (_G102, without, reworded)
    ]

    assert label_digests([with_reply])["G102"] == digests[0]
    assert len(set(digests)) == 3


# --- plantability -------------------------------------------------------------------


def test_the_roster_is_the_schedulers_seed_in_seed_order() -> None:
    assert SEEDED_PRACTITIONER_NAMES == ("William Osler", "Andreas Vesalius")


def test_a_given_inside_hours_on_the_grid_after_the_clock_plants() -> None:
    validate_plantable(
        _fixture(
            [_osler("+0d", "09:00"), _osler("+4d", "16:00"), _vesalius("+5d", "13:00")]
        ),
        DEFAULT_CLOCK,
    )


@pytest.mark.parametrize(
    "given",
    [
        _osler("+5d", "10:00"),  # Saturday: Osler works Mon-Fri
        _osler("+1d", "17:00"),  # ends 18:00, past his 17:00 close
        _vesalius("+1d", "14:00"),  # past Vesalius's 14:00 close
        _vesalius("+6d", "10:00"),  # Sunday
        _osler("+1d", "08:00"),  # before opening
    ],
)
def test_a_given_outside_its_practitioners_weekly_range_is_refused(
    given: dict[str, Any],
) -> None:
    with pytest.raises(LabelError, match="outside_schedule"):
        validate_plantable(_fixture([given]), DEFAULT_CLOCK)


@pytest.mark.parametrize("time", ["10:30", "09:15"])
def test_a_given_off_the_sixty_minute_grid_is_refused(time: str) -> None:
    with pytest.raises(LabelError, match="off_grid"):
        validate_plantable(_fixture([_osler("+1d", time)]), DEFAULT_CLOCK)


@pytest.mark.parametrize(
    ("clock", "day"),
    [
        (DEFAULT_CLOCK, "-7d"),
        (datetime(2026, 3, 2, 9, 0), "+0d"),  # exactly the clock is not after it
        (datetime(2026, 3, 2, 9, 30), "+0d"),
    ],
)
def test_a_given_not_strictly_after_the_clock_is_refused(
    clock: datetime, day: str
) -> None:
    with pytest.raises(LabelError, match="in_past"):
        validate_plantable(_fixture([_osler(day, "09:00")]), clock)


def test_a_given_beyond_the_booking_horizon_is_refused() -> None:
    # 2026-03-02 + 91 days is Monday 2026-06-01, past the 90-day horizon.
    with pytest.raises(LabelError, match="beyond_horizon"):
        validate_plantable(_fixture([_osler("+91d", "09:00")]), DEFAULT_CLOCK)


def test_two_given_entries_overlapping_for_one_practitioner_are_refused() -> None:
    fixture = _fixture([_osler("+1d", "10:00"), _osler("+1d", "10:00")])

    with pytest.raises(LabelError, match="overlap"):
        validate_plantable(fixture, DEFAULT_CLOCK)


def test_two_practitioners_at_the_same_time_are_refused_for_the_one_patient() -> None:
    # Every precondition is planted for the case's one patient, and the scheduler's
    # `appointments_patient_no_overlap` refuses the second as PATIENT_BUSY - so this
    # would not plant, and must fail as a label rather than mid-run.
    fixture = _fixture([_osler("+1d", "10:00"), _vesalius("+1d", "10:00")])

    with pytest.raises(LabelError, match="overlap"):
        validate_plantable(fixture, DEFAULT_CLOCK)


def test_two_practitioners_at_separate_times_plant() -> None:
    validate_plantable(
        _fixture([_osler("+1d", "10:00"), _vesalius("+1d", "11:00")]), DEFAULT_CLOCK
    )


def test_a_practitioner_the_seed_does_not_hold_is_refused() -> None:
    given = {"practitioner": "Gregory House", "day": "+1d", "time": "10:00"}

    with pytest.raises(LabelError, match="Gregory House"):
        validate_plantable(_fixture([given]), DEFAULT_CLOCK)


def test_an_expectation_naming_a_practitioner_the_seed_does_not_hold_is_refused() -> (
    None
):
    fixture = SchedulingFixture.model_validate(
        {
            "given": [],
            "expect": [
                {"practitioner": "Gregory House", "day": "+1d", "status": "standing"}
            ],
        }
    )

    with pytest.raises(LabelError, match="Gregory House"):
        validate_plantable(fixture, DEFAULT_CLOCK)


# --- the committed set --------------------------------------------------------------


def test_the_committed_set_has_a_fixture_on_exactly_the_eighteen_booking_cases() -> (
    None
):
    cases = load_cases(_CASES, _SCHEMA)

    assert [case.id for case in cases if case.scheduling is not None] == _BOOKING_CASES


def test_the_committed_set_carries_a_reply_on_exactly_the_eight_confirmed_writes() -> (
    None
):
    cases = load_cases(_CASES, _SCHEMA)

    replies = {
        case.id: case.scheduling.reply
        for case in cases
        if case.scheduling is not None and case.scheduling.reply is not None
    }
    assert replies == _REPLIES


@pytest.mark.parametrize("case_id", _BOOKING_CASES)
def test_every_committed_fixture_plants_against_the_default_clock(case_id: str) -> None:
    (case,) = [c for c in load_cases(_CASES, _SCHEMA) if c.id == case_id]

    assert case.scheduling is not None
    validate_plantable(case.scheduling, DEFAULT_CLOCK)


def test_the_contracts_worked_examples_are_labelled_as_the_contract_states() -> None:
    by_id = {case.id: case for case in load_cases(_CASES, _SCHEMA)}

    def dumped(case_id: str) -> dict[str, Any]:
        fixture = by_id[case_id].scheduling
        assert fixture is not None
        return fixture.model_dump(mode="json", exclude_none=True)

    assert dumped("G049") == {"given": [], "expect": []}
    assert dumped("G053") == {"given": [], "expect": []}
    assert dumped("G102") == _G102["scheduling"]
    g042 = dumped("G042")
    assert [e["day"] for e in g042["given"]] == ["+1d"]
    assert g042["expect"] == [{**g042["given"][0], "status": "cancelled"}]
    g088 = dumped("G088")
    assert g088["given"] == []
    assert [(e["day"], e.get("time"), e["status"]) for e in g088["expect"]] == [
        ("+2d", "09:00", "standing")
    ]
    # G062 names no time, so its reply takes the earliest Wednesday slot: any time.
    assert dumped("G062") == {
        "given": [],
        "expect": [
            {"practitioner": "William Osler", "day": "+2d", "status": "standing"}
        ],
        "reply": "The earliest Wednesday time is fine, yes please book it.",
    }


# Cases whose booking tool needs a practitioner id the message would otherwise leave
# the loop to ask for - `check_availability` and `book_appointment` both take one, and
# the prompt says never to choose for the patient - so each names its practitioner.
_NAMES_ITS_PRACTITIONER = ["G044", "G062", "G088", "G094", "G095", "G102"]


@pytest.mark.parametrize("case_id", _NAMES_ITS_PRACTITIONER)
def test_a_committed_case_that_needs_a_practitioner_names_one(case_id: str) -> None:
    (case,) = [c for c in load_cases(_CASES, _SCHEMA) if c.id == case_id]

    assert "William Osler" in case.message


@pytest.mark.parametrize("case_id", _BOOKING_CASES)
def test_no_committed_booking_case_names_the_run_clocks_own_weekday(
    case_id: str,
) -> None:
    # On a Monday-08:00 clock, "Monday" reads as today or as a week out; a label can
    # only mean one of them, so no booking message or reply may use the clock's weekday.
    (case,) = [c for c in load_cases(_CASES, _SCHEMA) if c.id == case_id]
    assert case.scheduling is not None
    weekday = DEFAULT_CLOCK.strftime("%A")

    assert weekday not in case.message
    assert weekday not in (case.scheduling.reply or "")
