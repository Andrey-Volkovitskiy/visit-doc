"""The harness command line: `run` drives cases, `score` re-scores, `compare` reads two.

`score` and `compare` read stored runs and the labels and nothing else, so both work
with the stack down. `run` needs the chat service running with `LOG_FORMAT=json`, its
log file, and its database.

`compare` never writes into either run it reads: its own record goes under the
artifacts directory, in a directory named after the two runs (research R8). It exits 0
whatever it finds - a finding is not a failure, and there is no flag that changes that
(FR-006). Refusing to compare at all is a different thing, and takes the reported-
failure path every other refusal takes.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Final

import httpx
from pydantic import ValidationError

from golden_harness.cases import LabelError, SelectionError, load_cases, select
from golden_harness.comparison.band import BandRefusedError, NoiseBand, build_band
from golden_harness.comparison.compare import NoCommonCasesError, compare
from golden_harness.comparison.render import (
    render_band,
    render_comparison,
    to_json,
)
from golden_harness.corpus import UnmappedCorpusEntryError, load_pin
from golden_harness.driver.logslice import ConditionsMissingError, JsonLogMissingError
from golden_harness.driver.run import (
    DEFAULT_CLOCK,
    RunStoppedError,
    drive_run,
    live_stack,
    resume_run,
)
from golden_harness.driver.scheduling import CleanupFailedError
from golden_harness.driver.session import ChatNotFoundError, SessionError
from golden_harness.driver.turn import ThreadReadError, TurnProtocolError
from golden_harness.record import write_atomically
from golden_harness.report import LabelDigestMismatchError, render_summary, score_run
from golden_harness.scoring.alignment import ConservationError
from golden_harness.scoring.retrieval import LogContractError

_REPO_ROOT: Final = Path(__file__).resolve().parents[4]
_GOLDEN: Final = _REPO_ROOT / "evals" / "golden"
DEFAULT_ARTIFACTS: Final = _REPO_ROOT / ".run" / "evals"
DEFAULT_LOG: Final = _REPO_ROOT / ".run" / "chat.log"
DEFAULT_LABELS: Final = _GOLDEN / "cases.json"
DEFAULT_BASE_URL: Final = "http://localhost:8000"
COMPARISONS: Final = "comparisons"
COMPARISON_JSON: Final = "comparison.json"
COMPARISON_MD: Final = "comparison.md"
BANDS: Final = "bands"
_SCHEMA: Final = _GOLDEN / "schema.json"
_CORPUS_PIN: Final = _GOLDEN / "corpus.json"

# The failures the harness reports as a sentence rather than a traceback: each already
# names what stopped and why. `httpx.HTTPError` is the chat service not reached, or not
# answering, on a call the driver does not classify itself - creating a chat, reading
# the corpus, reading a thread back.
_REPORTED_FAILURES: Final = (
    BandRefusedError,
    ChatNotFoundError,
    CleanupFailedError,
    ConditionsMissingError,
    ConservationError,
    FileNotFoundError,
    JsonLogMissingError,
    LabelDigestMismatchError,
    LabelError,
    LogContractError,
    NoCommonCasesError,
    RunStoppedError,
    SelectionError,
    SessionError,
    ThreadReadError,
    TurnProtocolError,
    UnmappedCorpusEntryError,
    ValidationError,
    httpx.HTTPError,
)


class RunNotFoundError(FileNotFoundError):
    """Neither a run directory nor a run id under the artifacts directory."""


class BandNotFoundError(FileNotFoundError):
    """Neither a band file nor a band id under the artifacts directory."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line.

    Raises: SystemExit on invalid arguments, as argparse does.
    """
    parser = argparse.ArgumentParser(prog="golden_harness")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="drive cases against the running stack")
    chosen = run.add_mutually_exclusive_group()
    chosen.add_argument(
        "--resume", help="a run id or directory to continue", default=None
    )
    chosen.add_argument(
        "--cases", type=_case_ids, help="comma-separated case ids", default=None
    )
    chosen.add_argument("--family", help="drive one family of cases", default=None)
    run.add_argument(
        "--clock",
        type=_local_clock,
        default=None,
        help=f"local time sent with every turn (default {DEFAULT_CLOCK.isoformat()})",
    )
    run.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    run.add_argument("--log", type=Path, default=DEFAULT_LOG)
    run.add_argument("--base-url", default=DEFAULT_BASE_URL)
    run.add_argument("--labels", type=Path, default=DEFAULT_LABELS)

    score = commands.add_parser("score", help="re-score a stored run from its files")
    score.add_argument("--run", required=True, help="a run id or directory")
    score.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    score.add_argument("--labels", type=Path, default=DEFAULT_LABELS)

    comparison = commands.add_parser(
        "compare", help="report what moved between two stored runs"
    )
    comparison.add_argument(
        "--base", required=True, help="the run compared from: a run id or directory"
    )
    comparison.add_argument(
        "--new", required=True, help="the run compared to: a run id or directory"
    )
    comparison.add_argument(
        "--band",
        default=None,
        help="a band id or file to mark each movement against",
    )
    comparison.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    comparison.add_argument("--labels", type=Path, default=DEFAULT_LABELS)

    band = commands.add_parser(
        "band", help="measure the run-to-run noise from five full runs of one build"
    )
    band.add_argument(
        "--runs",
        required=True,
        type=_case_ids,
        help="five run ids or directories, comma-separated, in the order taken",
    )
    band.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    band.add_argument("--labels", type=Path, default=DEFAULT_LABELS)

    args = parser.parse_args(argv)
    if args.command == "run" and args.resume is not None and args.clock is not None:
        run.error("--clock cannot be given with --resume, which keeps the run's clock")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """Run the harness command line.

    Returns: the process exit status
    """
    args = parse_args(argv)
    try:
        if args.command == "score":
            run_dir = resolve_run(args.run, args.artifacts)
            cases = load_cases(args.labels, _SCHEMA)
            print(render_summary(score_run(run_dir, cases)))
            return 0
        if args.command == "compare":
            _compare(args)
            return 0
        if args.command == "band":
            _band(args)
            return 0
        asyncio.run(_run(args))
        return 0
    except _REPORTED_FAILURES as exc:
        print(f"golden_harness: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def resolve_run(value: str, artifacts: Path) -> Path:
    """Find a run from a directory path or a bare run id under `artifacts`.

    Raises: RunNotFoundError when `value` is neither.
    """
    given = Path(value)
    if given.is_dir():
        return given
    under_artifacts = artifacts / value
    if under_artifacts.is_dir():
        return under_artifacts
    raise RunNotFoundError(
        f"no run {value!r}: not a directory, and not a run under {artifacts}"
    )


async def _run(args: argparse.Namespace) -> None:
    """Drive or resume a run, then score it and print the summary.

    Raises: RunNotFoundError, and whatever stops a run or its scoring.
    """
    cases = load_cases(args.labels, _SCHEMA)
    pin = load_pin(_CORPUS_PIN)
    resumed = (
        resolve_run(args.resume, args.artifacts) if args.resume is not None else None
    )
    async with live_stack(args.base_url) as stack:
        if resumed is not None:
            run_dir = await resume_run(
                resumed, cases, stack=stack, log_path=args.log, pin=pin
            )
        else:
            run_dir = await drive_run(
                cases,
                select(cases, ids=args.cases, family=args.family),
                stack=stack,
                log_path=args.log,
                pin=pin,
                artifacts_dir=args.artifacts,
                clock=args.clock if args.clock is not None else DEFAULT_CLOCK,
            )
    print(render_summary(score_run(run_dir, cases)))


def _local_clock(value: str) -> datetime:
    """Parse `--clock` as a local date-time carrying no timezone.

    Raises: argparse.ArgumentTypeError when `value` is not ISO-8601 or carries an
        offset - the chat service refuses a `local_now` with one, and the scheduler's
        booking predicates cannot compare it with a local start.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"not an ISO-8601 date-time: {value!r}"
        ) from exc
    if parsed.tzinfo is not None:
        raise argparse.ArgumentTypeError(
            f"the clock is local time and carries no timezone offset: {value!r}"
        )
    return parsed


