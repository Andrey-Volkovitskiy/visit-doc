"""`classify_intent`: structured-output intent classification."""

from typing import Any

from anthropic import APIConnectionError, APIStatusError, AsyncAnthropic

from chat.agent.history import to_claude_messages
from chat.core.config import get_settings
from chat.domain.models import Message
from chat.domain.schemas import IntentClassificationResult, IntentLabel

_MAX_TOKENS = 256
_SYSTEM_PROMPT = (
    "Classify the visitor's most recent message into every intent that applies, given "
    "the conversation so far: faq_question (a clinic policy/FAQ question), booking "
    "(anything only the clinic's live appointment records can answer - booking, "
    "rescheduling, cancelling, or listing appointments, and equally asking which "
    "practitioners this clinic has, what a named practitioner specializes in, or when "
    "one of them next has a free appointment), "
    "call_staff (an urgent or staff-handled issue, e.g. a billing problem), unknown "
    "(fits none of the above). A message may carry more than one intent at once."
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


def _is_unreachable(exc: BaseException) -> bool:
    """Say whether `exc` means the API never served the request.

    Two shapes do. A connection error reached no answer at all - `APITimeoutError`
    subclasses it, so a deadline that expired is one of these. And a 5xx is the API
    answering that it is not serving, which is an outage however it is worded.

    Every other status is the API reachable and answering: a schema it rejected, a key
    it refused, a quota it enforced. Those are this side's defect or this side's limit,
    not an outage, and an alert that fires for them is one an operator learns to ignore.

    Tested by *status code* rather than against a tuple of exception classes, because
    the SDK gives particular statuses their own classes and adds to that set over time:
    529 is `OverloadedError`, which is checked before the generic 5xx branch and so
    subclasses `InternalServerError` not at all - naming classes here missed exactly
    the status an Anthropic outage most often arrives as.
    """
    if isinstance(exc, APIConnectionError):
        return True
    return isinstance(exc, APIStatusError) and exc.status_code >= 500


class ClassificationFailedError(Exception):
    """Raised when a `classify_intent()` call fails or returns an invalid result.

    `dependency_unreachable` separates the two things that raise this: True when the
    API never served the request, False when it served one this code could not use.
    They are one failure to this function's caller, which falls back either way, and
    two different events to an operator - only the first is an outage.
    """

    def __init__(self, message: str, *, dependency_unreachable: bool = False) -> None:
        """Record why classification failed, and whether the API was the reason."""
        super().__init__(message)
        self.dependency_unreachable = dependency_unreachable


async def classify_intent(
    anthropic_client: AsyncAnthropic, bursts: list[list[Message]]
) -> IntentClassificationResult:
    """Classify the trailing patient message in `bursts`.

    Args:
        bursts: A `bound_to_last_n_turns()`-bounded window over the turn's
            conversation history - never the full chat history.

    Raises: ClassificationFailedError on any API error, timeout, a response that
        fails to validate against the schema, or a validated response that still
        contains `CLASSIFICATION_FAILED` - never returns a result containing it. Its
        `dependency_unreachable` says which of those it was.

    Uses native JSON Outputs (`output_config.format`), not tool-use.
    """
    try:
        response = await anthropic_client.messages.create(
            model=get_settings().CLASSIFICATION_MODEL,
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
        raise ClassificationFailedError(
            str(exc), dependency_unreachable=_is_unreachable(exc)
        ) from exc
