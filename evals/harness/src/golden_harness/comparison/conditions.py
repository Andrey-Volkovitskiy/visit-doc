"""The condition delta: what the two runs were measured under, field by field.

Asymmetric on purpose. A threshold that differs is usually *the change under test*, so
it is reported and the comparison continues (FR-009); a label that differs means the
two runs answered different questions, and that refusal belongs to scoring, which
already makes it (research R6). The corpus hash sits with the conditions rather than
with the labels for the same reason: a corpus edit does not change what a label says,
it changes what the label is about - so it is reported, first and separately, because a
reader scanning eleven rows can miss one (FR-010).
"""

from typing import Final

from golden_harness.comparison.model import ConditionChange, ConditionDelta
from golden_harness.record import RunConditions
from golden_harness.report import Report

# The field name the corpus hash is reported under. It is not a `RunConditions` field -
# the run records it beside them - and the delta names it as one so a reader has one
# list to read rather than two.
CORPUS_FIELD: Final = "corpus_sha256"


def condition_delta(base: Report, new: Report) -> ConditionDelta:
    """Return every condition that differs, corpus hash first, then declaration order.

    Both values are rendered as text, so a float threshold and a model id read alike.
    """
    changes: list[ConditionChange] = []
    if corpus_moved(base, new):
        changes.append(
            ConditionChange(
                field=CORPUS_FIELD,
                base=base.corpus.live_sha256,
                new=new.corpus.live_sha256,
            )
        )
    changes.extend(
        ConditionChange(field=name, base=str(was), new=str(is_now))
        for name in RunConditions.model_fields
        if (was := getattr(base.conditions, name))
        != (is_now := getattr(new.conditions, name))
    )
    return ConditionDelta(changes=changes)


def corpus_moved(base: Report, new: Report) -> bool:
    """Whether the two runs answered against different corpus text.

    The live hash is what the run actually answered from; the pin is what it was
    checked against, and a run whose check failed still answered from the live text.
    """
    return base.corpus.live_sha256 != new.corpus.live_sha256
