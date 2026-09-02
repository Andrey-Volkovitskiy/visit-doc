"""The one capability that is not about appointments: fetching a person.

`escalate_to_staff` takes no arguments at all, and that is the contract rather than an
omission (contracts/agent-tools.md):

- **No reason.** A reason exists and is a closed set of three, but the model can only
  ever raise one of them - the other two are decided by a gate and by a failure, neither
  of which runs inside a model turn. A `reason` parameter would be a field with one
  legal value that a model could nonetheless get wrong, and getting it wrong would
  mis-set the conversation's silencing state. The caller identity *is* the reason, so it
  is bound here and never supplied.
- **No summary.** The thread is what says what the patient wanted; a generated summary
  that can be wrong would be a second, less reliable account of it sitting beside the
  real one.

The handler performs no I/O and writes nothing. It records a request into the turn's
collector and returns; `turn.py` applies the transition once the turn has completed
(FR-006), which is why the result is always `ok` - there is no failure this handler can
report.
"""

from typing import Any

from chat.agent.tools.registry import Tool, ToolContext, ToolResult
from chat.domain.models import EscalationReason

# The same closed, empty schema `list_practitioners` uses: the model supplies nothing,
# so it can misstate nothing.
_NO_ARGUMENTS: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_DESCRIPTION = (
    "Hands this conversation to the clinic's staff, who will reply in this same "
    "conversation. Call this when the visitor asks to speak to a person, a human, "
    "staff, or the clinic itself. Do NOT call it because you are unsure of an answer, "
    "because a booking was refused, or because a tool failed - those are handled "
    "elsewhere. After calling it, tell the visitor that a staff member has been "
    "notified and will reply here, and do not promise a response time."
)

_ACKNOWLEDGEMENT = "Staff have been notified and will reply in this conversation."


async def escalate_to_staff(
    context: ToolContext, _arguments: dict[str, Any]
) -> ToolResult:
    """Record that this conversation's visitor asked for a person.

    Always `ok`: it touches no store, so there is no outcome that could be in doubt.
    """
    context.escalation.record(EscalationReason.PATIENT_ASKED_FOR_PERSON)
    return {"status": "ok", "explanation": _ACKNOWLEDGEMENT}


ESCALATE_TO_STAFF = Tool(
    name="escalate_to_staff",
    description=_DESCRIPTION,
    input_schema=_NO_ARGUMENTS,
    handler=escalate_to_staff,
    # FR-002: available in every conversation, including one whose patient record was
    # never created because scheduling was unreachable. Someone with no appointment at
    # all is exactly who needs to reach a person.
    requires_patient=False,
    # It creates nothing the patient cannot undo, and a failure to record it is not an
    # unknown outcome - the conversation is escalated or it is not.
    writes=False,
)

STAFF_TOOLS = [ESCALATE_TO_STAFF]
