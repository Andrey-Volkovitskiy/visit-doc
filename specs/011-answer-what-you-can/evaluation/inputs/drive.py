"""Drive the labelled sets against the running stack (spec 011, T059).

    uv run python specs/011-answer-what-you-can/evaluation/inputs/drive.py \
        <input.json> <output.json>

One fresh chat per message, in one session, so every turn retrieves against the starter
corpus a new session is planted with. For each turn we keep what the *record* says - the
stored `request_outcomes`, per request - beside the reply the patient was shown, because
that pair is what SC-002 through SC-004a are judged from, and what the record-based check
in `../procedure.md` re-reads later.

Nothing here is application code, no tier runs it, and `specs/**/*.py` is excluded from
ruff and mypy for that reason.
"""
import json, re, sys, time
from pathlib import Path
import urllib.request

BASE = "http://localhost:8000"
# The running chat service's log, relative to this file rather than to whoever ran it:
# specs/<feature>/evaluation/inputs/drive.py -> <repo root>/.run/chat.log
LOG = Path(__file__).resolve().parents[4] / ".run" / "chat.log"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
COOKIE = {}


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("content-type", "application/json")
    if COOKIE:
        req.add_header("cookie", "; ".join(f"{k}={v}" for k, v in COOKIE.items()))
    with urllib.request.urlopen(req, timeout=180) as r:
        for header in r.headers.get_all("set-cookie") or []:
            k, _, v = header.split(";")[0].partition("=")
            COOKIE[k] = v
        return r.read().decode()


def new_chat():
    return json.loads(call("POST", "/chats"))["id"]


def say(chat_id, message, local_now="2026-09-10T10:00:00"):
    offset = LOG.stat().st_size if LOG.exists() else 0
    raw = call("POST", "/chat", {"chat_id": chat_id, "message": message, "local_now": local_now})
    lines = [json.loads(l) for l in raw.strip().splitlines() if l.strip()]
    time.sleep(0.35)  # let the turn's trailing log lines land
    log = ""
    if LOG.exists():
        with LOG.open() as f:
            f.seek(offset)
            log = ANSI.sub("", f.read())
    return lines, log


def observe(chat_id, message, **kw):
    """Post one message and return what the turn produced, from the wire and the log."""
    lines, log = say(chat_id, message, **kw)
    done = lines[-1] if lines else {}
    streamed = "".join(l.get("text", "") for l in lines if l.get("type") == "token")
    raised = re.findall(r"escalation\.(?:raised|unchanged).*?reason=(\S+)", log)
    composing = log.count("'merged': True")
    return {
        "message": message,
        "reply": streamed or done.get("message", ""),
        "answer_source": done.get("answer_source"),
        # The record, per request: question as the classifier restated it, the answer
        # generated for it before any merge, its verdict, and what it cited.
        "request_outcomes": done.get("request_outcomes"),
        "escalations": raised,
        "composed": bool(composing),
    }


def stored(chat_id):
    """Re-read the thread, so what was stored is compared rather than what was streamed."""
    messages = json.loads(call("GET", f"/chats/{chat_id}/messages"))["messages"]
    reply = next((m for m in reversed(messages) if m["sender"] == "assistant"), None)
    return {
        "stored_reply": reply["content"] if reply else None,
        "stored_outcomes": reply["request_outcomes"] if reply else None,
        "marks": [m["attention_mark"] for m in messages if m["attention_mark"]],
    }


if __name__ == "__main__":
    messages = json.loads(Path(sys.argv[1]).read_text())
    out = []
    first = new_chat()  # mints the session, and plants the starter corpus in it
    for i, item in enumerate(messages):
        chat = first if i == 0 else new_chat()
        rec = {k: v for k, v in item.items() if k != "text"}
        if item.get("prior"):
            observe(chat, item["prior"])
        rec.update(observe(chat, item["text"]))
        rec.update(stored(chat))
        rec["chat_id"] = chat
        out.append(rec)
        print(json.dumps(rec), flush=True)
    Path(sys.argv[2]).write_text(json.dumps(out, indent=1))
