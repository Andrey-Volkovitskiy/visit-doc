"""Shared "meaningless content" check."""

import re

_LABEL_PATTERN = re.compile(r"question:|answer:", re.IGNORECASE)
_MEANINGLESS_CHARS = " \t\n\r-"


def is_meaningless(text: str) -> bool:
    """Return True if `text` has no meaningful content.

    Meaningless = nothing remains after stripping whitespace, dashes, and any bare
    `Question:`/`Answer:` labels.
    """
    without_labels = _LABEL_PATTERN.sub("", text)
    return without_labels.strip(_MEANINGLESS_CHARS) == ""
