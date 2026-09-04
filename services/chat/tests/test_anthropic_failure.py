"""Tests for `classify_failure`: which Anthropic failures are an outage, and which are
not. One decider serves both sites that can raise `critical.dependency_unreachable`
for `dependency="anthropic_api"` - the classifier's failure handler and the turn's
`generation` pipeline step - so these are the invariants both of them inherit.
"""

import httpx
import pytest
from anthropic import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    OverloadedError,
    RateLimitError,
)
from chat.agent.classify_intent import ClassificationFailedError
from chat.clients.anthropic_failure import AnthropicFailure, classify_failure

_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _status(cls: type[Exception], code: int) -> Exception:
    return cls(  # type: ignore[call-arg]
        "boom", response=httpx.Response(code, request=_REQUEST), body=None
    )


def test_a_connection_that_reached_nothing_is_an_outage() -> None:
    assert (
        classify_failure(APIConnectionError(request=_REQUEST))
        is AnthropicFailure.UNREACHABLE
    )


@pytest.mark.parametrize(
    ("cls", "code"),
    [(InternalServerError, 500), (OverloadedError, 529)],
)
def test_a_5xx_is_the_api_saying_it_is_not_serving(
    cls: type[Exception], code: int
) -> None:
    # 529 has its own SDK class that does *not* subclass `InternalServerError`, which
    # is why this is tested by status code rather than against a tuple of classes.
    assert classify_failure(_status(cls, code)) is AnthropicFailure.UNREACHABLE


def test_a_timeout_is_its_own_fact_and_not_an_outage() -> None:
    # `APITimeoutError` subclasses `APIConnectionError`, so it has to be tested first
    # or every expired deadline reports as an outage - the reading CLAUDE.md's design
    # principles forbid, since a timeout never proves the server did nothing.
    timed_out = classify_failure(APITimeoutError(request=_REQUEST))
    assert timed_out is AnthropicFailure.TIMED_OUT


@pytest.mark.parametrize(
    ("cls", "code"),
    [(AuthenticationError, 401), (RateLimitError, 429)],
)
def test_a_status_the_api_answered_with_is_not_an_outage(
    cls: type[Exception], code: int
) -> None:
    assert classify_failure(_status(cls, code)) is AnthropicFailure.ANSWERED


def test_anything_that_is_not_an_sdk_error_is_not_an_outage() -> None:
    # The generation step wraps a bare `except Exception`, so this is what a defect in
    # this service's own code arrives as. It must not page an operator for Anthropic.
    assert classify_failure(RuntimeError("boom")) is AnthropicFailure.ANSWERED


# --- the flag survives, or its loss is loud ----------------------------------------


def test_the_failure_a_classification_error_carries_is_not_optional() -> None:
    # `failure` is deliberately not in `args` - `str(exc)` has to stay the message,
    # since that is what the caller logs as `error_detail`. So anything rebuilding the
    # exception from `args` alone must fail loudly rather than default the outage away.
    exc = ClassificationFailedError("boom", AnthropicFailure.UNREACHABLE)

    assert str(exc) == "boom"
    assert exc.dependency_unreachable is True
    with pytest.raises(TypeError):
        exc.__class__(*exc.args)


def test_only_an_unreachable_api_reads_as_a_dependency_outage() -> None:
    for failure in (AnthropicFailure.TIMED_OUT, AnthropicFailure.ANSWERED):
        exc = ClassificationFailedError("boom", failure)
        assert exc.dependency_unreachable is False