def _case_ids(value: str) -> list[str]:
    """Split a comma-separated case list, ignoring blanks around commas."""
    return [part.strip() for part in value.split(",") if part.strip()]


def _compare(args: argparse.Namespace) -> None:
    """Compare two stored runs, write the comparison, and print its summary.

    The record goes under `<artifacts>/comparisons/<base>__<new>/` - never into either
    input run, since the new run may be the committed baseline under `specs/` or a run
    someone else will use as a baseline later, and a read must not add files to what it
    read (research R8).

    Raises: whatever stops a comparison, for `main` to report as a sentence.
    """
    base_dir = resolve_run(args.base, args.artifacts)
    new_dir = resolve_run(args.new, args.artifacts)
    cases = load_cases(args.labels, _SCHEMA)
    band = _resolve_band(args.band, args.artifacts) if args.band is not None else None
    comparison = compare(base_dir, new_dir, cases, band=band)
    written = (
        args.artifacts
        / COMPARISONS
        / (f"{comparison.base.run_id}__{comparison.new.run_id}")
    )
    written.mkdir(parents=True, exist_ok=True)
    write_atomically(written / COMPARISON_JSON, to_json(comparison))
    summary = render_comparison(comparison)
    write_atomically(written / COMPARISON_MD, summary)
    print(summary)


def _resolve_band(value: str, artifacts: Path) -> NoiseBand:
    """Find a band from a file path or a bare band id under `artifacts`.

    Raises: BandNotFoundError when `value` is neither.
    """
    given = Path(value)
    if given.is_file():
        return NoiseBand.model_validate_json(given.read_bytes())
    under_artifacts = artifacts / BANDS / f"{value}.json"
    if under_artifacts.is_file():
        return NoiseBand.model_validate_json(under_artifacts.read_bytes())
    raise BandNotFoundError(
        f"no band {value!r}: not a file, and not a band under {artifacts / BANDS}"
    )


def _band(args: argparse.Namespace) -> None:
    """Build a band from the five runs given, write it, and print what it observed.

    Raises: BandRefusedError when the five runs do not differ only by chance, and
        RunNotFoundError when one of them is not a run.
    """
    run_dirs = [resolve_run(value, args.artifacts) for value in args.runs]
    cases = load_cases(args.labels, _SCHEMA)
    band = build_band(run_dirs, cases)
    written = args.artifacts / BANDS
    written.mkdir(parents=True, exist_ok=True)
    write_atomically(written / f"{band.band_id}.json", to_json(band))
    print(render_band(band))
