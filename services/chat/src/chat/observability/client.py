"""The process's one tracer: a Langfuse client over a provider this service owns.

Built once, in the lifespan, from `Settings` alone. Every value the SDK would otherwise
look up in the process environment is passed explicitly, so what the service exports
and where is decided in the one place the rest of its configuration is.
"""

from langfuse import Langfuse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter

from chat.core.config import Settings
from chat.core.logging import known_secret_values
from chat.observability.masking import build_mask
from chat.observability.sampling import UntracedTurnSampler

# How long one export may take before the exporter gives the batch up. The SDK's own
# default, stated here so an environment variable cannot change it behind `Settings`.
_EXPORT_TIMEOUT_SECONDS = 5


class Tracer:
    """The tracing client this process exports through, or nothing when tracing is off.

    A disabled tracer holds no client at all rather than a disabled one: the SDK reads
    any key it is not given from the environment, so the only client that certainly
    exports nothing is the one never built.
    """

    def __init__(
        self,
        *,
        client: Langfuse | None,
        provider: TracerProvider | None,
        environment: str,
        known_secrets: list[str],
    ) -> None:
        """Hold the client and the provider it exports through.

        Args:
            environment: The environment a turn not sent by an eval run is filed under.
            known_secrets: The service's live secret values, redacted from a structured
                value as it is recorded and from an exception's status message - both
                besides the export mask, which redacts them from every attribute.
        """
        self._client = client
        self._provider = provider
        self.environment = environment
        self.known_secrets = known_secrets

    @property
    def enabled(self) -> bool:
        """Return whether this tracer exports anything at all."""
        return self._client is not None

    @property
    def client(self) -> Langfuse | None:
        """Return the Langfuse client, or None when tracing is off."""
        return self._client

    @property
    def provider(self) -> TracerProvider | None:
        """Return the isolated provider the client exports through, if any."""
        return self._provider

    def flush(self) -> None:
        """Export every span that has ended and not yet been sent. Blocks."""
        if self._client is not None:
            self._client.flush()

    def shutdown(self) -> None:
        """Flush, then stop the exporter and the SDK's background threads. Blocks.

        Bounded by the exporter's timeout: a destination that does not answer costs
        at most that long, and whatever it had not accepted is dropped.
        """
        if self._client is not None:
            self._client.shutdown()
        if self._provider is not None:
            self._provider.shutdown()


def build_tracer(
    settings: Settings, *, span_exporter: SpanExporter | None = None
) -> Tracer:
    """Build the tracer `settings` describe.

    Args:
        span_exporter: Replaces the HTTP exporter to Langfuse - a test passes an
            in-memory one, and then no request carrying the keys is ever made.

    The provider is this service's own, never OpenTelemetry's global one: that can be
    set once per process, and whatever set it first would keep it for every later
    tracer. Its sampler drops every span of a turn that asked not to be traced.
    """
    known_secrets = known_secret_values(settings)
    if not settings.tracing_enabled:
        return Tracer(
            client=None,
            provider=None,
            environment=settings.LANGFUSE_ENVIRONMENT,
            known_secrets=known_secrets,
        )
    provider = TracerProvider(sampler=UntracedTurnSampler())
    client = Langfuse(
        public_key=settings.LANGFUSE_PUBLIC_KEY.strip(),
        secret_key=settings.LANGFUSE_SECRET_KEY.strip(),
        base_url=settings.LANGFUSE_BASE_URL,
        timeout=_EXPORT_TIMEOUT_SECONDS,
        tracing_enabled=settings.tracing_enabled,
        environment=settings.LANGFUSE_ENVIRONMENT,
        sample_rate=1.0,
        tracer_provider=provider,
        mask_otel_spans=build_mask(known_secrets),
        span_exporter=span_exporter,
    )
    return Tracer(
        client=client,
        provider=provider,
        environment=settings.LANGFUSE_ENVIRONMENT,
        known_secrets=known_secrets,
    )
