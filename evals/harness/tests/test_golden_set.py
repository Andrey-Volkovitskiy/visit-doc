"""The declaration of the golden set, and its agreement with the committed JSON."""

import json
from pathlib import Path
from typing import Any

import pytest
from chat.agent.tools.scheduling_tools import SCHEDULING_TOOLS
from golden_harness.cases import LabelError, load_cases
from golden_harness.golden_set import (
    CASES_JSON,
    EXPECTED_LETTERS,
    PREREQUISITE,
    DeclarationError,
    build,
    problems,
    render,
    write,
)

_SCHEMA = Path(__file__).resolve().parents[2] / "golden" / "schema.json"


def test_the_committed_json_is_what_the_declaration_renders() -> None:
    """`cases.json` is an artifact, so a hand edit to it is a divergence, not a change.

    Byte-for-byte: the declaration is the set, and this is what keeps that sentence
    true rather than aspirational. Edit `golden_set.py` and re-run
    `python -m golden_harness build-set`.
    """
    assert render(build()) == CASES_JSON.read_text(encoding="utf-8")


def test_the_declaration_is_consistent() -> None:
    assert problems() == []


def test_the_declared_families_are_in_the_specified_order() -> None:
    assert [family["letter"] for family in build()["families"]] == EXPECTED_LETTERS


def test_the_declared_set_loads_through_the_schema(tmp_path: Path) -> None:
    # The declaration's own checks and the schema are different guards: this is the one
    # that says the rendered file is loadable, which is all any consumer needs.
    path = tmp_path / "cases.json"
    write(path)

    cases = load_cases(path, _SCHEMA)

    assert [case.id for case in cases] == [
        case.id for case in load_cases(CASES_JSON, _SCHEMA)
    ]


def test_writing_elsewhere_leaves_the_committed_file_alone(tmp_path: Path) -> None:
    before = CASES_JSON.read_bytes()

    write(tmp_path / "somewhere-else.json")

    assert CASES_JSON.read_bytes() == before


# --- the checks the declaration makes that no schema can ----------------------------
#
# Each is driven by breaking a copy of the declared set, because a check that never
# fires reads exactly like a set with nothing wrong in it.


def _with(monkeypatch: pytest.MonkeyPatch, families: list[dict[str, Any]]) -> None:
    monkeypatch.setattr("golden_harness.golden_set.FAMILIES", families)


def test_a_citation_outside_the_corpus_pin_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    families = json.loads(json.dumps(build()["families"]))
    target = next(
        case
        for family in families
        for case in family["cases"]
        if case["requests"][0].get("cites")
    )
    target["requests"][0]["cites"] = ["no-such-entry"]
    _with(monkeypatch, families)

    assert any(
        "no-such-entry" in problem and target["id"] in problem for problem in problems()
    )


def test_a_fixture_outside_its_practitioners_hours_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    families = json.loads(json.dumps(build()["families"]))
    target = next(
        case
        for family in families
        for case in family["cases"]
        if case.get("scheduling", {}).get("expect")
    )
    # 22:00 is outside both seeded practitioners' days, whichever this one names.
    target["scheduling"]["expect"][0]["time"] = "22:00"
    _with(monkeypatch, families)

    assert any(target["id"] in problem and "22:00" in problem for problem in problems())


def test_an_id_disagreeing_with_its_family_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    families = json.loads(json.dumps(build()["families"]))
    families[0]["cases"][0]["id"] = "G-z-99"
    _with(monkeypatch, families)

    assert any("G-z-99" in problem for problem in problems())


def test_build_refuses_an_inconsistent_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    families = json.loads(json.dumps(build()["families"]))
    families[0]["cases"][0]["id"] = "G-z-99"
    _with(monkeypatch, families)

    with pytest.raises(DeclarationError, match="G-z-99"):
        build()


# --- the prerequisite rule ----------------------------------------------------------


