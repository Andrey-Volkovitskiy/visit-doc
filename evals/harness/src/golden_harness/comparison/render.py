"""The human-readable summary, in the order the contract fixes it.

The order is part of the contract because it is what stops a reader drawing the wrong
conclusion from a real number: the conditions and the coverage restriction come before
any metric, and a moved exclusion count explains a moved denominator before a metric
does.

The language is part of it too. Without a band, a movement is described as *moved*, *up*
or *down*; the words *regression*, *better* and *worse* do not appear about a metric,
because without measured noise nothing licenses them (FR-036). A per-case direction is
said in the case's own terms - this request is now answered, and the label says it is
answerable - so a judgement about one case is never read as a verdict on the build. And
`not computed` and `not measured` are rendered as themselves: one means no scorer
produced the metric, the other that its denominator was empty, and neither is a zero.
"""

import json
from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel

from golden_harness.comparison.band import NoiseBand
from golden_harness.comparison.model import (
    CaseMovement,
    Comparison,
    MetricMovement,
    MovementDirection,
    MovementGroup,
    RunSide,
)
from golden_harness.report import METRICS_BY_FAMILY, MetricFamily
from golden_harness.scoring.metric import NOT_MEASURED, Metric

NOT_COMPUTED: Final = "not computed"
NOT_MEASURED_TEXT: Final = "not measured"

# What each direction is called in the output. Neutral by construction: no word here
# claims a movement is more than a movement.
_DIRECTION_TEXT: Final[dict[MovementDirection, str]] = {
    MovementDirection.IMPROVED: "moved towards its target",
    MovementDirection.DEGRADED: "moved away from its target",
    MovementDirection.UNCHANGED: "unchanged",
    MovementDirection.DIRECTIONLESS: "moved, in no direction",
    MovementDirection.NOT_COMPARABLE: "not comparable",
}

# What a case movement's direction means - about that case against its own label.
_CASE_DIRECTION_TEXT: Final[dict[MovementDirection, str]] = {
    MovementDirection.IMPROVED: "improved against its label",
    MovementDirection.DEGRADED: "degraded against its label",
    MovementDirection.DIRECTIONLESS: "changed, in no direction",
}

BAND_STATEMENT: Final = (
    "The band is the spread of five observations of one unchanged build. It is an "
    "observed range, not a confidence interval and not a significance test."
)
NO_BAND_STATEMENT: Final = (
    "No band was supplied: movements are reported, not judged. Nothing here says "
    "whether a movement is larger than this set moves on its own."
)
BAND_NOT_APPLICABLE: Final = (
    "The band was measured under other conditions, so nothing is marked against it."
)


