"""Tracing, in this service's own terms: turns, steps, generations and tool calls.

The only package allowed to import the tracing SDK. Everything else - the graph's
nodes, retrieval, the tool registry, the turn's route - calls the functions here, so
which provider records a trace, and in what wire format, stays in one place.

Every function is safe to call with nothing installed or with tracing off: the body it
wraps always runs, and the handle it yields accepts every call and records nothing. A
step's outcome is recorded by the wrapper, not by OpenTelemetry, which would record a
cancellation as a success and an exception as an event carrying its raw text:

- completed: level `DEFAULT`
- raised an `Exception`: level `ERROR`, status `<Type>: <redacted message>`
- cancelled: level `WARNING`, status `cancelled`

and the exception is always re-raised unchanged.

What an observation records - its input, its output, its metadata, a turn's trace
metadata - passes the key-name half of the log's redaction rule here, as it is recorded:
a structured value (a dict, a list, a tuple or a pydantic model, at any depth) has the
value under every secret-named key replaced whole, and the configured secret values in
it replaced. A string is recorded as it is, however it reads - a patient message that
happens to be JSON is text, not a structure with keys. Every attribute, strings
included, then passes the export mask's configured-secret-value pass
(`chat.observability.masking`). A model call's usage and sampling parameters are
recorded untouched: their keys are counts and settings (`cache_read_input_tokens`,
`max_tokens`) the key-name rule would take for secrets.
"""

import asyncio
from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from langfuse import (
    Langfuse,
    LangfuseGeneration,
    LangfuseRetriever,
    LangfuseSpan,
    LangfuseTool,
    propagate_attributes,
)
from langfuse.types import TraceContext
from opentelemetry import trace as otel_trace
from pydantic import BaseModel
from shared_logging import LogLevel, SafeLogger, redact_value

from chat.core.logging import get_logger
from chat.observability.client import Tracer
from chat.observability.sampling import UNTRACED

__all__ = [
    "UNTRACED",
    "Generation",
    "Observation",
    "ObservationType",
    "TokenUsage",
    "TraceDirective",
    "TurnOutcome",
    "TurnTrace",
    "generation",
    "install",
    "installed",
    "record",
    "step",
    "tool_call",
    "turn_root",
    "turn_trace",
    "uninstall",
]

# The environment every eval turn is filed under, whatever the service is configured
# with - so an eval run's traces are one filter away from everything else.
EVAL_ENVIRONMENT = "eval"
# The name of every turn's root observation, and of the trace it roots.
_TURN = "turn"
# A model call that stopped here ran out of room rather than finishing.
_TRUNCATED_STOP_REASON = "max_tokens"
_CANCELLED = "cancelled"

_Wrapped = LangfuseSpan | LangfuseGeneration | LangfuseTool | LangfuseRetriever

_LOG_METHODS = {
    LogLevel.DEBUG: SafeLogger.debug,
    LogLevel.INFO: SafeLogger.info,
    LogLevel.WARNING: SafeLogger.warning,
    LogLevel.ERROR: SafeLogger.error,
    LogLevel.CRITICAL: SafeLogger.critical,
}

# The one tracer this process exports through, installed by the lifespan. Read here
# rather than through the SDK's `get_client()`, which hands back a disabled client as
# soon as a process holds more than one - a reloaded dev server, or any test suite.
_active: Tracer | None = None


class ObservationType(StrEnum):
    """What kind of work an observation records, as Langfuse files it."""

    SPAN = "span"
    RETRIEVER = "retriever"
    TOOL = "tool"
    GENERATION = "generation"


class TurnOutcome(StrEnum):
    """How a turn ended, as its trace's root records it."""

    # The pipeline ran to its end, and its reply was stored.
    COMPLETED = "completed"
    # The pipeline raised, and `_settle_the_failure` handled it - which calls a
    # person only when no reply had been delivered yet.
    FAILED = "failed"
    # The turn ended without its reply reaching the thread - superseded by a newer
    # message or a person, or its write declined - and the patient was sent `cancelled`.
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TraceDirective:
    """What one request asked of its turn's trace.

    `traced` False means the turn exports nothing; it can only turn tracing off, never
    on in a service that has it off. The two eval ids are both set, or neither.
    """

    traced: bool = True
    eval_run_id: str | None = None
    eval_case_id: str | None = None

    def environment(self, configured: str) -> str:
        """Return the environment the turn is filed under: `eval` for an eval turn."""
        if self.eval_run_id is not None:
            return EVAL_ENVIRONMENT
        return configured

    def trace_metadata(self, turn_id: str) -> dict[str, str]:
        """Return the metadata every observation of the turn's trace carries."""
        metadata = {"turn_id": turn_id}
        if self.eval_run_id is not None:
            metadata["eval_run_id"] = self.eval_run_id
        if self.eval_case_id is not None:
            metadata["eval_case_id"] = self.eval_case_id
        return metadata


