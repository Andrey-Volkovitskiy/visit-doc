"""Loading, validating and selecting the golden set, and the digest taken of it."""

import json
from pathlib import Path
from typing import Any

import pytest
from chat.domain.schemas import IntentLabel
from golden_harness.cases import (
    Case,
    LabelError,
    Selection,
    SelectionError,
    SelectionKind,
    label_digests,
    load_cases,
    select,
)

_GOLDEN = Path(__file__).resolve().parents[2] / "golden"
_CASES = _GOLDEN / "cases.json"
_SCHEMA = _GOLDEN / "schema.json"


def _raw_cases() -> list[dict[str, Any]]:
    """The committed cases, flattened out of their families so a test can index them."""
    loaded = json.loads(_CASES.read_text(encoding="utf-8"))
    return [case for family in loaded["families"] for case in family["cases"]]


def _write(tmp_path: Path, raw: list[dict[str, Any]]) -> Path:
    """Write loose cases back as one family, which declares no letter.

    Without a letter the loader does not require the ids to match their family, which
    is what lets these tests keep the committed ids while regrouping them.
    """
    path = tmp_path / "cases.json"
    grouped = {
        "families": [
            {
                "name": "under test",
                "tests": "The committed cases, regrouped.",
                "cases": raw,
            }
        ]
    }
    path.write_text(json.dumps(grouped), encoding="utf-8")
    return path


def _id_of(**shape: Any) -> str:
    """The first committed case matching a shape, by id - never a hardcoded id.

    Picking a representative by what it *is* keeps these tests working across a
    reworked set; a literal id silently stops testing the thing it was chosen for.
    """
    for case in _raw_cases():
        first = case["requests"][0]
        if shape.get("intent") and first["intent"] != shape["intent"]:
            continue
        if (
            shape.get("answerable") is not None
            and first.get("answerable") is not shape["answerable"]
        ):
            continue
        if shape.get("history") and "history" not in case:
            continue
        if shape.get("tools") and not first.get("tools"):
            continue
        return str(case["id"])
    raise AssertionError(f"the committed set holds no case shaped {shape}")


def _digest_of(raw: list[dict[str, Any]], tmp_path: Path, case_id: str) -> str:
    return label_digests(load_cases(_write(tmp_path, raw), _SCHEMA))[case_id]


def test_the_committed_set_loads_with_its_known_counts() -> None:
    cases = load_cases(_CASES, _SCHEMA)

    requests = [request for case in cases for request in case.requests]
    families = json.loads(_CASES.read_text(encoding="utf-8"))["families"]

    # Derived, not restated: what is pinned is that every declared case loads and every
    # family is non-empty, which stays true of a reworked set. A literal count is a
    # number to edit, not a property to hold.
    assert len(cases) == sum(len(family["cases"]) for family in families)
    assert len(cases) == len({case.id for case in cases})
    assert all(family["cases"] for family in families)
    assert len(requests) >= len(cases)
    assert all(family["tests"].strip() for family in families)


def test_every_citation_names_an_entry_the_corpus_pin_holds() -> None:
    """A `cites` id the pin does not carry can never be retrieved.

    The case would then look like a retrieval failure for as long as nobody re-read the
    label, which is the most expensive kind of wrong label: it accuses the system.
    """
    pinned = {
        entry["id"]
        for entry in json.loads((_GOLDEN / "corpus.json").read_text(encoding="utf-8"))[
            "entries"
        ]
    }

    unknown = sorted(
        {
            cited
            for case in load_cases(_CASES, _SCHEMA)
            for request in case.requests
            for cited in (request.cites or [])
            if cited not in pinned
        }
    )

    assert unknown == []


def test_loaded_intents_are_the_chat_services_own_enum() -> None:
    cases = load_cases(_CASES, _SCHEMA)

    assert all(
        isinstance(request.intent, IntentLabel)
        for case in cases
        for request in case.requests
    )


def test_a_malformed_case_fails_validation_naming_its_id(tmp_path: Path) -> None:
    raw = _raw_cases()
    target = raw[41]
    target["requests"][0]["intent"] = "book_something"

    with pytest.raises(LabelError, match=target["id"]):
        load_cases(_write(tmp_path, raw), _SCHEMA)


