"""The recorded runs every comparison test is driven from.

`fixtures/runs/compare/` holds a small baseline run and one variant per respect a
comparison can report, each the baseline with exactly one edit (spec 013 T010). A test
naming a movement therefore cannot be reading two changes at once.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from golden_harness.cases import Case, load_cases
from golden_harness.record import CaseRun, read_case, recorded_case_ids
from golden_harness.report import Report, score

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "runs" / "compare"
SCHEMA = Path(__file__).resolve().parents[4] / "evals" / "golden" / "schema.json"


@pytest.fixture(scope="session")
def labels() -> list[Case]:
    """The labels every fixture run was taken against."""
    return load_cases(FIXTURES / "labels.json", SCHEMA)


@pytest.fixture
def run_dir() -> Callable[[str], Path]:
    """Resolve a fixture run by name: `run_dir("base")`, `run_dir("verdict")`."""

    def resolve(name: str) -> Path:
        directory = FIXTURES / name
        assert directory.is_dir(), f"no fixture run {name!r} under {FIXTURES}"
        return directory

    return resolve


@pytest.fixture
def scored(labels: list[Case], run_dir: Callable[[str], Path]) -> Callable[..., Report]:
    """Score a fixture run by name, writing nothing into it."""

    def of(name: str, *, only: list[str] | None = None) -> Report:
        return score(run_dir(name), labels, only=only)

    return of


@pytest.fixture
def case_runs(run_dir: Callable[[str], Path]) -> Callable[[str], list[CaseRun]]:
    """Read every case record of a fixture run, in the run's own order."""

    def of(name: str) -> list[CaseRun]:
        directory = run_dir(name)
        return [
            read_case(directory, case_id) for case_id in recorded_case_ids(directory)
        ]

    return of
