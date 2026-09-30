"""What a stored run's model calls spent, by the call site that made them.

Read from the `model.usage` entries the chat service logs once for every model call
that returned, and that the harness stores with each case's turn events - the first
turn's and the scripted reply's. So a run recorded before the service logged them
reports no spend rather than zero, and a call that raised before it returned is in no
entry. Only the attempt a case records is counted, never one abandoned for a retry.

A call site is the call's observation name with its iteration suffix removed, so the
booking loop's `handle_booking.model[1]`, `[2]`, ... are one site whose calls outnumber
its turns. Each site is priced at its model's list price (`cost.prices`); a call whose
model has no price, or that did not report what it read from and wrote to the cache,
leaves its site and the run's total unpriced rather than cheaper than it was.
"""

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from golden_harness.cost.prices import PRICES
from golden_harness.record import CaseRun, read_case, recorded_case_ids

USAGE_EVENT: Final = "model.usage"
_ITERATION_SUFFIX: Final = re.compile(r"\[\d+\]$")
_RATE_PLACES: Final = 1
_MONEY_PLACES: Final = Decimal("0.0001")


class UsageContractError(ValueError):
    """A stored `model.usage` entry is not what the log contract describes.

    Names the case. Raised rather than skipped: an entry left out would report the run
    as cheaper than it was.
    """


class _Counts(BaseModel):
    """One call's token counts, keyed as the chat service logs them."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    input: int = Field(ge=0)
    output: int = Field(ge=0)
    cache_read: int | None = Field(default=None, ge=0)
    cache_write: int | None = Field(default=None, ge=0)
    cache_write_1h: int | None = Field(default=None, ge=0)
    thinking: int | None = Field(default=None, ge=0)


class _UsageEntry(BaseModel):
    """One `model.usage` log entry: a model call that returned, and what it spent."""

    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    event: Literal["model.usage"]
    turn_id: str
    call: str
    model: str
    usage: _Counts


@dataclass
class CallSiteCost:
    """What one call site's calls spent across a run.

    `thinking` is None when no call reported it, and is counted over the calls that
    did, whose output is `thinking_of_output`. `cost` is None when any call went
    unpriced; `unpriced_calls` says how many.
    """

    call_site: str
    model: str
    turns: set[str] = field(default_factory=set)
    calls: int = 0
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cache_write_1h: int = 0
    calls_reading_cache: int = 0
    calls_missing_cache_counts: int = 0
    thinking: int | None = None
    thinking_of_output: int = 0
    unpriced_calls: int = 0
    cost: Decimal | None = Decimal(0)

    @property
    def call_hit_rate(self) -> float | None:
        """Return the share of calls that read anything from the cache.

        None when a call did not report its cache counts.
        """
        if self.calls_missing_cache_counts or not self.calls:
            return None
        return self.calls_reading_cache / self.calls

    @property
    def token_hit_rate(self) -> float | None:
        """Return the share of input tokens served from the cache.

        None when a call did not report its cache counts, or nothing was sent.
        """
        sent = self.input + self.cache_read + self.cache_write
        if self.calls_missing_cache_counts or not sent:
            return None
        return self.cache_read / sent

    @property
    def thinking_share(self) -> float | None:
        """Return the share of output spent thinking, over calls that reported it."""
        if self.thinking is None or not self.thinking_of_output:
            return None
        return self.thinking / self.thinking_of_output

    def add(self, entry: _UsageEntry) -> None:
        """Count one call into this site."""
        counts = entry.usage
        self.turns.add(entry.turn_id)
        self.calls += 1
        self.input += counts.input
        self.output += counts.output
        self.cache_read += counts.cache_read or 0
        self.cache_write += counts.cache_write or 0
        self.cache_write_1h += counts.cache_write_1h or 0
        if counts.cache_read is None or counts.cache_write is None:
            self.calls_missing_cache_counts += 1
        if counts.cache_read:
            self.calls_reading_cache += 1
        if counts.thinking is not None:
            self.thinking = (self.thinking or 0) + counts.thinking
            self.thinking_of_output += counts.output
        call_cost = _priced(entry)
        if call_cost is None:
            self.unpriced_calls += 1
            self.cost = None
        elif self.cost is not None:
            self.cost += call_cost


@dataclass(frozen=True)
class CostReport:
    """What a run's model calls spent, one entry per call site, sorted by name.

    `cost` is None when any site's is.
    """

    run: str
    cases: int
    cases_with_usage: int
    sites: list[CallSiteCost]

    @property
    def cost(self) -> Decimal | None:
        """Return the run's total cost, or None when any call went unpriced."""
        total = Decimal(0)
        for site in self.sites:
            if site.cost is None:
                return None
            total += site.cost
        return total


