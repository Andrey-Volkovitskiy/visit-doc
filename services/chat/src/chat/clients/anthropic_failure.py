"""What one Anthropic API failure means, decided in one place.

Two sites raise `critical.dependency_unreachable` for `dependency="anthropic_api"` -
the classifier's own failure handler and the turn's `generation` pipeline step - and
before this module they decided it by different rules, so the same 401 was an outage
on one path and a defect on the other. The decision lives here so there is one answer
to "was the model API unreachable", and adding a caller cannot add a third rule.
"""

from enum import StrEnum

from anthropic import APIConnectionError, APIStatusError, APITimeoutError


class AnthropicFailure(StrEnum):
    """What a failed Anthropic call proves about the API.

    Three members rather than a bool because the three are genuinely different facts,
    and only one of them is an outage. Folding `TIMED_OUT` into either of the others
    is the mistake this enum exists to prevent: reported as `UNREACHABLE` it pages an
    operator for a read that was very likely served, and reported as `ANSWERED` it
    claims this side saw a response it never saw.
    """

    # The request was never served: nothing was reached, or the API answered that it
    # is not serving. This, and only this, is an outage.
    UNREACHABLE = "unreachable"
    # The answer did not arrive before this side's deadline, which says nothing about
    # whether the API served the request - see CLAUDE.md's "a timeout never proves the
    # server did nothing". Not an outage, and not an answer either.
    TIMED_OUT = "timed_out"
    # The API answered, and this side could not use the answer: a schema it rejected,
    # a key it refused, a quota it enforced. This side's defect or this side's limit.
    ANSWERED = "answered"


def classify_failure(exc: BaseException) -> AnthropicFailure:
    """Say what `exc` proves about the API that raised it.

    `APITimeoutError` is tested first because it *subclasses* `APIConnectionError`:
    tested second, every deadline that expired would be reported as an outage, which
    is the reading CLAUDE.md's design principles forbid. httpx maps read, write and
    pool timeouts alike onto it, so this branch also covers a pool timeout, which is
    this service's own connection limit rather than anything about the API.

    Status is tested by *code* rather than against a tuple of exception classes,
    because the SDK gives particular statuses their own classes and adds to that set
    over time: 529 is `OverloadedError`, which subclasses `APIStatusError` but not
    `InternalServerError` - naming classes here missed exactly the status an Anthropic
    outage most often arrives as.
    """
    if isinstance(exc, APITimeoutError):
        return AnthropicFailure.TIMED_OUT
    if isinstance(exc, APIConnectionError):
        return AnthropicFailure.UNREACHABLE
    if isinstance(exc, APIStatusError) and exc.status_code >= 500:
        return AnthropicFailure.UNREACHABLE
    return AnthropicFailure.ANSWERED
