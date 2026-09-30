"""What the models a run may call cost, per million tokens.

Anthropic's first-party list prices, read from
https://platform.claude.com/docs/en/about-claude/pricing on 2026-09-30. A model
missing here is not free: its calls are reported unpriced. A report is priced when it
is rendered, not when the run was taken, so re-pricing an old run after a change here
prices it at the new list - update the date above with the prices.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

_PER_TOKEN: Final = Decimal(1_000_000)


@dataclass(frozen=True)
class ModelPrice:
    """One model's list prices, in US dollars per million tokens."""

    input: Decimal
    output: Decimal
    cache_write_5m: Decimal
    cache_write_1h: Decimal
    cache_read: Decimal

    def cost(
        self,
        *,
        input: int,
        output: int,
        cache_read: int,
        cache_write_5m: int,
        cache_write_1h: int,
    ) -> Decimal:
        """Return the dollar cost of the given token counts at these prices."""
        total = (
            input * self.input
            + output * self.output
            + cache_read * self.cache_read
            + cache_write_5m * self.cache_write_5m
            + cache_write_1h * self.cache_write_1h
        )
        return total / _PER_TOKEN


PRICES: Final[dict[str, ModelPrice]] = {
    "claude-sonnet-5": ModelPrice(
        input=Decimal(2),
        output=Decimal(10),
        cache_write_5m=Decimal("2.50"),
        cache_write_1h=Decimal(4),
        cache_read=Decimal("0.20"),
    ),
    "claude-haiku-4-5-20251001": ModelPrice(
        input=Decimal(1),
        output=Decimal(5),
        cache_write_5m=Decimal("1.25"),
        cache_write_1h=Decimal(2),
        cache_read=Decimal("0.10"),
    ),
}