class TokenUsage(Protocol):
    """What a model call reports it spent."""

    @property
    def input_tokens(self) -> int:
        """Tokens the call was sent."""
        ...

    @property
    def output_tokens(self) -> int:
        """Tokens the call generated."""
        ...

    @property
    def cache_read_input_tokens(self) -> int | None:
        """Tokens read from the prompt cache, or None when the provider did not say."""
        ...


class Observation:
    """A handle on one open observation; each call is a no-op if it is not recorded."""

    def __init__(self, wrapped: _Wrapped | None, *, recording: bool) -> None:
        """Hold the SDK's observation, if one was opened and is recorded.

        One that is not recorded is not held: every update to it would be dropped, and
        only after its payload had been copied and redacted for nothing.
        """
        self._wrapped = wrapped if recording else None
        self._recording = recording

    @property
    def recording(self) -> bool:
        """Return whether anything set on this observation will be exported."""
        return self._recording

    def set_output(self, output: Any) -> None:
        """Record `output` as what this observation produced."""
        if self._wrapped is not None:
            self._wrapped.update(output=_recordable(output))

    def set_metadata(self, **fields: Any) -> None:
        """Merge `fields` into this observation's metadata."""
        if self._wrapped is not None:
            self._wrapped.update(metadata=_recordable(fields))

    def warn(self, status_message: str) -> None:
        """Mark this observation degraded but not failed, saying why."""
        if self._wrapped is not None:
            self._wrapped.update(level="WARNING", status_message=status_message)


class Generation(Observation):
    """A handle on one model call's observation."""

    def __init__(self, wrapped: _Wrapped | None, *, recording: bool) -> None:
        """Hold the SDK's observation, with no streamed token seen yet."""
        super().__init__(wrapped, recording=recording)
        self._first_token_at: datetime | None = None

    def mark_token(self) -> None:
        """Note that a streamed call produced a token; the first one's time is kept."""
        if self._first_token_at is None:
            self._first_token_at = datetime.now(UTC)

    def record_completion(
        self,
        output: Any,
        usage: TokenUsage,
        stop_reason: str | None,
        completion_start_time: datetime | None = None,
    ) -> None:
        """Record what the call returned and what it spent.

        Args:
            output: The response content, as the call returned it.
            stop_reason: Why the model stopped; a truncated call is marked a warning.
            completion_start_time: When a streamed call's first token arrived; by
                default, when `mark_token` was first called, if it was.

        A cache count the provider did not report is left out rather than recorded as
        zero: zero would be a claim about the call that nobody measured.
        """
        if self._wrapped is None:
            return
        usage_details = {"input": usage.input_tokens, "output": usage.output_tokens}
        if usage.cache_read_input_tokens is not None:
            usage_details["cache_read_input_tokens"] = usage.cache_read_input_tokens
        self._wrapped.update(
            output=_recordable(output),
            usage_details=usage_details,
            completion_start_time=(
                completion_start_time
                if completion_start_time is not None
                else self._first_token_at
            ),
        )
        if stop_reason == _TRUNCATED_STOP_REASON:
            self.warn(_TRUNCATED_STOP_REASON)


class TurnTrace(Observation):
    """A handle on a turn's root observation, and on the trace it roots."""

    def __init__(
        self, wrapped: _Wrapped | None, *, recording: bool, trace_id: str | None
    ) -> None:
        """Hold the root observation and the id of the trace it is exported under."""
        super().__init__(wrapped, recording=recording)
        self._trace_id = trace_id

    @property
    def trace_id(self) -> str | None:
        """Return the id this turn's trace is exported under, or None if it is not.

        None for a turn that is not recorded: the SDK would still report a well-formed
        id for it, naming a trace that never reaches Langfuse.
        """
        return self._trace_id

    def set_outcome(self, outcome: TurnOutcome) -> None:
        """Record how the turn ended.

        A cancelled turn is also marked `WARNING` with status `cancelled` - the marking
        a `CancelledError` through the root gets - so a turn whose reply was declined
        without anything being raised reads the same as one that was superseded.
        """
        self.set_metadata(turn_outcome=outcome.value)
        if outcome is TurnOutcome.CANCELLED:
            self.warn(_CANCELLED)