def cost_report(run_dir: Path) -> CostReport:
    """Read every case `run_dir` recorded and total what its model calls spent.

    Raises: UsageContractError when a stored usage entry does not parse.
    """
    sites: dict[tuple[str, str], CallSiteCost] = {}
    case_ids = recorded_case_ids(run_dir)
    cases_with_usage = 0
    for case_id in case_ids:
        entries = list(_usage_entries(read_case(run_dir, case_id)))
        if entries:
            cases_with_usage += 1
        for entry in entries:
            site = call_site(entry.call)
            key = (site, entry.model)
            sites.setdefault(key, CallSiteCost(call_site=site, model=entry.model)).add(
                entry
            )
    return CostReport(
        run=run_dir.name,
        cases=len(case_ids),
        cases_with_usage=cases_with_usage,
        sites=[sites[key] for key in sorted(sites)],
    )


def call_site(call: str) -> str:
    """Return the call site a logged call belongs to: its name without an iteration."""
    return _ITERATION_SUFFIX.sub("", call)


def render_cost(report: CostReport) -> str:
    """Render a cost report as Markdown: one row per call site, then the total."""
    lines = [
        f"# Model spend - run {report.run}",
        "",
        (
            f"Cases recorded: {report.cases}; with a logged model call: "
            f"{report.cases_with_usage}."
        ),
        "",
    ]
    if not report.sites:
        lines.append(
            "No model call was logged. A run recorded before the chat service logged "
            f"`{USAGE_EVENT}` carries none."
        )
        return "\n".join(lines) + "\n"
    lines += [
        (
            "| Call site | Model | Turns | Calls | Input | Cache read | Cache write "
            "| Output | Thinking | Calls hitting cache | Input from cache | Cost "
            "| Share |"
        ),
        "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    total = report.cost
    for site in report.sites:
        lines.append(
            f"| {site.call_site} | {site.model} | {len(site.turns)} | {site.calls} "
            f"| {site.input:,} | {site.cache_read:,} | {site.cache_write:,} "
            f"| {site.output:,} | {_thinking(site)} "
            f"| {_percent(site.call_hit_rate)} | {_percent(site.token_hit_rate)} "
            f"| {_money(site.cost, site.unpriced_calls)} | {_share(site.cost, total)} |"
        )
    unpriced = sum(site.unpriced_calls for site in report.sites)
    lines.append(
        f"| **Total** | | | {sum(site.calls for site in report.sites)} "
        f"| {sum(site.input for site in report.sites):,} "
        f"| {sum(site.cache_read for site in report.sites):,} "
        f"| {sum(site.cache_write for site in report.sites):,} "
        f"| {sum(site.output for site in report.sites):,} | | | "
        f"| **{_money(total, unpriced)}** | |"
    )
    lines += [
        "",
        (
            "Input is what the cache neither served nor stored; a call's whole "
            "prompt is Input + Cache read + Cache write. Thinking is part of Output."
        ),
    ]
    return "\n".join(lines) + "\n"


def _usage_entries(case_run: CaseRun) -> Iterator[_UsageEntry]:
    """Yield the usage entries of a case's first turn, then of its scripted reply.

    Raises: UsageContractError when one does not parse.
    """
    turns: Sequence[list[dict[str, JsonValue]] | None] = (
        case_run.events,
        case_run.reply_turn.events if case_run.reply_turn is not None else None,
    )
    for events in turns:
        for event in events or []:
            if event.get("event") != USAGE_EVENT:
                continue
            try:
                yield _UsageEntry.model_validate(event)
            except ValidationError as exc:
                raise UsageContractError(
                    f"case {case_run.case_id}: a {USAGE_EVENT} entry does not parse: "
                    f"{exc}"
                ) from exc


def _priced(entry: _UsageEntry) -> Decimal | None:
    """Return one call's cost, or None when its model or its cache counts are unknown.

    A call that wrote to the cache without saying which lifetime it wrote to cannot be
    priced: the two lifetimes cost different amounts.
    """
    price = PRICES.get(entry.model)
    counts = entry.usage
    if price is None or counts.cache_read is None or counts.cache_write is None:
        return None
    if counts.cache_write and counts.cache_write_1h is None:
        return None
    one_hour = counts.cache_write_1h or 0
    return price.cost(
        input=counts.input,
        output=counts.output,
        cache_read=counts.cache_read,
        cache_write_5m=counts.cache_write - one_hour,
        cache_write_1h=one_hour,
    )


def _thinking(site: CallSiteCost) -> str:
    """Render a site's thinking tokens and their share of its output."""
    if site.thinking is None:
        return "not reported"
    return f"{site.thinking:,} ({_percent(site.thinking_share)})"


def _percent(rate: float | None) -> str:
    """Render a rate as a percentage, or a dash when it could not be computed."""
    if rate is None:
        return "-"
    return f"{rate * 100:.{_RATE_PLACES}f}%"


def _money(cost: Decimal | None, unpriced_calls: int) -> str:
    """Render a dollar amount, or how many calls left it unpriced."""
    if cost is None:
        return f"unpriced ({unpriced_calls} calls)"
    return f"${cost.quantize(_MONEY_PLACES)}"


def _share(cost: Decimal | None, total: Decimal | None) -> str:
    """Render a site's share of the run's total cost, when both are priced."""
    if cost is None or total is None or not total:
        return "-"
    return _percent(float(cost / total))
