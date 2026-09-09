# Evaluation inputs

| File | What it is |
|---|---|
| `compound.json`, `mixed.json`, `multi.json`, `single.json`, `overriding.json` | The labelled sets — 98 messages, each with the segmentation a human assigned it. Described in [`../messages.md`](../messages.md). |
| `*.out.json` | What the shipped implementation produced, one row per message. The committed record the next run is compared against. |
| `segment.py` | The driver. Calls `classify_intent()` in-process; needs `ANTHROPIC_API_KEY` and nothing else running. |

Run it as described in [`../procedure.md`](../procedure.md). Nothing here is application code, no
tier runs it, and `specs/**/*.py` is excluded from ruff and mypy for that reason.
