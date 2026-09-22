"""Masking at export: no configured secret leaves the process inside a span.

Every string attribute of every exported span passes the log's own redaction rule, and
nothing the SDK or OpenTelemetry records outside the attributes - a span event - may
carry one either, since a patch cannot reach an event. The span here puts each secret
everywhere a turn could: its input, output, metadata, a warning's status message, and
the text of an exception raised through it. The last test drives a whole turn, so a
span path the bare step does not take - a tool call, a failing generation, the root -
is held to the same rule.
"""

import json
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic.types import ToolUseBlock
from chat.domain.schemas import IntentLabel
from chat.main import app
from chat.observability import generation, step
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from .conftest import (
    fake_anthropic_client,
    fake_usage,
    finished_spans,
    installed_tracer,
    span_attributes,
    span_output,
    tracing_settings,
    turn,
)

_SECRETS = {
    "ANTHROPIC_API_KEY": "sk-ant-m4sk-t3st-k3y",
    "VOYAGE_API_KEY": "pa-v0yag3-m4sk-t3st",
    "LANGFUSE_SECRET_KEY": "sk-lf-m4sk-t3st-s3cr3t",
    "ADMIN_SECRET": "adm1n-m4sk-t3st",
}
_DATABASE_PASSWORD = "db-p4ss-m4sk-t3st"
_QDRANT_PASSWORD = "qdr4nt-p4ss-m4sk-t3st"
_EVERY_SECRET = (*_SECRETS.values(), _DATABASE_PASSWORD, _QDRANT_PASSWORD)
_UNNAMED_VALUE = "an-unconfigured-v4lu3"

_PATIENT_TEXT = "Can Dr. Adams see me on Friday about my knee?"
_DISPLAY_NAME = "Dr. Adams"


@pytest.fixture
def exporter() -> Iterator[InMemorySpanExporter]:
    """Trace into an in-memory exporter, with every secret-bearing setting set."""
    settings = tracing_settings(
        **_SECRETS,
        DATABASE_URL=f"postgresql+asyncpg://visitdoc:{_DATABASE_PASSWORD}@localhost/db",
        QDRANT_URL=f"http://qdrant:{_QDRANT_PASSWORD}@localhost:6333",
    )
    exporter = InMemorySpanExporter()
    with installed_tracer(settings, exporter):
        yield exporter


def _everything_exported(spans: list[ReadableSpan]) -> str:
    """Render every attribute and every span event of `spans` as one string."""
    parts: list[str] = []
    for span in spans:
        parts.extend(f"{key}={value}" for key, value in (span.attributes or {}).items())
        for event in span.events:
            parts.append(event.name)
            parts.extend(
                f"{key}={value}" for key, value in (event.attributes or {}).items()
            )
    return "\n".join(parts)


def _leak_every_secret() -> None:
    """Open one span carrying every secret in every place a turn can put one."""
    everywhere = " ".join(_EVERY_SECRET)
    with step("leaky", input={"text": _PATIENT_TEXT, "note": everywhere}) as observed:
        observed.set_output({"reply": f"{_DISPLAY_NAME} can. {everywhere}"})
        observed.set_metadata(context=everywhere, api_key=_UNNAMED_VALUE)
        observed.warn(f"degraded: {everywhere}")


def test_no_configured_secret_is_exported_in_any_attribute(
    exporter: InMemorySpanExporter,
) -> None:
    _leak_every_secret()

    exported = _everything_exported(finished_spans(exporter)["leaky"])

    for secret in _EVERY_SECRET:
        assert secret not in exported
    assert "***REDACTED***" in exported


def test_a_value_under_a_secret_named_key_is_exported_redacted(
    exporter: InMemorySpanExporter,
) -> None:
    _leak_every_secret()

    exported = _everything_exported(finished_spans(exporter)["leaky"])

    assert _UNNAMED_VALUE not in exported


def test_an_exception_raised_through_a_span_exports_no_secret_in_attributes_or_events(
    exporter: InMemorySpanExporter,
) -> None:
    everywhere = " ".join(_EVERY_SECRET)

    with pytest.raises(RuntimeError), step("failing"):
        raise RuntimeError(f"upstream refused {everywhere}")

    (span,) = finished_spans(exporter)["failing"]
    exported = _everything_exported([span])
    for secret in _EVERY_SECRET:
        assert secret not in exported
    assert span.status.description is None or not any(
        secret in span.status.description for secret in _EVERY_SECRET
    )


