"""018 offline probe: replay a stored run's retrieval through the checked-out build.

For every FAQ segment the baseline run searched, take the classifier's stored `query`,
run production's own `_run_pipeline` (both search branches, the shortlist gate, the
real rerank, the rerank gate, the verdict) against a freshly seeded corpus carrying
the baseline's entry ids, and splice the events it logs into a copy of the run in
place of the baseline's. No generation call is made, so only retrieval and the rerank
verdict are meaningful in the copy; the replies stay the baseline's, and so do the
serving rows a comparison reads from them.

Run from the repo root, with Qdrant up and VOYAGE_API_KEY in .env (it spends Voyage
embed and rerank calls, a few cents):

    uv run python specs/018-hybrid-retrieval/evaluation/probe/hybrid_probe.py \
        evals/baselines/01M3WD842TTD1Q9FX8TDRTMFAB <out_dir>
    make eval-compare BASE=evals/baselines/01M3WD842TTD1Q9FX8TDRTMFAB NEW=<out_dir>
    uv run python specs/018-hybrid-retrieval/evaluation/probe/diff_verdicts.py \
        evals/baselines/01M3WD842TTD1Q9FX8TDRTMFAB <out_dir>

`PROBE_DENSE_ONLY=1` empties the lexical branch: the control, whose movement against
the baseline is the replay's own rather than BM25's. The RRF probe ran on `07ff68d`,
the dense-plus-BM25 one on `cd84c89`; the comparisons are beside this file.

This file is a reconstruction: the scratch copy those probes ran from was lost, and it was
rebuilt from the session that wrote it, running the replays in a plain loop and reading the
conditions from the build rather than writing them in. It has not been re-run since, so check
its output against `dense-plus-bm25.md` before relying on it.
"""

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

os.environ["QDRANT_COLLECTION_NAME"] = "faq_probe_018"

from structlog.testing import capture_logs  # noqa: E402
from ulid import ULID  # noqa: E402
from voyageai.client_async import AsyncClient  # noqa: E402

from chat.agent.answer_faq import _run_pipeline  # noqa: E402
from chat.core.config import get_settings  # noqa: E402
from chat.rag.indexing import publish_revision  # noqa: E402
from chat.rag.retriever import RETRIEVAL_METHOD  # noqa: E402
from chat.repositories import qdrant_repository  # noqa: E402
from golden_harness.corpus import load_pin  # noqa: E402

if os.environ.get("PROBE_DENSE_ONLY") == "1":
    from chat.rag import retriever as _retriever
    from chat.repositories.qdrant_repository import SearchBranches

    _real_search = _retriever.search

    async def _dense_only(*args, **kwargs):  # type: ignore[no-untyped-def]
        found = await _real_search(*args, **kwargs)
        return SearchBranches(dense=found.dense, lexical=[])

    _retriever.search = _dense_only  # type: ignore[assignment]

_REPLACED = {
    "faq.retrieval_completed",
    "faq.similarity_gate",
    "faq.reranking_completed",
    "faq.reranking_unavailable",
    "faq.rerank_gate",
    "faq.verdict",
}
_ATTEMPTS = 4


async def _seed(qdrant, voyage, entry_ids: dict[str, int]) -> tuple[str, list[str]]:
    pin = load_pin(Path("evals/golden/corpus.json"))
    session_id = str(ULID())
    revisions = []
    for entry in pin.entries:
        revision = str(ULID())
        await publish_revision(
            qdrant, voyage, session_id, entry_ids[entry.id], revision, entry.text
        )
        revisions.append(revision)
    return session_id, revisions


