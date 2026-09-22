"""The per-turn sampling decision: whether this turn's spans are recorded at all.

A turn sent `X-VisitDoc-Trace: off` must export nothing while the turn beside it, in the
same process, exports everything. The decision is a flag in the turn's own context,
set once before the turn's task is created: asyncio copies the context into the task,
so every span the turn opens - on any task it spawns, or on an executor thread - sees
the flag, and nothing outside the turn does.
"""

from collections.abc import Sequence
from contextvars import ContextVar

from opentelemetry.context import Context
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_ON,
    Decision,
    ParentBased,
    Sampler,
    SamplingResult,
)
from opentelemetry.trace import Link, SpanKind
from opentelemetry.trace.span import TraceState
from opentelemetry.util.types import Attributes

# True for the duration of a turn whose request asked not to be traced. Set in exactly
# one place - the turn's stream, before its task is created - so it can neither miss a
# span of that turn nor leak into another one.
UNTRACED: ContextVar[bool] = ContextVar("visitdoc_untraced", default=False)


class UntracedTurnSampler(Sampler):
    """Drop every span of an untraced turn, else sample as `ParentBased(ALWAYS_ON)`.

    The flag is consulted before the parent: a turn's root is opened under a seeded
    trace id, which the SDK presents as a remote parent already marked sampled, so a
    parent-first sampler would record exactly the turn it was asked to drop.
    """

    def __init__(self) -> None:
        """Delegate every span outside an untraced turn to the default sampler."""
        self._delegate = ParentBased(ALWAYS_ON)

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: SpanKind | None = None,
        attributes: Attributes = None,
        links: Sequence[Link] | None = None,
        trace_state: TraceState | None = None,
    ) -> SamplingResult:
        """Return `DROP` inside an untraced turn, else the delegate's decision."""
        if UNTRACED.get():
            return SamplingResult(Decision.DROP)
        return self._delegate.should_sample(
            parent_context, trace_id, name, kind, attributes, links, trace_state
        )

    def get_description(self) -> str:
        """Name this sampler and the default it delegates to."""
        return "UntracedTurnSampler{ParentBased{root:AlwaysOnSampler}}"
