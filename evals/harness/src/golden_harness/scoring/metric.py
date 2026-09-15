"""A metric as published: numerator, denominator, what was set aside, and its value.

A metric with nothing to be computed over is `not_measured`, never 0.0 and never 1.0 -
either number would be a claim about a system nobody observed.
"""

from collections import Counter
from collections.abc import Iterable
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from golden_harness.record import ExclusionReason

NOT_MEASURED: Final = "not_measured"

# The reasons that set a case aside from every metric. `handed_off_turn` is deliberately
# absent: the turn classified before it handed off, so it is scored for classification
# and set aside only by the retrieval and serving scorers.
CASE_SCOPED_REASONS: frozenset[ExclusionReason] = frozenset(
    {
        ExclusionReason.RUN_ERROR,
        ExclusionReason.SILENCED_TURN,
        ExclusionReason.CANCELLED_TURN,
        ExclusionReason.MISSING_LOG_SLICE,
        ExclusionReason.UNRESOLVABLE_FIXTURE,
        ExclusionReason.OUTCOME_UNKNOWN,
    }
)


class Exclusions(BaseModel):
    """How many cases or requests a metric set aside, by reason."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    counts: dict[ExclusionReason, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _counts_are_not_negative(self) -> "Exclusions":
        """Refuse a negative count."""
        negative = [reason.value for reason, n in self.counts.items() if n < 0]
        if negative:
            raise ValueError(f"exclusion counts cannot be negative: {negative}")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> int:
        """Return how many were set aside, for every reason together."""
        return sum(self.counts.values())

    @classmethod
    def tally(cls, reasons: Iterable[ExclusionReason]) -> "Exclusions":
        """Count one exclusion per reason given."""
        return cls(counts=dict(Counter(reasons)))


class Metric(BaseModel):
    """One published metric.

    `numerator` is a count for a share and a sum of reciprocal ranks for a mean rank;
    either way it never exceeds `denominator`, the number of cases or requests the
    metric was computed over.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    numerator: int | float = Field(ge=0)
    denominator: int = Field(ge=0)
    excluded: Exclusions

    @model_validator(mode="after")
    def _numerator_fits_the_denominator(self) -> "Metric":
        """Refuse a numerator larger than the denominator."""
        if self.numerator > self.denominator:
            raise ValueError(
                f"{self.name}: numerator {self.numerator} exceeds "
                f"denominator {self.denominator}"
            )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def value(self) -> float | Literal["not_measured"]:
        """Return numerator over denominator, or `not_measured` over an empty one."""
        if self.denominator == 0:
            return NOT_MEASURED
        return self.numerator / self.denominator
