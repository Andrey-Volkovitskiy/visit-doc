"""Drive the labelled evaluation sets against the running stack (spec 009, T080).

One fresh chat per message unless the row needs a preceding assistant turn. For each
turn we keep: the classifier's labels, the router's stopping cause, whether staff were
called and under which reason, whether the conversation fell silent, and the reply.
"""
import json, re, sys, time
from pathlib import Path
import urllib.request

BASE = "http://localhost:8000"
LOG = Path("/home/andrey/visit-doc/.run/chat.log")
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


def say(chat_id, message, local_now="2026-09-08T10:00:00"):
    offset = LOG.stat().st_size
    raw = call("POST", "/chat", {"chat_id": chat_id, "message": message, "local_now": local_now})
    lines = [json.loads(l) for l in raw.strip().splitlines() if l.strip()]
    time.sleep(0.35)  # let the turn's trailing log lines land
    with LOG.open() as f:
        f.seek(offset)
        log = ANSI.sub("", f.read())
    return lines, log


def field(log, event, key):
    for line in log.splitlines():
        if f" {event} " in line:
            m = re.search(rf"\b{key}=(\[[^\]]*\]|\{{.*?\}}|\S+)", line)
            if m:
                return m.group(1)
    return None


def observe(chat_id, message, **kw):
    lines, log = say(chat_id, message, **kw)
    done = lines[-1] if lines else {}
    intents = field(log, "intent.classified", "intents") or ""
    result = field(log, "node.completed", "result") or ""
    stopping = re.search(r"'stopping_cause': '([^']+)'", result)
    raised = re.findall(r"escalation\.(raised|unchanged).*?reason=(\S+)", log)
    return {
        "message": message,
        "intents": re.findall(r"'([a-z_]+)'", intents),
        "stopping_cause": stopping.group(1) if stopping else None,
        "escalations": [r[1] for r in raised],
        "answer_source": done.get("answer_source"),
        "faq_verdict": done.get("faq_verdict"),
        "reply": "".join(l.get("text", "") for l in lines if l.get("type") == "token")
        or done.get("message", ""),
    }


def state(chat_id):
    rows = json.loads(call("GET", "/console/conversations"))["conversations"]
    row = next(r for r in rows if r["chat_id"] == chat_id)
    marks = [m["attention_mark"] for m in json.loads(call("GET", f"/chats/{chat_id}/messages"))["messages"]]
    return {"escalated": row["escalated"], "reason": row["escalation_reason"],
            "may_reply": row["assistant_may_reply"], "emphasized": row["emphasized"],
            "marks": [m for m in marks if m]}


if __name__ == "__main__":
    messages = json.loads(Path(sys.argv[1]).read_text())
    out = []
    first = new_chat()  # mints the session
    for i, item in enumerate(messages):
        chat = first if i == 0 else new_chat()
        rec = {"id": item["id"], "expected": item.get("expected")}
        if item.get("prior"):
            observe(chat, item["prior"])
        rec.update(observe(chat, item["text"]))
        rec["state"] = state(chat)
        out.append(rec)
        print(json.dumps(rec), flush=True)
    Path(sys.argv[2]).write_text(json.dumps(out, indent=1))