def test_a_secret_named_key_nested_in_an_input_or_output_is_exported_redacted(
    exporter: InMemorySpanExporter,
) -> None:
    with step(
        "nested",
        input={"request": {"api_key": _UNNAMED_VALUE, "text": _PATIENT_TEXT}},
    ) as observed:
        observed.set_output(
            [{"auth": {"token": _UNNAMED_VALUE}, "name": _DISPLAY_NAME}]
        )
        observed.set_metadata(upstream={"password": _UNNAMED_VALUE})

    (span,) = finished_spans(exporter)["nested"]
    exported = _everything_exported([span])
    assert _UNNAMED_VALUE not in exported
    attributes = span_attributes(span)
    assert json.loads(attributes["langfuse.observation.input"]) == {
        "request": {"api_key": "***REDACTED***", "text": _PATIENT_TEXT}
    }
    assert span_output(span) == [
        {"auth": {"token": "***REDACTED***"}, "name": _DISPLAY_NAME}
    ]


def test_a_configured_secret_used_as_a_key_inside_a_payload_is_exported_redacted(
    exporter: InMemorySpanExporter,
) -> None:
    with step("keyed", input=dict.fromkeys(_EVERY_SECRET, "value")):
        pass

    exported = _everything_exported(finished_spans(exporter)["keyed"])

    for secret in _EVERY_SECRET:
        assert secret not in exported


# A patient message that happens to be JSON. It was recorded as text, and text is
# exported as it was written: the key-name rule is for a structure the code recorded,
# not for one a string could be read as.
_JSON_LOOKING_TEXT = '{"password": "x"}'


def test_a_plain_string_that_reads_as_json_is_exported_verbatim(
    exporter: InMemorySpanExporter,
) -> None:
    with step("verbatim", input=_JSON_LOOKING_TEXT) as observed:
        observed.set_output(_JSON_LOOKING_TEXT)
        observed.set_metadata(message=_JSON_LOOKING_TEXT)

    (span,) = finished_spans(exporter)["verbatim"]
    attributes = span_attributes(span)
    assert attributes["langfuse.observation.input"] == _JSON_LOOKING_TEXT
    assert attributes["langfuse.observation.output"] == _JSON_LOOKING_TEXT
    assert attributes["langfuse.observation.metadata.message"] == _JSON_LOOKING_TEXT


def test_a_patient_message_that_reads_as_json_is_the_root_input_verbatim(
    exporter: InMemorySpanExporter,
) -> None:
    with (
        patch("chat.main.AsyncAnthropic", return_value=fake_anthropic_client()),
        TestClient(app) as client,
    ):
        turn(client, _JSON_LOOKING_TEXT)

    (root,) = finished_spans(exporter)["turn"]
    assert span_attributes(root)["langfuse.observation.input"] == _JSON_LOOKING_TEXT


def test_a_secret_named_key_inside_a_model_object_in_a_payload_is_exported_redacted(
    exporter: InMemorySpanExporter,
) -> None:
    # The booking loop sends the model's own content blocks back to it, so a later
    # call's input holds pydantic objects, not only dicts.
    block = ToolUseBlock(
        id="toolu_1",
        name="book_appointment",
        input={"api_key": _UNNAMED_VALUE, "note": _PATIENT_TEXT},
        type="tool_use",
    )
    with generation(
        "model_call",
        model="m",
        input={"messages": ({"role": "assistant", "content": [block]},)},
        model_parameters={"max_tokens": 64},
    ):
        pass

    (span,) = finished_spans(exporter)["model_call"]
    (message,) = json.loads(span_attributes(span)["langfuse.observation.input"])[
        "messages"
    ]
    (content,) = message["content"]
    assert content["input"] == {"api_key": "***REDACTED***", "note": _PATIENT_TEXT}