def test_an_unknown_key_fails_validation_naming_its_id(tmp_path: Path) -> None:
    raw = _raw_cases()
    raw[7]["surprise"] = True

    with pytest.raises(LabelError, match=raw[7]["id"]):
        load_cases(_write(tmp_path, raw), _SCHEMA)


def test_a_duplicated_case_id_fails_as_a_label(tmp_path: Path) -> None:
    raw = _raw_cases()
    raw[3]["id"] = raw[2]["id"]

    with pytest.raises(LabelError, match=raw[2]["id"]):
        load_cases(_write(tmp_path, raw), _SCHEMA)


def test_selecting_by_ids_returns_exactly_those_in_file_order() -> None:
    cases = load_cases(_CASES, _SCHEMA)

    picked = [cases[7].id, cases[0].id, cases[3].id]
    selection = select(cases, ids=picked)

    assert selection.case_ids == [cases[0].id, cases[3].id, cases[7].id]
    assert selection.kind is SelectionKind.IDS


def test_selecting_by_family_returns_exactly_that_family_in_file_order() -> None:
    cases = load_cases(_CASES, _SCHEMA)
    family = cases[0].family
    expected = [case.id for case in cases if case.family == family]

    selection = select(cases, family=family)

    assert selection.case_ids == expected
    assert selection.kind is SelectionKind.FAMILY
    assert selection.family == family


def test_selecting_nothing_in_particular_selects_the_whole_set() -> None:
    cases = load_cases(_CASES, _SCHEMA)

    selection = select(cases)

    assert selection.case_ids == [case.id for case in cases]
    assert selection.kind is SelectionKind.ALL


def test_an_unknown_id_raises_rather_than_selecting_less() -> None:
    cases = load_cases(_CASES, _SCHEMA)

    with pytest.raises(SelectionError, match="G999"):
        select(cases, ids=[cases[0].id, "G999"])


def test_an_unknown_family_raises_rather_than_selecting_nothing() -> None:
    cases = load_cases(_CASES, _SCHEMA)

    with pytest.raises(SelectionError, match="no-such-family"):
        select(cases, family="no-such-family")


def test_an_empty_id_list_raises_rather_than_selecting_nothing() -> None:
    cases = load_cases(_CASES, _SCHEMA)

    with pytest.raises(SelectionError):
        select(cases, ids=[])


def test_ids_and_family_together_are_refused() -> None:
    cases = load_cases(_CASES, _SCHEMA)

    with pytest.raises(SelectionError):
        select(cases, ids=[cases[0].id], family=cases[0].family)


def test_the_schemas_intents_are_the_classifiers_labels() -> None:
    # The drift guard: a ninth intent added to the service and not to the label schema
    # (or the reverse) fails here rather than as an unexplained validation error.
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    cases = schema["properties"]["families"]["items"]["properties"]["cases"]
    request = cases["items"]["properties"]["requests"]["items"]
    labelled = set(request["properties"]["intent"]["enum"])

    produced = {label.value for label in IntentLabel}
    assert labelled == produced - {IntentLabel.CLASSIFICATION_FAILED.value}


def test_there_is_one_sha256_digest_per_case() -> None:
    cases = load_cases(_CASES, _SCHEMA)

    digests = label_digests(cases)

    assert list(digests) == [case.id for case in cases]
    assert all(len(value) == 64 for value in digests.values())
    assert len(set(digests.values())) == len(cases)


@pytest.mark.parametrize(
    ("shape", "edit"),
    [
        ({}, lambda case: case["requests"][0].update(gist="another gist")),
        ({}, lambda case: case.update(note="a corrected note")),
        ({"intent": "small_talk"}, lambda case: case.update(note="a new note")),
    ],
    ids=["gist", "note-replaced", "note-added"],
)
def test_editing_an_unscored_field_leaves_the_digest_unchanged(
    tmp_path: Path, shape: dict[str, Any], edit: Any
) -> None:
    case_id = _id_of(**shape)
    raw = _raw_cases()
    before = _digest_of(raw, tmp_path, case_id)

    edit(next(case for case in raw if case["id"] == case_id))

    assert _digest_of(raw, tmp_path, case_id) == before