def to_json(record: BaseModel) -> str:
    """Render a comparison or a band as JSON with sorted keys.

    Equal records make equal bytes: the same treatment the report uses, so a diff
    between two stored records is readable rather than a reordering.
    """
    return json.dumps(record.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def render_comparison(comparison: Comparison) -> str:
    """Render the comparison as Markdown, from the record alone.

    Nothing here reads a run directory: the stored comparison is sufficient to
    re-render this summary, which is what makes the machine-readable form the one that
    is kept (FR-025).
    """
    lines = [
        f"# Comparison - {comparison.base.run_id} -> {comparison.new.run_id}",
        "",
        "## Runs",
        "",
        *(_run_line(side, name) for side, name in _sides(comparison)),
        "",
        "## Conditions",
        "",
        *_condition_lines(comparison),
        "",
        "## Coverage",
        "",
        *_coverage_lines(comparison),
        "",
        "## Band",
        "",
        *_band_lines(comparison),
        "",
        "## Alignment and exclusions",
        "",
        *_alignment_lines(comparison),
        "",
        "## Metrics",
        "",
        *_metric_lines(comparison),
        "",
        "## Case movements",
        "",
        *_case_lines(comparison.cases),
        "",
        "## Cases that moved under an unchanged metric",
        "",
        *_churn_lines(comparison),
    ]
    return "\n".join(lines) + "\n"


def _sides(comparison: Comparison) -> list[tuple[RunSide, str]]:
    """Return both runs with the name each is reported under."""
    return [(comparison.base, "Base"), (comparison.new, "New")]


def _run_line(side: RunSide, name: str) -> str:
    """Name one run: its id, where it was read from, when it ran, how complete it is."""
    recorded, selected = len(side.recorded_cases), len(side.selection.case_ids)
    completeness = (
        "complete"
        if side.complete
        else f"incomplete: {recorded} of {selected} selected cases recorded"
    )
    return (
        f"- {name}: {side.run_id} ({side.location}), started "
        f"{side.started_at.isoformat()}, {completeness}"
    )


def _condition_lines(comparison: Comparison) -> list[str]:
    """Render the condition delta, corpus first, or say the conditions matched."""
    if comparison.conditions.identical:
        return [
            "The two runs' conditions matched in every field, corpus hash included."
        ]
    lines = [
        f"- {change.field}: {change.base} -> {change.new}"
        for change in comparison.conditions.changes
    ]
    if comparison.corpus_moved:
        lines.insert(
            0,
            "**The corpus moved**: the two runs answered from different text, so every "
            "retrieval and serving number below was measured against a different "
            "corpus.",
        )
    return lines


def _coverage_lines(comparison: Comparison) -> list[str]:
    """State the restriction and its count before any metric, when there is one."""
    coverage = comparison.coverage
    if not coverage.restricted:
        return [f"Both runs recorded the same {len(coverage.common)} cases."]
    return [
        (
            f"**Restricted**: every metric below is computed over the "
            f"{len(coverage.common)} cases both runs recorded, on both sides - never "
            "quoted from either run's own report."
        ),
        (
            f"- recorded by the baseline only ({len(coverage.base_only)}): "
            f"{_listed(coverage.base_only)}"
        ),
        (
            f"- recorded by the new run only ({len(coverage.new_only)}): "
            f"{_listed(coverage.new_only)}"
        ),
    ]


def _band_lines(comparison: Comparison) -> list[str]:
    """Say which band is being applied, or that none is - never silently neither."""
    if comparison.band_id is None:
        return [NO_BAND_STATEMENT]
    lines = [f"Band: {comparison.band_id}.", BAND_STATEMENT]
    if not comparison.band_applicable:
        lines.append(BAND_NOT_APPLICABLE)
    return lines


def _alignment_lines(comparison: Comparison) -> list[str]:
    """Render both sides' alignment totals and every exclusion count."""
    base, new = comparison.alignment.base, comparison.alignment.new
    lines = [
        "| totals | aligned | unaligned | excluded | labelled |",
        "|---|---|---|---|---|",
        (
            f"| base | {base.aligned} | {base.unaligned} | {base.excluded} "
            f"| {base.labelled} |"
        ),
        f"| new | {new.aligned} | {new.unaligned} | {new.excluded} | {new.labelled} |",
        "",
    ]
    lines.append(
        "Alignment moved, so a denominator below may have moved with it."
        if comparison.alignment.moved
        else "Alignment held on both sides."
    )
    lines.append("")
    if not comparison.exclusions:
        lines.append("Neither run set a case aside.")
        return lines
    lines.extend(
        f"- {reason}: {count.base} -> {count.new}" + (" (moved)" if count.moved else "")
        for reason, count in comparison.exclusions.items()
    )
    return lines


def _metric_lines(comparison: Comparison) -> list[str]:
    """Render every metric, by family, with both sides and the movements behind it."""
    lines: list[str] = []
    for family in METRICS_BY_FAMILY:
        movements = [
            movement for movement in comparison.metrics if movement.family is family
        ]
        if not movements:
            continue
        lines.extend(_family_lines(family, movements, comparison.cases))
    return lines


def _family_lines(
    family: MetricFamily,
    movements: Sequence[MetricMovement],
    cases: Sequence[CaseMovement],
) -> list[str]:
    """Render one family's table, each row naming the cases that moved under it."""
    lines = [
        f"### {family.value}",
        "",
        "| metric | base | new | movement | cases |",
        "|---|---|---|---|---|",
    ]
    for movement in movements:
        moved_by = {_where(case) for case in cases if movement.name in case.affects}
        lines.append(
            f"| {movement.name} | {_value(movement.base)} | {_value(movement.new)} "
            f"| {_movement_text(movement)} | {_listed(sorted(moved_by))} |"
        )
    lines.append("")
    return lines


def _where(movement: CaseMovement) -> str:
    """Name what a movement is about: its case, and its request where it has one.

    The position is part of the name here for the same reason it is in the movement
    list: a metric computed per request is answerable per request, and "G802" alone
    leaves a reader of a two-request case to guess which one moved (FR-017).
    """
    if movement.position is None:
        return movement.case_id
    return f"{movement.case_id} [{movement.position}]"


def _value(metric: Metric | None) -> str:
    """Render one side of a metric: its value, and the fraction behind it."""
    if metric is None:
        return NOT_COMPUTED
    if metric.value == NOT_MEASURED:
        return f"{NOT_MEASURED} ({NOT_MEASURED_TEXT}: empty denominator)"
    numerator = (
        f"{metric.numerator:.3f}"
        if isinstance(metric.numerator, float)
        else str(metric.numerator)
    )
    return f"{float(metric.value):.3f} ({numerator} / {metric.denominator})"


def _movement_text(movement: MetricMovement) -> str:
    """Say what a metric did, and mark it against the band only where one applies."""
    if movement.direction is MovementDirection.NOT_COMPARABLE:
        return _DIRECTION_TEXT[movement.direction]
    parts = [_DIRECTION_TEXT[movement.direction]]
    if movement.delta is not None and movement.delta != 0:
        parts.append(
            f"{'up' if movement.delta > 0 else 'down'} {abs(movement.delta):.3f}"
        )
    if movement.denominator_moved:
        parts.append("denominator moved")
    if movement.band is not None:
        where = "within" if movement.band.inside else "outside"
        parts.append(
            f"{where} the observed range of five runs "
            f"[{movement.band.low:.3f}, {movement.band.high:.3f}]"
        )
    return "; ".join(parts)


def _case_lines(cases: Sequence[CaseMovement]) -> list[str]:
    """Render the movements, grouped by what changed, each with both states."""
    if not cases:
        return ["No case moved."]
    lines: list[str] = []
    for group in MovementGroup:
        movements = [case for case in cases if case.group is group]
        if not movements:
            continue
        lines.extend([f"### {group.value} ({len(movements)})", ""])
        lines.extend(_case_line(movement) for movement in movements)
        lines.append("")
    return lines


def _case_line(movement: CaseMovement) -> str:
    """Render one movement: both states, what it says about that case, its metrics."""
    line = (
        f"- {_where(movement)}: {movement.base} -> {movement.new} "
        f"({_CASE_DIRECTION_TEXT[movement.direction]}"
    )
    if movement.direction is not MovementDirection.DIRECTIONLESS:
        line += _labelled_as(movement)
    line += ")"
    line += _set_aside_as(movement)
    if movement.varies_on_its_own:
        line += " - this case varied on an unchanged build"
    if movement.affects:
        line += f" - affects {', '.join(movement.affects)}"
    if movement.question is not None:
        line += f' - "{movement.question}"'
    return line


def _set_aside_as(movement: CaseMovement) -> str:
    """Name the exclusion a movement sits beside, where a run recorded one.

    A verdict keeps the direction its label gives it on a case a run set aside - the
    patient did get a different reply, whatever a scorer counted - and this clause is
    what stops that direction being read as a metric that moved. It claims nothing
    about which metrics the exclusion cost; `affects`, printed beside it, names the
    ones that actually changed.

    The exclusion group is silent here because its own two states are those two
    reasons: the clause would print the same fact twice on one line.
    """
    if movement.group is MovementGroup.EXCLUSION:
        return ""
    was, is_now = movement.base_excluded, movement.new_excluded
    if was is None and is_now is None:
        return ""
    if was is not None and is_now is not None:
        reasons = was.value if was is is_now else f"{was.value} -> {is_now.value}"
        return f" - the case was set aside in both runs ({reasons})"
    if is_now is not None:
        return f" - the case was set aside in the new run ({is_now.value})"
    assert was is not None
    return f" - the case was set aside in the baseline ({was.value})"


def _labelled_as(movement: CaseMovement) -> str:
    """Name what the label asks of a directed movement, so the judgement is bounded.

    The movement carries the words: a request labelled a gap must not be described as
    labelled answerable, which is what naming the group rather than reading the label
    used to do.
    """
    if movement.labelled is None:
        return ""
    return f"; labelled {movement.labelled}"


def _churn_lines(comparison: Comparison) -> list[str]:
    """List the cases that moved under a metric whose value did not (FR-018)."""
    unchanged = {
        movement.name
        for movement in comparison.metrics
        if movement.direction is MovementDirection.UNCHANGED
    }
    churn = [
        (case, sorted(set(case.affects) & unchanged))
        for case in comparison.cases
        if set(case.affects) & unchanged
    ]
    if not churn:
        return ["None: every case that moved moved a metric with it."]
    return [
        f"- {_where(case)}: {case.base} -> {case.new}, under {', '.join(names)}"
        for case, names in churn
    ]


def _listed(values: Sequence[str]) -> str:
    """Render a list of ids, or say there are none."""
    return ", ".join(values) if values else "none"


def render_band(band: NoiseBand) -> str:
    """Render a band as Markdown: what it observed, and what it does not claim.

    The statement about what five observations are worth is printed with the numbers
    rather than beneath them, because the numbers are what a reader quotes (FR-032).
    """
    lines = [
        f"# Noise band {band.band_id}",
        "",
        BAND_STATEMENT,
        "",
        NOT_A_THRESHOLD,
        "",
        "## Measured from",
        "",
        f"- Runs, in the order taken: {', '.join(band.run_ids)}",
        f"- Cases: {len(band.case_ids)}",
        f"- Corpus: {band.corpus_sha256}",
        f"- Clock: {band.clock.isoformat()}",
        f"- Measured at: {band.measured_at.isoformat()}",
        *(f"- {name}: {value}" for name, value in band.conditions.model_dump().items()),
        "",
        "## Observed range per metric",
        "",
        "| metric | low | high | the five values |",
        "|---|---|---|---|",
        *(
            f"| {observation.name} | {observation.low:.3f} | {observation.high:.3f} "
            f"| {', '.join(f'{value:.3f}' for value in observation.values)} |"
            for observation in band.metrics.values()
        ),
        "",
        "## Cases that varied on an unchanged build",
        "",
        *_variation_lines(band),
    ]
    return "\n".join(lines) + "\n"


NOT_A_THRESHOLD: Final = (
    "This band is evidence of how much these numbers move on their own. It is not a "
    "threshold: no run is required to beat it, and nothing here says what a run should "
    "score."
)


def _variation_lines(band: NoiseBand) -> list[str]:
    """List each varying case with how many of the runs produced each state."""
    if not band.varying_cases:
        return ["None: every case produced the same outcome in all five runs."]
    total = len(band.run_ids)
    return [
        f"- {variation.name} ({variation.group.value}): "
        + ", ".join(
            f"{state} in {count} of {total}"
            for state, count in sorted(variation.outcomes.items())
        )
        for variation in band.varying_cases
    ]
