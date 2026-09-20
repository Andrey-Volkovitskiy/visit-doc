"""The declaration of the golden set, and its agreement with the committed JSON."""

import json
from pathlib import Path
from typing import Any

import pytest
from golden_harness.cases import load_cases
from golden_harness.golden_set import (
    CASES_JSON,
    EXPECTED_LETTERS,
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
