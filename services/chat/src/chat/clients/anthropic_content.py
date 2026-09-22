"""What an Anthropic response said, in a shape a trace can record.

A response's content is a list of the SDK's block objects. A trace records plain data,
so each call site that records a model call hands its blocks through here rather than
deciding on its own which of a block's fields are worth keeping.
"""

from collections.abc import Iterable
from typing import Any


def content_as_output(blocks: Iterable[Any]) -> list[dict[str, Any]]:
    """Describe each content block by its type and the fields that type carries.

    Returns: one dict per block, in response order - `text` for a text block; `id`,
        `name` and `input` for a tool call; the type alone for any other kind.
    """
    described: list[dict[str, Any]] = []
    for block in blocks:
        kind = block.type
        if kind == "text":
            described.append({"type": kind, "text": block.text})
            continue
        if kind == "tool_use":
            described.append(
                {"type": kind, "id": block.id, "name": block.name, "input": block.input}
            )
            continue
        described.append({"type": kind})
    return described