# The root of the turn this context belongs to, for the length of the turn - so a
# payload known only deep inside the graph can be put on the trace's top level.
_current_turn: ContextVar[TurnTrace | None] = ContextVar(
    "visitdoc_turn_trace", default=None
)


def turn_root() -> Observation:
    """Return the root observation of the turn this code runs in.

    Outside a turn - or in one nothing records - the handle accepts every call and
    records nothing, so a caller deep inside the graph never has to ask which it is.
    """
    handle = _current_turn.get()
    if handle is None:
        return Observation(None, recording=False)
    return handle


def install(tracer: Tracer) -> None:
    """Make `tracer` the one every observation in this process is recorded through."""
    global _active
    _active = tracer


def uninstall() -> None:
    """Stop recording through the installed tracer; observations become no-ops."""
    global _active
    _active = None


def installed() -> Tracer | None:
    """Return the tracer observations are recorded through, if one is installed."""
    return _active


@contextmanager
def turn_trace(
    turn_id: str,
    *,
    chat_id: str,
    session_id: str,
    directive: TraceDirective,
    input: Any,
) -> Generator[TurnTrace]:
    """Open a turn's root observation, the trace it roots, and its trace attributes.

    Args:
        turn_id: Seeds the trace id, so the trace is findable from any log line of the
            turn without a mapping held anywhere.
        chat_id: The trace's session, so a conversation's turns group together.
        session_id: The trace's user.
        directive: Adds the eval run's ids and environment for an eval turn. Whether
            the turn is traced at all is decided before this is called, not here.
        input: The patient message(s) the turn answers.

    Yields: the root's handle, which says whether the turn is recorded - and so
        whether it has a trace id worth reporting.

    The trace attributes are set before the body runs, since only observations opened
    after them carry them.
    """
    tracer = _active
    if tracer is None or tracer.client is None:
        with _holding_turn(TurnTrace(None, recording=False, trace_id=None)) as handle:
            yield handle
        return
    trace_id = Langfuse.create_trace_id(seed=turn_id)
    with (
        _observed(
            tracer,
            _TURN,
            as_type=ObservationType.SPAN,
            input=input,
            trace_id=trace_id,
        ) as wrapped,
        propagate_attributes(
            session_id=chat_id,
            user_id=session_id,
            trace_name=_TURN,
            environment=directive.environment(tracer.environment),
            metadata=_recordable(directive.trace_metadata(turn_id)),
        ),
    ):
        recording = otel_trace.get_current_span().is_recording()
        handle = TurnTrace(
            wrapped, recording=recording, trace_id=trace_id if recording else None
        )
        with _holding_turn(handle):
            yield handle


@contextmanager
def step(
    name: str,
    *,
    as_type: ObservationType = ObservationType.SPAN,
    input: Any = None,
) -> Generator[Observation]:
    """Open an observation named `name` under the current one, for the body's length."""
    tracer = _active
    if tracer is None or tracer.client is None:
        yield Observation(None, recording=False)
        return
    with _observed(tracer, name, as_type=as_type, input=input) as wrapped:
        yield Observation(
            wrapped, recording=otel_trace.get_current_span().is_recording()
        )


@contextmanager
def generation(
    name: str,
    *,
    model: str,
    input: Any,
    model_parameters: dict[str, Any],
) -> Generator[Generation]:
    """Open a model call's observation under the current one.

    Args:
        input: What the model was sent - the system prompt and the messages, and
            any tool definitions.
        model_parameters: The sampling parameters the call set, e.g. `max_tokens`.
    """
    tracer = _active
    if tracer is None or tracer.client is None:
        yield Generation(None, recording=False)
        return
    with _observed(
        tracer,
        name,
        as_type=ObservationType.GENERATION,
        input=input,
        model=model,
        model_parameters=model_parameters,
    ) as wrapped:
        yield Generation(
            wrapped, recording=otel_trace.get_current_span().is_recording()
        )


@contextmanager
def tool_call(name: str, arguments: Any) -> Generator[Observation]:
    """Open a tool call's observation, `tool:<name>`, with its arguments as input."""
    with step(
        f"tool:{name}", as_type=ObservationType.TOOL, input=arguments
    ) as observed:
        yield observed


