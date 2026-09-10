# Evaluation inputs

| File | What it is |
|---|---|
| `setA.json` | 16 two-question messages, exactly one half answerable from the starter corpus. SC-001, SC-002, SC-002a. |
| `setB.json` | 12 messages whose two halves are about one subject, only one covered. SC-003. |
| `single.json` | 1g's single-request set, replayed. SC-011 — a difference count, not a score. |
| `*.out.json` | What a run produced, one row per message. The committed record the next run is compared against. |
| `drive.py` | The driver. Posts each message to a running stack and keeps the stored record beside the reply. |

Described in [`../messages.md`](../messages.md); run as described in [`../procedure.md`](../procedure.md).
Nothing here is application code, no tier runs it, and `specs/**/*.py` is excluded from ruff and
mypy for that reason.
