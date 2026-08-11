"""`classify_intent`: structured-output intent classification (research.md #3/#4)."""

from typing import Any

from anthropic import AsyncAnthropic

from chat.agent.history import to_claude_messages
from chat.domain.models import Message
from chat.domain.schemas import IntentClassificationResult, IntentLabel

_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 256
_SYSTEM_PROMPT = (
    "Classify the visitor's most recent message into every intent that applies, given "
    "the conversation so far: faq_question (a clinic policy/FAQ question), booking "
    "(wants to book/reschedule/cancel an appointment), call_staff (an urgent or "
    "staff-handled issue, e.g. a billing problem), unknown (fits none of the above). A "
    "message may carry more than one intent at once."
)

# The classifier's own request schema, built from `IntentClassificationResult`'s
# schema but with `CLASSIFICATION_FAILED` excluded from the `intents` enum - that
# value is assigned only by orchestration code on a failed/invalid call (FR-007), so
# it must be structurally unreachable from the model's own response (research.md #3).
# `additionalProperties: false` is required by the API for any `object`-typed JSON
# Outputs schema - Pydantic's own `model_json_schema()` doesn't set it by default.
_RESPONSE_SCHEMA: dict[str, Any] = IntentClassificationResult.model_json_schema()
_RESPONSE_SCHEMA["$defs"]["IntentLabel"]["enum"] = [
    label.value
    for label in IntentLabel
    if label is not IntentLabel.CLASSIFICATION_FAILED
]
_RESPONSE_SCHEMA["additionalProperties"] = False


class ClassificationFailedError(Exception):
    """Raised when a `classify_intent()` call fails or returns an invalid result."""


async def classify_intent(
    anthropic_client: AsyncAnthropic, bursts: list[list[Message]]
) -> IntentClassificationResult:
    """Classify the trailing patient message in `bursts` (FR-001/FR-002).

    `bursts` is a `bound_to_last_n_turns()`-bounded window (research.md #5) over the
    turn's conversation history - never the full chat history. Translated to Claude's
    `messages` format internally, via `history.py::to_claude_messages()` - this
    function's own implementation detail (it's the one that talks to
    `anthropic_client`), not something the caller should need to do itself. Uses
    native JSON Outputs (`output_config.format`), not tool-use (research.md #3).

    Raises: ClassificationFailedError on any API error, timeout, a response that fails
        to validate against the schema, or a validated response that still contains
        `CLASSIFICATION_FAILED` (FR-007) - never returns a result containing it.
    """
    try:
        response = await anthropic_client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=to_claude_messages(bursts),
            output_config={
                "format": {"type": "json_schema", "schema": _RESPONSE_SCHEMA}
            },
        )
        block = response.content[0]
        if block.type != "text":
            raise ClassificationFailedError(f"unexpected content block: {block.type}")
        result = IntentClassificationResult.model_validate_json(block.text)
        if IntentLabel.CLASSIFICATION_FAILED in result.intents:
            # Defense in depth: `_RESPONSE_SCHEMA`'s enum exclusion (above) is what's
            # actually supposed to make this unreachable, but that's enforced at a
            # private, Pydantic-internals-dependent key path - re-check the parsed
            # result itself so a schema-level regression fails loudly here instead of
            # silently violating this function's own contract (FR-007).
            raise ClassificationFailedError(
                "model returned CLASSIFICATION_FAILED despite schema exclusion"
            )
        return result
    except ClassificationFailedError:
        raise
    except Exception as exc:
        raise ClassificationFailedError(str(exc)) from exc