def record(
    observation: Observation,
    event: str,
    payload: dict[str, Any],
    *,
    level: LogLevel = LogLevel.INFO,
) -> None:
    """Log `event` with exactly `payload`, and make `payload` the observation's output.

    One object, two sinks: the log line and the trace are given the very same payload,
    so the two cannot come to report different values for one decision.
    """
    _LOG_METHODS[level](get_logger(), event, **payload)
    observation.set_output(payload)


def _recordable(value: Any) -> Any:
    """Return `value` as an observation may record it.

    A structured value comes back as plain dicts and lists with the log's redaction
    rule applied inside it: the value under a secret-named key replaced at any depth,
    and each configured secret value replaced. Anything else - a string above all - is
    returned unchanged, left to the export mask.
    """
    structure = _as_structure(value)
    if not isinstance(structure, dict | list):
        return value
    tracer = _active
    known_secrets = tracer.known_secrets if tracer is not None else []
    return redact_value(structure, known_secrets)


def _as_structure(value: Any) -> Any:
    """Return `value` with each dict, list, tuple and pydantic model made plain.

    A model becomes the dict it serializes to, a tuple a list, and a key a string -
    the shape the SDK exports it in - so the key-name rule sees every key the trace
    will show. Anything else is returned as it is.
    """
    if isinstance(value, BaseModel):
        return _as_structure(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _as_structure(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_as_structure(item) for item in value]
    return value


@contextmanager
def _holding_turn(handle: TurnTrace) -> Generator[TurnTrace]:
    """Make `handle` the current turn's root for the body's length."""
    token = _current_turn.set(handle)
    try:
        yield handle
    finally:
        _current_turn.reset(token)


@contextmanager
def _observed(
    tracer: Tracer,
    name: str,
    *,
    as_type: ObservationType,
    input: Any,
    trace_id: str | None = None,
    model: str | None = None,
    model_parameters: dict[str, Any] | None = None,
) -> Generator[_Wrapped]:
    """Open one SDK observation, recording how the body ended on it.

    Raises: whatever the body raises, unchanged, once the outcome is recorded.

    The body's exception is caught inside the SDK's context and re-raised only once
    that context has closed normally. Let through, OpenTelemetry would record it as a
    span event holding the raw message and stack - out of the export mask's reach -
    and would record a cancellation as nothing at all.
    """
    client = tracer.client
    assert client is not None
    failure: BaseException | None = None
    trace_context: TraceContext | None = (
        {"trace_id": trace_id} if trace_id is not None else None
    )
    with _opened(
        client,
        name,
        as_type=as_type,
        trace_context=trace_context,
        model=model,
        model_parameters=model_parameters,
    ) as wrapped:
        # Set once the sampler has decided, and only if it recorded the observation: an
        # untraced turn's payloads would otherwise be copied and redacted for nothing.
        if input is not None and otel_trace.get_current_span().is_recording():
            wrapped.update(input=_recordable(input))
        try:
            yield wrapped
        except asyncio.CancelledError as cancelled:
            wrapped.update(level="WARNING", status_message=_CANCELLED)
            failure = cancelled
        except Exception as exc:  # noqa: BLE001 - recorded here, re-raised below
            message = redact_value(f"{type(exc).__name__}: {exc}", tracer.known_secrets)
            wrapped.update(level="ERROR", status_message=message)
            failure = exc
    if failure is not None:
        raise failure


def _opened(
    client: Langfuse,
    name: str,
    *,
    as_type: ObservationType,
    trace_context: TraceContext | None,
    model: str | None,
    model_parameters: dict[str, Any] | None,
) -> AbstractContextManager[_Wrapped]:
    """Start the SDK observation of `as_type`, at the default level, as the current one.

    One call per type rather than one call passing the type through: the SDK's return
    type - and whether it accepts a model at all - is decided by which literal it is
    handed.
    """
    match as_type:
        case ObservationType.GENERATION:
            return client.start_as_current_observation(
                trace_context=trace_context,
                name=name,
                as_type="generation",
                level="DEFAULT",
                model=model,
                model_parameters=model_parameters,
            )
        case ObservationType.TOOL:
            return client.start_as_current_observation(
                trace_context=trace_context,
                name=name,
                as_type="tool",
                level="DEFAULT",
            )
        case ObservationType.RETRIEVER:
            return client.start_as_current_observation(
                trace_context=trace_context,
                name=name,
                as_type="retriever",
                level="DEFAULT",
            )
        case ObservationType.SPAN:
            return client.start_as_current_observation(
                trace_context=trace_context,
                name=name,
                as_type="span",
                level="DEFAULT",
            )
