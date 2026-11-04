"""Diff two runs' FAQ verdicts and rerank survivors request by request.

The harness scores retrieval by entry, so a chunk replaced by another chunk of the same
entry moves no metric; this reads the chunks themselves. Entry ids are mapped back to
corpus slugs through each run's `entry_ids`, so two runs seeded into different
sessions compare.

Usage: uv run python diff_verdicts.py <base_run_dir> <new_run_dir>
"""

import json
import sys
from pathlib import Path


def _load(run_dir: str) -> tuple[dict, dict]:
    d = Path(run_dir)
    slug = {v: k for k, v in json.loads((d / "run.json").read_text())["entry_ids"].items()}
    out: dict = {}
    for f in sorted((d / "cases").glob("*.json")):
        events = json.loads(f.read_text()).get("events") or []
        turns: list = []
        for e in events:
            if e.get("turn_id") is not None and e["turn_id"] not in turns:
                turns.append(e["turn_id"])
        for e in events:
            if e.get("event") in ("faq.verdict", "faq.rerank_gate", "faq.similarity_gate"):
                key = (f.stem, turns.index(e["turn_id"]), e["segment"])
                out.setdefault(key, {})[e["event"]] = e
    return out, slug


def _kept(event: dict | None, slug: dict) -> list:
    return [(slug.get(x["entry_id"]), x["chunk_index"]) for x in (event or {}).get("kept", [])]


(base, base_slug), (new, new_slug) = _load(sys.argv[1]), _load(sys.argv[2])
verdicts = survivors = tops = sent_base = sent_new = 0
for key in sorted(set(base) | set(new), key=str):
    b, n = base.get(key, {}), new.get(key, {})
    sent_base += len(_kept(b.get("faq.similarity_gate"), base_slug))
    sent_new += len(_kept(n.get("faq.similarity_gate"), new_slug))
    bv = b.get("faq.verdict", {}).get("verdict")
    nv = n.get("faq.verdict", {}).get("verdict")
    bk = _kept(b.get("faq.rerank_gate"), base_slug)
    nk = _kept(n.get("faq.rerank_gate"), new_slug)
    if bv != nv:
        verdicts += 1
        print("VERDICT", key, bv, "->", nv, bk, "->", nk)
    elif bk != nk:
        survivors += 1
        top = bk[:1] != nk[:1]
        tops += top
        print("TOP" if top else "SURVIVORS", key, bk, "->", nk)
print(
    f"requests {len(base)} -> {len(new)}; verdict changes {verdicts}; survivor changes "
    f"without one {survivors} (top survivor changed {tops}); chunks sent to the "
    f"reranker {sent_base} -> {sent_new}"
)