def test_a_tools_prerequisite_is_the_tool_that_fetches_the_id_it_needs() -> None:
    """The premise of `PREREQUISITE`, checked against the tools themselves.

    Each dependent tool takes an id it cannot be told in words, and each prerequisite
    takes nothing - which is what makes it the only way to obtain one. If a tool were
    ever changed to accept a practitioner by name, this fails rather than leaving every
    booking label quietly requiring a call the loop no longer has to make.
    """
    required = {
        tool.name: set(tool.input_schema.get("required", ()))
        for tool in SCHEDULING_TOOLS
    }
    id_needed = {"practitioner_id", "appointment_id"}

    for dependent, prerequisite in PREREQUISITE.items():
        assert required[dependent] & id_needed, dependent
        assert required[prerequisite] == set(), prerequisite


def test_every_booking_label_names_the_prerequisite_of_every_tool_it_names() -> None:
    """A label naming a tool without its prerequisite expects an invented id.

    It would then be scored as unserved for a call the loop could never have made, or -
    worse - read as served on a turn that guessed.
    """
    for case in (c for family in build()["families"] for c in family["cases"]):
        for request in case["requests"]:
            named = set(request.get("tools", ()))
            implied = {PREREQUISITE[tool] for tool in named if tool in PREREQUISITE}

            assert implied <= named, f"{case['id']}: missing {sorted(implied - named)}"


def test_bk_adds_a_prerequisite_the_declaration_left_off() -> None:
    from golden_harness.golden_set import bk

    assert bk("x", "check_availability")["tools"] == [
        "check_availability",
        "list_practitioners",
    ]
    # Already named, so not repeated - and the declaration's own order is kept.
    assert bk("x", "list_my_appointments", "cancel_appointment")["tools"] == [
        "list_my_appointments",
        "cancel_appointment",
    ]


# --- a removed case leaves its number behind ----------------------------------------


def test_a_family_may_skip_a_number_a_removed_case_used_to_hold() -> None:
    """The number is the case's identity, not its index.

    Renumbering after a removal would rename labels that had not changed and make every
    stored run that selected them unscoreable, so a hole is the correct outcome.
    """
    numbers = {
        family["letter"]: [
            int(case["id"].rsplit("-", 1)[1]) for case in family["cases"]
        ]
        for family in build()["families"]
    }

    assert all(ns == sorted(ns) for ns in numbers.values())
    assert all(len(ns) == len(set(ns)) for ns in numbers.values())
    # The set carries at least one such hole today, so the rule is exercised by real
    # data rather than only by the synthetic cases below.
    assert any(ns[-1] > len(ns) for ns in numbers.values())


def test_the_loader_accepts_a_family_with_a_hole(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(_one_family(["G-z-01", "G-z-04"])), encoding="utf-8")

    assert [case.id for case in load_cases(path, _SCHEMA)] == ["G-z-01", "G-z-04"]


@pytest.mark.parametrize(
    "ids",
    [["G-z-04", "G-z-01"], ["G-z-01", "G-z-01"], ["G-z-01", "G-y-02"]],
    ids=["descending", "repeated", "another-familys-letter"],
)
def test_the_loader_refuses_ids_that_do_not_ascend_within_their_family(
    tmp_path: Path, ids: list[str]
) -> None:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(_one_family(ids)), encoding="utf-8")

    with pytest.raises((LabelError, ValueError)):
        load_cases(path, _SCHEMA)


def _one_family(ids: list[str]) -> dict[str, Any]:
    """A minimal file: one lettered family holding a case per id."""
    return {
        "families": [
            {
                "letter": "z",
                "name": "under test",
                "tests": "Cases built by this test.",
                "cases": [
                    {
                        "id": case_id,
                        "message": "Are you open?",
                        "requests": [
                            {
                                "intent": "faq_question",
                                "gist": "hours",
                                "answerable": True,
                                "cites": ["hours-location"],
                            }
                        ],
                    }
                    for case_id in ids
                ],
            }
        ]
    }
