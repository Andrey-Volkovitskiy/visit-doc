"""The noise band: what it is measured from, what it refuses, and what it may claim.

A band asserts that its five runs differ only by chance, so anything contradicting that
is a refusal rather than a warning (FR-030). What it produces is an observed range and a
frequency count - five values per metric, and per varying case how many of the five
produced each state - and never a standard deviation, a confidence interval or a
threshold (FR-032, FR-038).
"""

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from golden_harness.cases import Case
from golden_harness.comparison.band import BandRefusedError, NoiseBand, build_band
from golden_harness.comparison.compare import compare
from golden_harness.comparison.model import MovementDirection
from golden_harness.comparison.render import BAND_STATEMENT, render_comparison
from golden_harness.scoring.serving import UNSERVED_ANSWERABLE_SHARE

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "runs" / "band"
SCHEMA = Path(__file__).resolve().parents[4] / "evals" / "golden" / "schema.json"

_FIVE = ["run1", "run2", "run3", "run4", "run5"]

type Runs = Callable[[str], Path]


@pytest.fixture(scope="session")
def band_labels() -> list[Case]:
    from golden_harness.cases import load_cases

    return load_cases(FIXTURES / "labels.json", SCHEMA)


@pytest.fixture
def band_run() -> Runs:
    def resolve(name: str) -> Path:
        directory = FIXTURES / name
        assert directory.is_dir(), f"no band fixture {name!r}"
        return directory

    return resolve


@pytest.fixture
def band(band_run: Runs, band_labels: list[Case]) -> NoiseBand:
    return build_band([band_run(name) for name in _FIVE], band_labels)


# --- construction (T024) ---


def test_a_band_records_all_five_values_per_metric_in_run_order(
    band: NoiseBand,
) -> None:
    observed = band.metrics[UNSERVED_ANSWERABLE_SHARE]

    assert observed.values == [0.25, 0.5, 0.0, 0.5, 0.25]
    assert (observed.low, observed.high) == (0.0, 0.5)


def test_a_band_names_the_five_runs_in_the_order_they_were_taken(
    band: NoiseBand, band_run: Runs, band_labels: list[Case]
) -> None:
    from golden_harness.record import read_run

    assert band.run_ids == [read_run(band_run(name)).run_id for name in _FIVE]


def test_a_band_mints_its_own_id_so_two_over_the_same_runs_differ(
    band_run: Runs, band_labels: list[Case]
) -> None:
    runs = [band_run(name) for name in _FIVE]

    first = build_band(runs, band_labels)
    second = build_band(runs, band_labels)

    assert first.band_id != second.band_id
    assert first.metrics == second.metrics


def test_a_band_records_the_conditions_the_corpus_and_the_case_set(
    band: NoiseBand, band_run: Runs
) -> None:
    from golden_harness.record import read_run

    run = read_run(band_run("run1"))

    assert band.conditions == run.conditions
    assert band.corpus_sha256 == run.corpus.live_sha256
    assert band.case_ids == ["G801", "G802", "G803", "G804", "G805", "G806"]


def test_a_metric_that_held_across_all_five_has_a_range_of_one_value(
    band: NoiseBand,
) -> None:
    observed = band.metrics["similarity_hit_at_1"]

    assert observed.low == observed.high
    assert len(observed.values) == 5


def test_a_band_counts_how_many_of_the_five_produced_each_state(
    band: NoiseBand,
) -> None:
    varying = {variation.case_id: variation for variation in band.varying_cases}

    # G805 abstained in two of the five, and G802 answered in one.
    assert sum(varying["G805"].outcomes.values()) == 5
    assert varying["G805"].outcomes["abstained_rerank_floor"] == 2
    assert varying["G805"].outcomes["answered"] == 3
    assert varying["G802"].outcomes["answered"] == 1


def test_a_case_that_never_varied_is_not_listed_as_varying(band: NoiseBand) -> None:
    assert {variation.case_id for variation in band.varying_cases} == {"G802", "G805"}


# --- refusals (T025) ---


@pytest.mark.parametrize("count", [4, 6])
def test_a_band_from_any_count_but_five_is_refused_naming_the_count(
    band_run: Runs, band_labels: list[Case], count: int
) -> None:
    names = [*_FIVE, "other-conditions"][:count] if count > 5 else _FIVE[:count]

    with pytest.raises(BandRefusedError, match=str(count)):
        build_band([band_run(name) for name in names], band_labels)


def test_a_run_that_did_not_drive_the_whole_set_is_refused_naming_it(
    band_run: Runs, band_labels: list[Case]
) -> None:
    runs = [band_run(name) for name in ["run1", "run2", "run3", "run4", "narrowed"]]

    with pytest.raises(BandRefusedError, match=r"narrowed|01K6BANDNARROW"):
        build_band(runs, band_labels)


def test_a_run_that_recorded_fewer_cases_than_it_selected_is_refused(
    band_run: Runs, band_labels: list[Case], tmp_path: Path
) -> None:
    # A run that set out to drive the whole set and stopped partway measures every
    # metric over a different population, so its value beside four full ones would put
    # a population difference inside a range that claims to be run-to-run noise.
    partial = tmp_path / "partial"
    shutil.copytree(band_run("run5"), partial)
    (partial / "cases" / "G806.json").unlink()
    runs = [band_run(name) for name in _FIVE[:4]] + [partial]

    with pytest.raises(BandRefusedError, match="5 of 6") as refused:
        build_band(runs, band_labels)

    assert "01K6BAND5" in str(refused.value)


