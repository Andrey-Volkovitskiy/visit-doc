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
    loaded: list[dict[str, Any]] = json.loads(_CASES.read_text(encoding="utf-8"))
    return loaded


def _write(tmp_path: Path, raw: list[dict[str, Any]]) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _digest_of(raw: list[dict[str, Any]], tmp_path: Path, case_id: str) -> str:
    return label_digests(load_cases(_write(tmp_path, raw), _SCHEMA))[case_id]


def test_the_committed_set_loads_with_its_known_counts() -> None:
    cases = load_cases(_CASES, _SCHEMA)

    requests = [request for case in cases for request in case.requests]
    assert len(cases) == 135
    assert len(requests) == 191
    faq = [r for r in requests if r.intent is IntentLabel.FAQ_QUESTION]
    assert len(faq) == 118


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

    selection = select(cases, ids=["G042", "G001", "G034"])

    assert selection.case_ids == ["G001", "G034", "G042"]
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
        select(cases, ids=["G001", "G999"])


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
        select(cases, ids=["G001"], family=cases[0].family)


def test_the_schemas_intents_are_the_classifiers_labels() -> None:
    # The drift guard: a ninth intent added to the service and not to the label schema
    # (or the reverse) fails here rather than as an unexplained validation error.
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    request = schema["items"]["properties"]["requests"]["items"]
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
    ("case_id", "edit"),
    [
        ("G001", lambda case: case["requests"][0].update(gist="another gist")),
        ("G001", lambda case: case.update(note="a corrected note")),
        ("G002", lambda case: case.update(note="a note where there was none")),
        ("G001", lambda case: case.update(source="new")),
        ("G001", lambda case: case.update(family="another-family")),
    ],
)
def test_editing_an_unscored_field_leaves_the_digest_unchanged(
    tmp_path: Path, case_id: str, edit: Any
) -> None:
    raw = _raw_cases()
    before = _digest_of(raw, tmp_path, case_id)

    edit(next(case for case in raw if case["id"] == case_id))

    assert _digest_of(raw, tmp_path, case_id) == before


def _set_history(case: dict[str, Any]) -> None:
    case["history"] = [{"role": "assistant", "text": "Anything else?"}]


@pytest.mark.parametrize(
    ("case_id", "edit"),
    [
        ("G027", lambda case: case["requests"][0].update(intent="distress")),
        ("G001", lambda case: case.update(message="What do I bring?")),
        ("G001", _set_history),
        ("G034", lambda case: case["history"][0].update(text="Something else.")),
        ("G001", lambda case: case["requests"][0].update(cites=["referral"])),
        (
            "G001",
            lambda case: case["requests"][0].update(answerable=False, cites=[]),
        ),
        (
            "G042",
            lambda case: case["requests"][0].update(tools=["list_practitioners"]),
        ),
    ],
)
def test_editing_a_scored_field_changes_the_digest(
    tmp_path: Path, case_id: str, edit: Any
) -> None:
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
    schema["items"]["properties"]["expected_reply"] = {"type": "string"}
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    raw = _raw_cases()
    raw[0]["expected_reply"] = "one"
    before = label_digests(load_cases(_write(tmp_path, raw), schema_path))["G001"]

    raw[0]["expected_reply"] = "another"

    after = label_digests(load_cases(_write(tmp_path, raw), schema_path))["G001"]
    assert after != before


def test_a_digest_names_only_its_own_case(tmp_path: Path) -> None:
    raw = _raw_cases()
    before = label_digests(load_cases(_write(tmp_path, raw), _SCHEMA))

    target = next(case for case in raw if case["id"] == "G027")
    target["requests"][0]["intent"] = "distress"
    after = label_digests(load_cases(_write(tmp_path, raw), _SCHEMA))

    changed = [case_id for case_id in before if before[case_id] != after[case_id]]
    assert changed == ["G027"]


def test_the_digest_is_over_canonical_json_so_key_order_does_not_matter(
    tmp_path: Path,
) -> None:
    raw = _raw_cases()
    before = _digest_of(raw, tmp_path, "G001")

    raw[0] = dict(reversed(list(raw[0].items())))
    raw[0]["requests"] = [dict(reversed(list(r.items()))) for r in raw[0]["requests"]]

    assert _digest_of(raw, tmp_path, "G001") == before


def test_a_case_keeps_its_gist_for_a_human_reader() -> None:
    cases = load_cases(_CASES, _SCHEMA)

    case: Case = cases[0]
    assert case.requests[0].gist == "what to bring"


def test_a_selection_names_a_family_exactly_when_chosen_by_family() -> None:
    with pytest.raises(ValueError):
        Selection(kind=SelectionKind.IDS, family="x", case_ids=["G001"])
    with pytest.raises(ValueError):
        Selection(kind=SelectionKind.FAMILY, case_ids=["G001"])
