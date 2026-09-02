"""The capability seam: named, schema'd tools the agent can invoke.

The booking node knows tool names and JSON schemas and nothing else - that a
`book_appointment` call becomes a gRPC round trip to another service is entirely the
handler's business. Swapping a handler for a different transport later changes no agent
code.

Nothing here knows what scheduling is; `scheduling_tools.py` supplies the entries.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

import grpc
from anthropic.types import ToolParam

from chat.agent.escalation import EscalationRequests
from chat.core.config import Settings

# What a handler returns: a small JSON-serializable object the model reads back as a
# `tool_result`. Always carries its own `status`, never prose the caller must parse.
ToolResult = dict[str, Any]

# Returned for a tool whose ambient precondition is unmet, in place of running it. Says
# only what is true at this layer - there is no patient to act for - and leaves what
# that means for a booking to the model.
_NO_PATIENT_RESULT: ToolResult = {
    "status": "unavailable",
    "explanation": "This chat has no patient record, so nothing could be done.",
}


@dataclass(frozen=True)
class ToolContext:
    """The ambient facts every handler needs, bound once per turn.

    None of these are tool parameters, and that is the point: a model cannot invent or
    leak a session id it was never shown, and cannot substitute a different "now" than
    the client supplied.

    `patient_id` is None when this chat has no patient record yet, which is the normal
    state of a chat created while scheduling was unreachable.

    `escalation` is this turn's collector of calls to staff. It is ambient for the same
    reason the rest of this is: a model must not be able to hand over a conversation
    other than the one it is in. It always exists, so a handler never has to ask whether
    it may record - a context built without one collects into a throwaway, which is
    what a test wants and what production never does.
    """

    channel: grpc.aio.Channel
    settings: Settings
    session_id: str
    patient_id: str | None
    local_now: datetime
    escalation: EscalationRequests = field(default_factory=EscalationRequests)


@dataclass(frozen=True)
class Tool:
    """One capability: what it is called, what it does, and what it accepts.

    `requires_patient` marks a tool that cannot run for a chat with no patient record
    yet. The registry enforces it, so a tool declares the requirement once instead of
    every handler opening with the same guard - and a new tool that needs a patient
    cannot forget to check.

    `writes` marks a tool whose call can create something the patient cannot undo. It is
    what lets a caller tell "this failed and nothing happened" from "this failed and its
    effect is unknown" without hardcoding a tool name: a read that raised wrote nothing
    by construction, while a write that raised may already have landed.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[ToolContext, dict[str, Any]], Awaitable[ToolResult]]
    requires_patient: bool = False
    writes: bool = False


class UnknownToolError(Exception):
    """Raised when a model asks for a tool the registry does not hold."""


class ToolArgumentError(Exception):
    """Raised when a model's tool arguments cannot be read as its schema declares.

    Deliberately distinct from every other handler failure: it is raised while reading
    the arguments, before the handler has called anything, so the call provably had no
    effect. That is what lets a rejected *write* be reported as having created nothing,
    rather than as an outcome nobody can know.
    """


def required_argument(arguments: dict[str, Any], name: str) -> str:
    """Return `name` from a model-supplied arguments dict, coerced to `str`.

    Raises: ToolArgumentError if `name` is absent or null.

    A value arrives as whatever JSON the model produced, so a schema-declared string
    can still turn up as a number - coerced here rather than trusted.
    """
    value = arguments.get(name)
    if value is None:
        raise ToolArgumentError(f"{name} is required")
    return str(value)


# Every id a model may pass back is one this service handed it: a 26-character
# Crockford base32 ULID. `I`, `L`, `O` and `U` are not in that alphabet.
_ULID_LENGTH = 26
_ULID_ALPHABET = frozenset("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def _is_id(value: str) -> bool:
    """Whether `value` could be an id this service issued."""
    return len(value) == _ULID_LENGTH and set(value) <= _ULID_ALPHABET


def required_id_argument(arguments: dict[str, Any], name: str) -> str:
    """Return `name` as an id the model was actually given, uppercased.

    Raises: ToolArgumentError if `name` is absent, null, or is not shaped like an id
        this service issued.

    The shape is checked here rather than left to the service that owns the id, because
    the two failures are not the same fact and its answer cannot tell them apart. A
    fabricated id comes back as `appointment_not_found` - identical to the appointment
    genuinely not being on the patient's record - and the model relays that to the
    patient as though their real appointment did not exist. Rejected at this boundary
    it stays a `ToolArgumentError`: provably no effect, correctable, and the model gets
    another attempt inside the same turn instead of the patient getting a denial.

    Only the *shape* is knowable here. A well-formed id belonging to someone else is
    the owning service's question, and it answers it with the session predicate on the
    read.

    ULIDs are defined case-insensitively, so a value the model lowercased is the id it
    was given; it is uppercased rather than refused, since the stores compare exactly.
    """
    value = required_argument(arguments, name).upper()
    if not _is_id(value):
        raise ToolArgumentError(
            f"{name} must be an id you read from a tool result in this turn, not "
            f"{value!r}"
        )
    return value


def optional_id_argument(arguments: dict[str, Any], name: str) -> str | None:
    """Return `name` as an id if it was supplied at all, else None.

    Raises: ToolArgumentError if `name` is present but is not shaped like an id.

    Absent and empty both mean "not supplied" - a model that means to omit an optional
    argument sometimes sends `""` instead - and an empty string is not an id either
    way, so the two collapse to one answer rather than one of them becoming a rejection.
    """
    if not arguments.get(name):
        return None
    return required_id_argument(arguments, name)


class ToolRegistry:
    """The tools available for one turn, bound to that turn's ambient context."""

    def __init__(self, tools: list[Tool], context: ToolContext) -> None:
        """Index `tools` by name and hold the context every handler is called with."""
        self._tools = {tool.name: tool for tool in tools}
        self._context = context

    @property
    def names(self) -> list[str]:
        """Return the registered tool names, in registration order."""
        return list(self._tools)

    def to_anthropic_tools(self) -> list[ToolParam]:
        """Render the registry into the shape the Messages API's `tools=` expects.

        Only name, description, and schema cross this boundary - the handler stays on
        this side, which is what keeps the model's view of a capability to its
        contract. Building the provider's request shape is this module's own job, so
        the node calling it never sees a provider type.
        """
        return [
            cast(
                "ToolParam",
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                },
            )
            for tool in self._tools.values()
        ]

    def writes(self, name: str) -> bool:
        """Whether the named tool can create something, for a caller reporting failure.

        An unregistered name is not a write: it never ran, so nothing it might have
        done is in question.
        """
        tool = self._tools.get(name)
        return tool is not None and tool.writes

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Run the named tool with this turn's ambient context.

        Raises:
            UnknownToolError: `name` is not registered.
            ToolArgumentError: propagated from a handler that could not read its
                arguments - raised before that handler calls anything.

        A tool declaring `requires_patient` is answered as unavailable, without being
        run, when the turn has no patient record - the same result its handler would
        have produced, decided in one place instead of in each of them.
        """
        tool = self._tools.get(name)
        if tool is None:
            raise UnknownToolError(name)
        if tool.requires_patient and self._context.patient_id is None:
            return dict(_NO_PATIENT_RESULT)
        return await tool.handler(self._context, arguments)