def test_a_differing_condition_is_refused_naming_the_field_and_both_values(
    band_run: Runs, band_labels: list[Case]
) -> None:
    runs = [
        band_run(name) for name in ["run1", "run2", "run3", "run4", "other-conditions"]
    ]

    with pytest.raises(BandRefusedError, match="rerank_floor") as refused:
        build_band(runs, band_labels)

    assert "0.58" in str(refused.value) and "0.52" in str(refused.value)


def test_a_differing_corpus_hash_is_refused_naming_both_hashes(
    band_run: Runs, band_labels: list[Case]
) -> None:
    runs = [band_run(name) for name in ["run1", "run2", "run3", "run4", "other-corpus"]]

    with pytest.raises(BandRefusedError, match="corpus") as refused:
        build_band(runs, band_labels)

    assert "e" * 64 in str(refused.value)


def test_a_differing_label_digest_is_refused_naming_the_cases(
    band_run: Runs, band_labels: list[Case]
) -> None:
    runs = [band_run(name) for name in ["run1", "run2", "run3", "run4", "other-labels"]]

    with pytest.raises(BandRefusedError, match="G802"):
        build_band(runs, band_labels)


def test_a_differing_case_set_is_refused_naming_the_cases_not_common_to_all(
    band_run: Runs, band_labels: list[Case]
) -> None:
    runs = [band_run(name) for name in ["run1", "run2", "run3", "run4", "other-cases"]]

    with pytest.raises(BandRefusedError, match="G806"):
        build_band(runs, band_labels)


# --- application (T026) ---


def test_a_movement_inside_the_observed_range_is_marked_inside(
    band: NoiseBand, band_run: Runs, band_labels: list[Case]
) -> None:
    comparison = compare(band_run("run1"), band_run("run2"), band_labels, band=band)
    marked = {
        movement.name: movement.band
        for movement in comparison.metrics
        if movement.band is not None
    }

    assert comparison.band_applicable
    assert comparison.band_id == band.band_id
    assert marked[UNSERVED_ANSWERABLE_SHARE] is not None
    assert marked[UNSERVED_ANSWERABLE_SHARE].inside
    assert all(verdict.inside for verdict in marked.values() if verdict is not None)


def test_a_movement_outside_the_observed_range_is_marked_outside(
    band: NoiseBand, band_run: Runs, band_labels: list[Case], tmp_path: Path
) -> None:
    narrow = band.model_copy(
        update={
            "metrics": {
                **band.metrics,
                UNSERVED_ANSWERABLE_SHARE: band.metrics[
                    UNSERVED_ANSWERABLE_SHARE
                ].model_copy(update={"values": [0.0], "low": 0.0, "high": 0.0}),
            }
        }
    )

    comparison = compare(band_run("run1"), band_run("run2"), band_labels, band=narrow)

    (movement,) = [
        movement
        for movement in comparison.metrics
        if movement.name == UNSERVED_ANSWERABLE_SHARE
    ]
    assert movement.band is not None and not movement.band.inside


def test_a_band_measured_under_other_conditions_marks_nothing_and_says_so(
    band: NoiseBand, band_run: Runs, band_labels: list[Case]
) -> None:
    elsewhere = band.model_copy(
        update={"conditions": band.conditions.model_copy(update={"rerank_floor": 0.4})}
    )

    comparison = compare(
        band_run("run1"), band_run("run2"), band_labels, band=elsewhere
    )

    assert comparison.band_id == elsewhere.band_id
    assert not comparison.band_applicable
    assert all(movement.band is None for movement in comparison.metrics)
    assert "measured under other conditions" in render_comparison(comparison)


def test_a_metric_the_band_never_observed_is_reported_unmarked(
    band: NoiseBand, band_run: Runs, band_labels: list[Case]
) -> None:
    without = band.model_copy(
        update={
            "metrics": {
                name: observation
                for name, observation in band.metrics.items()
                if name != UNSERVED_ANSWERABLE_SHARE
            }
        }
    )

    comparison = compare(band_run("run1"), band_run("run2"), band_labels, band=without)

    (movement,) = [
        movement
        for movement in comparison.metrics
        if movement.name == UNSERVED_ANSWERABLE_SHARE
    ]
    assert movement.band is None
    assert movement.direction is not MovementDirection.UNCHANGED


def test_a_case_the_band_saw_vary_is_marked_wherever_it_moved(
    band: NoiseBand, band_run: Runs, band_labels: list[Case]
) -> None:
    comparison = compare(band_run("run1"), band_run("run2"), band_labels, band=band)

    moved = {movement.case_id: movement for movement in comparison.cases}

    assert moved["G805"].varies_on_its_own
    assert "varied on an unchanged build" in render_comparison(comparison)


def test_every_rendering_citing_a_band_says_it_is_five_observations(
    band: NoiseBand, band_run: Runs, band_labels: list[Case]
) -> None:
    summary = render_comparison(
        compare(band_run("run1"), band_run("run2"), band_labels, band=band)
    )

    assert BAND_STATEMENT in summary
    assert "five observations" in BAND_STATEMENT
    assert "not a confidence interval" in BAND_STATEMENT


def test_a_band_carries_no_threshold_and_no_target(band: NoiseBand) -> None:
    stored = band.model_dump(mode="json")

    assert not {"threshold", "target", "pass", "verdict"} & set(stored)
    for observation in stored["metrics"].values():
        assert set(observation) == {"name", "values", "low", "high"}
