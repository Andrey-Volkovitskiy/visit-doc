"""The tracer factory and its per-turn sampler.

What is under test is the process's one tracer as the lifespan builds it: that it
exports when both keys are set and builds nothing when they are not, that it never
takes over OpenTelemetry's global provider, and that a context carrying the untraced
flag exports nothing - even under a trace id seeded to force the parent's sampled bit.
"""

from unittest.mock import patch

from chat.core.config import Settings
from chat.observability import sampling
from chat.observability.client import build_tracer
from langfuse import Langfuse
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from .conftest import finished_spans, installed_tracer, tracing_settings


def test_an_enabled_tracer_exports_an_observation() -> None:
    exporter = InMemorySpanExporter()

    with installed_tracer(tracing_settings(), exporter) as tracer:
        assert tracer.enabled
        assert tracer.client is not None
        with tracer.client.start_as_current_observation(name="probe"):
            pass

        spans = finished_spans(exporter)

    assert list(spans) == ["probe"]


def test_a_disabled_tracer_builds_no_client_and_exports_nothing() -> None:
    exporter = InMemorySpanExporter()
    settings = Settings(LANGFUSE_PUBLIC_KEY="", LANGFUSE_SECRET_KEY="")

    with patch("chat.observability.client.Langfuse") as langfuse_cls:
        tracer = build_tracer(settings, span_exporter=exporter)

    # Not a disabled client: no client at all. The SDK would read the keys it was not
    # given from the process environment, so the only safe disabled client is none.
    langfuse_cls.assert_not_called()
    assert not tracer.enabled
    assert tracer.client is None
    tracer.flush()
    tracer.shutdown()
    assert exporter.get_finished_spans() == ()


def test_the_tracer_never_takes_over_the_global_provider() -> None:
    before = trace.get_tracer_provider()

    with installed_tracer(tracing_settings(), InMemorySpanExporter()) as tracer:
        assert trace.get_tracer_provider() is before
        assert tracer.provider is not None
        assert tracer.provider is not before


def test_every_value_comes_from_settings_not_the_sdks_environment_lookup() -> None:
    settings = tracing_settings(
        LANGFUSE_BASE_URL="https://langfuse.example.test",
        LANGFUSE_ENVIRONMENT="staging",
    )

    with patch("chat.observability.client.Langfuse") as langfuse_cls:
        build_tracer(settings, span_exporter=InMemorySpanExporter())

    kwargs = langfuse_cls.call_args.kwargs
    assert kwargs["public_key"] == settings.LANGFUSE_PUBLIC_KEY
    assert kwargs["secret_key"] == settings.LANGFUSE_SECRET_KEY
    assert kwargs["base_url"] == "https://langfuse.example.test"
    assert kwargs["environment"] == "staging"
    assert kwargs["tracing_enabled"] is True
    assert kwargs["tracer_provider"] is not None
    assert kwargs["mask_otel_spans"] is not None


def test_an_untraced_context_records_and_exports_nothing() -> None:
    exporter = InMemorySpanExporter()

    with installed_tracer(tracing_settings(), exporter) as tracer:
        assert tracer.client is not None
        token = sampling.UNTRACED.set(True)
        try:
            with tracer.client.start_as_current_observation(name="dropped") as obs:
                recording = trace.get_current_span().is_recording()
                with tracer.client.start_as_current_observation(name="child"):
                    child_recording = trace.get_current_span().is_recording()
                obs.update(output="unseen")
        finally:
            sampling.UNTRACED.reset(token)

        spans = finished_spans(exporter)

    assert not recording
    assert not child_recording
    assert spans == {}


def test_the_untraced_flag_outranks_a_seeded_trace_context() -> None:
    # A seeded trace id arrives as a remote parent marked sampled, so a sampler that
    # consulted the parent first would record the turn it was asked to drop.
    exporter = InMemorySpanExporter()

    with installed_tracer(tracing_settings(), exporter) as tracer:
        assert tracer.client is not None
        token = sampling.UNTRACED.set(True)
        try:
            with tracer.client.start_as_current_observation(
                trace_context={"trace_id": Langfuse.create_trace_id(seed="turn-1")},
                name="turn",
            ):
                recording = trace.get_current_span().is_recording()
        finally:
            sampling.UNTRACED.reset(token)

        spans = finished_spans(exporter)

    assert not recording
    assert spans == {}


def test_the_flag_left_unset_traces_a_seeded_root() -> None:
    exporter = InMemorySpanExporter()
    trace_id = Langfuse.create_trace_id(seed="turn-2")

    with installed_tracer(tracing_settings(), exporter) as tracer:
        assert tracer.client is not None
        with tracer.client.start_as_current_observation(
            trace_context={"trace_id": trace_id}, name="turn"
        ):
            pass

        spans = finished_spans(exporter)

    (root,) = spans["turn"]
    assert root.context is not None
    assert format(root.context.trace_id, "032x") == trace_id
