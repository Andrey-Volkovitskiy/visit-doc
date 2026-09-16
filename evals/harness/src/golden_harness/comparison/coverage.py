"""What the two runs covered: the cases they share, and each side's completeness.

Both answers come from the case records on disk rather than from either run's
selection, because a selection says what a run set out to drive and the records say
what it has. The difference between them is exactly what makes a run incomplete, which
is reported and never refused (FR-012).
"""

from pathlib import Path

from golden_harness.comparison.model import CaseCoverage, RunSide
from golden_harness.record import Run, recorded_case_ids


def case_coverage(base_dir: Path, new_dir: Path) -> CaseCoverage:
    """Return the cases both runs recorded, and those only one of them did.

    The common set is what every metric is computed over, so it is sorted and used as
    given: two comparisons of the same pair cannot disagree about the population.
    """
    on_base = set(recorded_case_ids(base_dir))
    on_new = set(recorded_case_ids(new_dir))
    base_only = sorted(on_base - on_new)
    new_only = sorted(on_new - on_base)
    return CaseCoverage(
        common=sorted(on_base & on_new),
        base_only=base_only,
        new_only=new_only,
        restricted=bool(base_only or new_only),
    )


def run_side(run_dir: Path, run: Run) -> RunSide:
    """Describe one run as the comparison saw it, including where it was read from.

    `location` is the directory as given, so a baseline under `specs/` is traceable to
    where it lives rather than to a copy someone made of it (FR-026).
    """
    recorded = recorded_case_ids(run_dir)
    return RunSide(
        run_id=run.run_id,
        location=str(run_dir),
        started_at=run.started_at,
        conditions=run.conditions,
        corpus=run.corpus,
        selection=run.selection,
        recorded_cases=recorded,
        complete=set(run.selection.case_ids) <= set(recorded),
    )