async def _replay(qdrant, voyage, query, session_id, revisions) -> list[dict]:
    for attempt in range(_ATTEMPTS):
        try:
            with capture_logs() as logs:
                await _run_pipeline(
                    qdrant, voyage, voyage, query, session_id, revisions
                )
        except Exception as exc:  # noqa: BLE001 - a transient Voyage error is retried
            print(f"  retry {attempt + 1} after {type(exc).__name__}: {exc}", flush=True)
            await asyncio.sleep(2 * (attempt + 1))
            continue
        events = [e for e in logs if e.get("event") in _REPLACED]
        names = [e["event"] for e in events]
        if names.count("faq.retrieval_completed") != 1 or names.count("faq.verdict") != 1:
            raise RuntimeError(f"{query!r}: captured {names}")
        if any(e["event"] == "faq.reranking_unavailable" for e in events):
            print(f"  retry {attempt + 1}: reranker unavailable", flush=True)
            await asyncio.sleep(2 * (attempt + 1))
            continue
        return events
    raise RuntimeError(f"gave up on {query!r}")


async def main(base: Path, out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(base, out, ignore=shutil.ignore_patterns("report.*", "README.md"))
    run = json.loads((out / "run.json").read_text())
    settings = get_settings()
    qdrant = qdrant_repository.create_client(settings)
    voyage = AsyncClient(api_key=settings.VOYAGE_API_KEY)
    if await qdrant.collection_exists(qdrant_repository.COLLECTION_NAME):
        await qdrant.delete_collection(qdrant_repository.COLLECTION_NAME)
    await qdrant_repository.ensure_collection(qdrant)
    try:
        session_id, revisions = await _seed(qdrant, voyage, run["entry_ids"])
        print(f"seeded {len(revisions)} entries", flush=True)

        jobs = []
        for case_path in sorted((out / "cases").glob("*.json")):
            case = json.loads(case_path.read_text())
            queries = {}
            for event in case.get("events") or []:
                if event.get("event") != "intent.classified":
                    continue
                for segment in event.get("segments") or []:
                    if segment["intent"] == "faq_question":
                        queries[(event["turn_id"], segment["position"])] = segment["query"]
            searched = {
                (e["turn_id"], e["segment"])
                for e in case.get("events") or []
                if e.get("event") == "faq.retrieval_completed"
            }
            for key in sorted(searched):
                jobs.append((case_path, key, queries[key]))
        print(f"{len(jobs)} searched segments to replay", flush=True)

        # capture_logs is process-global, so replays run one at a time: two at once
        # would each capture the other's events.
        results = []
        for job in jobs:
            results.append(
                (job, await _replay(qdrant, voyage, job[2], session_id, revisions))
            )

        by_case: dict[Path, dict] = {}
        for (case_path, (turn_id, segment), _query), events in results:
            by_case.setdefault(case_path, {})[(turn_id, segment)] = events
        for case_path, replays in by_case.items():
            case = json.loads(case_path.read_text())
            rebuilt, inserted = [], set()
            for event in case["events"]:
                key = (event.get("turn_id"), event.get("segment"))
                if event.get("event") in _REPLACED and key in replays:
                    if key not in inserted:
                        for new in replays[key]:
                            rebuilt.append(
                                {
                                    **new,
                                    "node": "answer_faq",
                                    "segment": key[1],
                                    "turn_id": key[0],
                                    "level": new.get("log_level", "info"),
                                }
                            )
                        inserted.add(key)
                    continue
                rebuilt.append(event)
            for event in rebuilt:
                event.pop("log_level", None)
            case["events"] = rebuilt
            case_path.write_text(json.dumps(case, indent=2))

        # The conditions the checked-out build states, as `service.configured` would.
        conditions = run["conditions"]
        conditions.pop("similarity_floor", None)
        conditions["retrieval"] = RETRIEVAL_METHOD
        conditions["lexical_cap"] = settings.LEXICAL_CAP
        run["run_id"] = str(ULID())
        (out / "run.json").write_text(json.dumps(run, indent=2))
        print(f"wrote {out} as run {run['run_id']}", flush=True)
    finally:
        await qdrant.delete_collection(qdrant_repository.COLLECTION_NAME)
        await qdrant.close()


asyncio.run(main(Path(sys.argv[1]), Path(sys.argv[2])))
