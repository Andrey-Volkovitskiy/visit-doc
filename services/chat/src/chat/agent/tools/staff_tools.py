"""The one capability that is not about appointments: fetching a person.

`escalate_to_staff` takes no arguments at all, and that is the contract rather than an
omission (contracts/agent-tools.md):

- **No reason.** Eight reasons exist, but this caller can only ever raise one of them -
  the others are decided by the router, by a gate and by a failure, none of which runs
  inside a model turn. A `reason` parameter would be a field with one legal value that a
  model could nonetheless get wrong, and getting it wrong would mis-mark the message.
  The caller identity *is* the reason, so it is bound here and never supplied.

  That binding is exactly why the description below refuses the neighbouring cases. This
  tool means "the patient asked for a person"; a request the assistant may not serve
  means something else, with a fix of its own. A model calling this for a receipt or a
  letter would record that the patient asked for a human when they did not.
- **No notice of its own.** Calling it does not stop the conversation: the loop carries
  on with whatever else the patient asked, and the fixed sentence telling the patient
  staff have it is appended after the reply by the graph, the same sentence every other
  path that fetches a person sends. The model is told not to write one, so the patient
  does not read it twice in two wordings.
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
    "Forwards this request to the clinic's staff, who will reply in this same "
    "conversation. The conversation continues: carry on with anything else the "
    "visitor asked. Call this only when the visitor explicitly asks to speak to a "
    "person, a human, staff, or the clinic itself. Do NOT call it because you are "
    "unsure of an answer, because a booking was refused, because a tool failed, or "
    "because the visitor asked for something outside what you can do (a receipt, a "
    "letter, a prescription, a billing correction) - every one of those is handled "
    "elsewhere. Do not tell the visitor about the handoff yourself - a notice saying "
    "staff have it is added to your reply for you - and do not promise a response "
    "time."
)

_ACKNOWLEDGEMENT = (
    "Forwarded to staff. A notice telling the visitor so is added after your reply - "
    "do not write one. Carry on with anything else they asked."
)


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
    # unknown outcome - the call is recorded or it is not.
    writes=False,
)

STAFF_TOOLS = [ESCALATE_TO_STAFF]