def test_renaming_a_family_or_rewording_its_description_moves_no_digest(
    tmp_path: Path,
) -> None:
    """The group describes the cases; it is not part of any one of them.

    `family` is carried onto a case by the loader and excluded from the digest, so a
    regrouped or re-described set still re-scores every run taken against it.
    """
    loaded = json.loads(_CASES.read_text(encoding="utf-8"))
    before = label_digests(load_cases(_CASES, _SCHEMA))

    for group in loaded["families"]:
        group["name"] = f"renamed-{group['name']}"
        group["tests"] = "Reworded."
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(loaded), encoding="utf-8")

    assert label_digests(load_cases(path, _SCHEMA)) == before


def _set_history(case: dict[str, Any]) -> None:
    case["history"] = [{"role": "assistant", "text": "Anything else?"}]


_FAQ = {"intent": "faq_question", "answerable": True}


@pytest.mark.parametrize(
    ("shape", "edit"),
    [
        (
            {"intent": "small_talk"},
            lambda case: case["requests"][0].update(intent="distress"),
        ),
        (_FAQ, lambda case: case.update(message="What do I bring?")),
        (_FAQ, _set_history),
        ({"history": True}, lambda case: case["history"][0].update(text="Else.")),
        (_FAQ, lambda case: case["requests"][0].update(cites=["referral"])),
        (_FAQ, lambda case: case["requests"][0].update(answerable=False, cites=[])),
        (
            {"tools": True},
            # Appended rather than assigned: assigning the tools a case already has
            # would be a no-op, and the test would pass without testing anything.
            lambda case: case["requests"][0]["tools"].append("list_my_appointments"),
        ),
    ],
    ids=[
        "intent",
        "message",
        "history-added",
        "history-text",
        "cites",
        "answerable",
        "tools",
    ],
)
def test_editing_a_scored_field_changes_the_digest(
    tmp_path: Path, shape: dict[str, Any], edit: Any
) -> None:
    case_id = _id_of(**shape)
    raw = _raw_cases()
    before = _digest_of(raw, tmp_path, case_id)

    edit(next(case for case in raw if case["id"] == case_id))

    assert _digest_of(raw, tmp_path, case_id) != before


def test_a_field_the_schema_adds_is_fingerprinted_before_the_model_types_it(
    tmp_path: Path,
) -> None:
    # `Case` keeps a key the schema admits and the model does not type, so a scored
    # field added to the schema cannot change unnoticed under a stored run.
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    cases = schema["properties"]["families"]["items"]["properties"]["cases"]
    cases["items"]["properties"]["expected_reply"] = {"type": "string"}
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    raw = _raw_cases()
    first = raw[0]["id"]
    raw[0]["expected_reply"] = "one"
    before = label_digests(load_cases(_write(tmp_path, raw), schema_path))[first]

    raw[0]["expected_reply"] = "another"

    after = label_digests(load_cases(_write(tmp_path, raw), schema_path))[first]
    assert after != before


def test_a_digest_names_only_its_own_case(tmp_path: Path) -> None:
    raw = _raw_cases()
    before = label_digests(load_cases(_write(tmp_path, raw), _SCHEMA))

    target_id = _id_of(intent="small_talk")
    target = next(case for case in raw if case["id"] == target_id)
    target["requests"][0]["intent"] = "distress"
    after = label_digests(load_cases(_write(tmp_path, raw), _SCHEMA))

    changed = [case_id for case_id in before if before[case_id] != after[case_id]]
    assert changed == [target_id]


def test_the_digest_is_over_canonical_json_so_key_order_does_not_matter(
    tmp_path: Path,
) -> None:
    raw = _raw_cases()
    first = raw[0]["id"]
    before = _digest_of(raw, tmp_path, first)

    raw[0] = dict(reversed(list(raw[0].items())))
    raw[0]["requests"] = [dict(reversed(list(r.items()))) for r in raw[0]["requests"]]

    assert _digest_of(raw, tmp_path, first) == before


def test_a_case_keeps_its_gist_for_a_human_reader() -> None:
    cases = load_cases(_CASES, _SCHEMA)

    case: Case = cases[0]
    # Every request carries one, and none is blank: the gist is a human reference, so
    # what matters is that it is there to read, not what it says.
    assert case.requests[0].gist.strip()
    assert all(r.gist.strip() for c in cases for r in c.requests)


def test_a_selection_names_a_family_exactly_when_chosen_by_family() -> None:
    with pytest.raises(ValueError):
        Selection(kind=SelectionKind.IDS, family="x", case_ids=["G001"])
    with pytest.raises(ValueError):
        Selection(kind=SelectionKind.FAMILY, case_ids=["G001"])
