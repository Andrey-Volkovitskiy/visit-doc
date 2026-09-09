"""Measure how the classifier splits a message, in-process (spec 010, T049).

Calls `classify_intent()` directly rather than driving a whole turn through the API:
what these sets measure is the segmentation itself, and a turn would additionally
spend retrieval and generation on every message to tell us nothing more about it.

    uv run python specs/010-multi-request-turns/evaluation/inputs/segment.py \
        <input.json> <output.json>

Each row records the segmentation the model chose against the segmentation a human
assigned it, so a later run can be compared with the recorded one rather than rerun
from scratch.
"""
import asyncio
import json
import sys
from pathlib import Path

from anthropic import AsyncAnthropic
from chat.agent.classify_intent import ClassificationFailedError, classify_intent
from chat.core.config import get_settings
from chat.domain.models import Message, MessageSender


def bursts(item):
    """Build the history one row stands for: an optional prior turn, then the message."""
    history = []
    if item.get("prior"):
        history.append([Message(sender=MessageSender.PATIENT, content=item["prior"], id="p0")])
        history.append([Message(sender=MessageSender.ASSISTANT, content="Of course.", id="a0")])
    history.append([Message(sender=MessageSender.PATIENT, content=item["text"], id="p1")])
    return history


async def classify_one(client, item):
    try:
        result = await classify_intent(client, bursts(item))
    except ClassificationFailedError as exc:
        # A rejected result is a miss, not an absent row: counting only the rows that
        # came back would let the measurement flatter itself exactly where the
        # classifier failed hardest.
        row = {"id": item["id"], "text": item["text"], "error": str(exc)}
        if "expected" in item:
            row["expected"] = item["expected"]
            row["count_matches"] = False
        if "expected_label" in item:
            row["expected_label"] = item["expected_label"]
            row["label_present"] = False
        return row
    row = {
        "id": item["id"],
        "text": item["text"],
        "segments": [{"intent": s.intent.value, "text": s.text} for s in result.segments],
        "count": len(result.segments),
        "cap_bound": result.cap_bound,
    }
    if "expected" in item:
        row["expected"] = item["expected"]
        row["count_matches"] = len(result.segments) == item["expected"]
    if "expected_label" in item:
        row["expected_label"] = item["expected_label"]
        row["label_present"] = item["expected_label"] in [s.intent.value for s in result.segments]
    return row


async def main():
    items = json.loads(Path(sys.argv[1]).read_text())
    client = AsyncAnthropic(api_key=get_settings().ANTHROPIC_API_KEY)
    rows = []
    try:
        for item in items:
            row = await classify_one(client, item)
            rows.append(row)
            print(json.dumps(row), flush=True)
    finally:
        await client.close()
    Path(sys.argv[2]).write_text(json.dumps(rows, indent=1))
    counted = [r for r in rows if "count_matches" in r]
    labelled = [r for r in rows if "label_present" in r]
    if counted:
        hits = sum(r["count_matches"] for r in counted)
        print(f"segment count: {hits}/{len(counted)} = {hits / len(counted):.0%}")
    if labelled:
        hits = sum(r["label_present"] for r in labelled)
        print(f"label present: {hits}/{len(labelled)} = {hits / len(labelled):.0%}")


if __name__ == "__main__":
    asyncio.run(main())
