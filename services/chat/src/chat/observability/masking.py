"""Export-time masking: no configured secret value leaves the process inside a span.

Applied by the SDK on its exporter thread, to every attribute of every span in a batch
- not only the input, output and metadata its older `mask` hook sees, but the status
message too, which is where an exception's text is recorded. Each string, and each
string of an array attribute, has every occurrence of a configured secret value
replaced, by `shared_logging`'s rule and with the same known secret values the log
matches, so the log and the trace cannot disagree about what a secret is.

That is the only pass made here. The other half of the log's rule - a value under a
secret-named key is replaced whole - is applied where `chat.observability` records a
structured value, before the SDK serializes it, because only there is a structure told
apart from a string: once exported, a payload the code recorded as a dict and a patient
message that happens to read as JSON are the same kind of attribute. Nothing is decoded
here, so a string is exported as it was recorded, apart from the secret values in it.
A secret value used as a key inside a serialized payload is still caught, since the
pass runs over the serialized text, keys included.
"""

from collections.abc import Sequence

from langfuse.types import (
    MaskOtelSpansFunction,
    MaskOtelSpansParams,
    MaskOtelSpansResult,
    OtelSpanIdentifier,
    OtelSpanPatch,
)
from opentelemetry.util.types import AttributeValue
from shared_logging import redact_value


def build_mask(known_secrets: Sequence[str]) -> MaskOtelSpansFunction:
    """Build the `mask_otel_spans` hook that redacts `known_secrets` from every span.

    A failure inside the hook makes the SDK drop the whole batch: a trace is lost, and
    nothing is exported unmasked.
    """
    secrets = list(known_secrets)

    def mask_otel_spans(*, params: MaskOtelSpansParams) -> MaskOtelSpansResult:
        """Return a patch for every span with an attribute the rule changes."""
        patches: dict[OtelSpanIdentifier, OtelSpanPatch | None] = {}
        for identifier, span in params.spans.items():
            changed = {
                key: masked
                for key, value in span.attributes.items()
                if (masked := _masked(value, secrets)) != value
            }
            if changed:
                patches[identifier] = OtelSpanPatch(set_attributes=changed)
        return MaskOtelSpansResult(span_patches=patches)

    return mask_otel_spans


def _masked(value: AttributeValue, secrets: list[str]) -> AttributeValue:
    """Return `value` with every occurrence of a configured secret value replaced.

    A string is redacted as the text it is, and an array item by item; a number or a
    boolean cannot hold a secret and is returned unchanged.
    """
    if isinstance(value, str):
        redacted: str = redact_value(value, secrets)
        return redacted
    if isinstance(value, bool | int | float):
        return value
    return tuple(
        redact_value(item, secrets) if isinstance(item, str) else item for item in value
    )