def test_a_configured_secret_in_a_plain_string_input_is_still_exported_redacted(
    exporter: InMemorySpanExporter,
) -> None:
    secret = _SECRETS["ANTHROPIC_API_KEY"]
    with step("plain", input=f'{{"note": "{secret}"}}'):
        pass

    (span,) = finished_spans(exporter)["plain"]
    assert span_attributes(span)["langfuse.observation.input"] == (
        '{"note": "***REDACTED***"}'
    )


def test_usage_and_model_parameters_are_not_passed_through_the_key_name_rule(
    exporter: InMemorySpanExporter,
) -> None:
    usage = fake_usage()
    with generation(
        "model_call", model="m", input=[], model_parameters={"max_tokens": 64}
    ) as observed:
        observed.record_completion("done", usage, "end_turn")

    (span,) = finished_spans(exporter)["model_call"]
    attributes = span_attributes(span)
    assert json.loads(attributes["langfuse.observation.model.parameters"]) == {
        "max_tokens": 64
    }
    usage_details = json.loads(attributes["langfuse.observation.usage_details"])
    assert usage_details["input"] == usage.input_tokens
    assert usage_details["output"] == usage.output_tokens
    assert usage_details["cache_read_input_tokens"] == usage.cache_read_input_tokens


def test_patient_text_and_names_are_exported_unchanged(
    exporter: InMemorySpanExporter,
) -> None:
    _leak_every_secret()

    exported = _everything_exported(finished_spans(exporter)["leaky"])

    assert _PATIENT_TEXT in exported
    assert _DISPLAY_NAME in exported


def test_a_mask_that_raises_drops_the_batch_rather_than_exporting_it_unmasked(
    exporter: InMemorySpanExporter,
) -> None:
    with patch(
        "chat.observability.masking.redact_value", side_effect=RuntimeError("broken")
    ):
        _leak_every_secret()
        spans = finished_spans(exporter)

    assert spans == {}


def _failing_booking_client(everywhere: str) -> MagicMock:
    """Return a model that calls a tool with `everywhere`, then fails naming it."""
    client = fake_anthropic_client(
        intents=[IntentLabel.BOOKING],
        booking_tool_calls=[[("list_practitioners", {"note": everywhere})]],
    )
    answer = client.messages.create.side_effect
    booking_calls = 0

    async def _create(*args: object, **kwargs: object) -> MagicMock:
        nonlocal booking_calls
        if kwargs.get("tools") is not None:
            booking_calls += 1
            if booking_calls > 1:
                raise RuntimeError(f"upstream refused {everywhere}")
        result: MagicMock = await answer(*args, **kwargs)
        return result

    client.messages.create = AsyncMock(side_effect=_create)
    return client


def test_a_failing_turn_with_tool_calls_exports_no_configured_secret_anywhere(
    exporter: InMemorySpanExporter,
) -> None:
    everywhere = " ".join(_EVERY_SECRET)
    roster = (
        SimpleNamespace(
            id="01PRACTITIONER",
            full_name=_DISPLAY_NAME,
            specialty=everywhere,
            appointment_duration_minutes=30,
            bookable=True,
        ),
    )

    with (
        patch(
            "chat.clients.scheduling.list_practitioners",
            AsyncMock(return_value=roster),
        ),
        patch(
            "chat.main.AsyncAnthropic", return_value=_failing_booking_client(everywhere)
        ),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        turn(client, _PATIENT_TEXT)

    spans = finished_spans(exporter)
    everything = [span for named in spans.values() for span in named]
    exported = _everything_exported(everything)
    for secret in _EVERY_SECRET:
        assert secret not in exported
    # The paths the secrets were sent down were all taken, and were masked - not
    # merely absent from the export.
    (called, *_) = [
        span
        for span in spans["tool:list_practitioners"]
        if "note" in span_attributes(span)["langfuse.observation.input"]
    ]
    assert "***REDACTED***" in span_attributes(called)["langfuse.observation.input"]
    assert "***REDACTED***" in span_attributes(called)["langfuse.observation.output"]
    (failed,) = spans["handle_booking.model[2]"]
    failed_attributes = span_attributes(failed)
    assert failed_attributes["langfuse.observation.level"] == "ERROR"
    assert "***REDACTED***" in failed_attributes["langfuse.observation.status_message"]
    (root,) = spans["turn"]
    assert span_attributes(root)["langfuse.observation.level"] == "ERROR"
