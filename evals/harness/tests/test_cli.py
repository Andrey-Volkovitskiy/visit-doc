"""The command line: `run` drives and prints the summary, `score` needs only files."""

import shutil
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

import grpc
import httpx
import pytest
import shared_db
import sqlalchemy.ext.asyncio
from golden_harness import cli
from golden_harness.cases import Selection, SelectionKind
from golden_harness.driver.run import DEFAULT_CLOCK
from golden_harness.driver.session import ChatNotFoundError
from golden_harness.scoring.retrieval import LogContractError

_REPO = Path(__file__).resolve().parents[3]
_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "runs" / "us1"
_LABELS = _FIXTURE / "labels.json"


@pytest.fixture
def run_copy(tmp_path: Path) -> Path:
    copy = tmp_path / "artifacts" / "01K5US1RVN0000000000000000"
    shutil.copytree(_FIXTURE, copy)
    return copy


def _refuse(*_args: Any, **_kwargs: Any) -> NoReturn:
    raise AssertionError("scoring reached for the network or the database")


@pytest.fixture
def stack_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(httpx.Client, "send", _refuse)
    monkeypatch.setattr(httpx.AsyncClient, "send", _refuse)
    monkeypatch.setattr(grpc, "insecure_channel", _refuse)
    monkeypatch.setattr(grpc.aio, "insecure_channel", _refuse)
    monkeypatch.setattr(shared_db, "create_engine", _refuse)
    monkeypatch.setattr(sqlalchemy.ext.asyncio, "create_async_engine", _refuse)


def test_run_defaults() -> None:
    args = cli.parse_args(["run"])

    assert args.command == "run"
    assert args.artifacts == _REPO / ".run" / "evals"
    assert args.log == _REPO / ".run" / "chat.log"
    assert args.base_url == "http://localhost:8000"
    assert args.labels == _REPO / "evals" / "golden" / "cases.json"
    assert (args.resume, args.cases, args.family, args.clock) == (
        None,
        None,
        None,
        None,
    )


def test_run_accepts_a_case_list_a_family_and_a_clock() -> None:
    by_ids = cli.parse_args(
        ["run", "--cases", "G001,G042", "--clock", "2026-03-09T08:00"]
    )
    by_family = cli.parse_args(["run", "--family", "partial-serving"])

    assert by_ids.cases == ["G001", "G042"]
    assert by_ids.clock == datetime(2026, 3, 9, 8, 0)
    assert by_family.family == "partial-serving"


@pytest.mark.parametrize(
    "extra",
    [["--cases", "G001"], ["--family", "small-talk"], ["--clock", "2026-03-09T08:00"]],
)
def test_resume_is_exclusive_with_what_a_resumed_run_takes_from_run_json(
    extra: list[str],
) -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["run", "--resume", "01K5ANY", *extra])


def test_a_run_is_traced_unless_it_asks_not_to_be() -> None:
    assert cli.parse_args(["run"]).no_trace is False
    assert cli.parse_args(["run", "--no-trace"]).no_trace is True
    assert cli.parse_args(["run", "--family", "small-talk", "--no-trace"]).no_trace


def test_a_resumed_run_keeps_its_own_tracing_so_no_trace_is_refused_with_resume(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.parse_args(["run", "--resume", "01K5ANY", "--no-trace"])

    assert exited.value.code == 2
    err = capsys.readouterr().err
    assert "--no-trace" in err
    assert "--resume" in err


@pytest.mark.parametrize(
    "clock", ["2026-03-09T08:00+02:00", "2026-03-09T08:00Z", "soon"]
)
def test_a_clock_with_an_offset_or_no_date_time_is_refused(clock: str) -> None:
    # `local_now` carries no timezone: the service answers one with a 422, and the
    # fixture check compares it with naive starts, so it is refused before any turn.
    with pytest.raises(SystemExit):
        cli.parse_args(["run", "--clock", clock])


def test_cases_and_family_are_exclusive() -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["run", "--cases", "G001", "--family", "small-talk"])


def test_score_needs_a_run_and_defaults_to_the_golden_labels() -> None:
    args = cli.parse_args(["score", "--run", "01K5ANY"])

    assert args.command == "score"
    assert args.run == "01K5ANY"
    assert args.labels == _REPO / "evals" / "golden" / "cases.json"
    with pytest.raises(SystemExit):
        cli.parse_args(["score"])


@pytest.mark.usefixtures("stack_is_down")
def test_score_scores_a_run_path_from_files_alone(
    run_copy: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = cli.main(["score", "--run", str(run_copy), "--labels", str(_LABELS)])

    assert status == 0
    printed = capsys.readouterr().out
    assert "## Conditions" in printed
    assert "5 / 6" in printed
    assert (run_copy / "report.json").is_file()
    assert (run_copy / "report.md").is_file()


@pytest.mark.usefixtures("stack_is_down")
def test_score_resolves_a_bare_run_id_under_the_artifacts_directory(
    run_copy: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = cli.main(
        [
            "score",
            "--run",
            run_copy.name,
            "--artifacts",
            str(run_copy.parent),
            "--labels",
            str(_LABELS),
        ]
    )

    assert status == 0
    assert "## Conditions" in capsys.readouterr().out


def test_score_fails_on_a_run_that_does_not_exist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = cli.main(["score", "--run", "01K5NOSUCHRUN", "--artifacts", str(tmp_path)])

    assert status != 0
    assert "01K5NOSUCHRUN" in capsys.readouterr().err


class _Recorder:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def drive_run(self, *args: Any, **kwargs: Any) -> Path:
        self.calls.append(("drive_run", args, kwargs))
        return self.run_dir

    async def resume_run(self, *args: Any, **kwargs: Any) -> Path:
        self.calls.append(("resume_run", args, kwargs))
        return self.run_dir

    @asynccontextmanager
    async def live_stack(self, base_url: str) -> AsyncIterator[object]:
        self.calls.append(("live_stack", (base_url,), {}))
        yield object()


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch, run_copy: Path) -> _Recorder:
    recorder = _Recorder(run_copy)
    monkeypatch.setattr(cli, "drive_run", recorder.drive_run)
    monkeypatch.setattr(cli, "resume_run", recorder.resume_run)
    monkeypatch.setattr(cli, "live_stack", recorder.live_stack)
    return recorder


def test_run_drives_the_selection_and_prints_the_summary(
    recorder: _Recorder, run_copy: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = cli.main(
        [
            "run",
            "--cases",
            "G902,G901",
            "--labels",
            str(_LABELS),
            "--artifacts",
            str(run_copy.parent),
            "--log",
            "/tmp/chat.log",
            "--base-url",
            "http://chat.example:9000",
        ]
    )

    assert status == 0
    assert recorder.calls[0] == ("live_stack", ("http://chat.example:9000",), {})
    name, args, kwargs = recorder.calls[1]
    assert name == "drive_run"
    assert args[1] == Selection(kind=SelectionKind.IDS, case_ids=["G901", "G902"])
    assert kwargs["artifacts_dir"] == run_copy.parent
    assert kwargs["log_path"] == Path("/tmp/chat.log")
    assert kwargs["clock"] == DEFAULT_CLOCK
    assert kwargs["tracing_requested"] is True
    printed = capsys.readouterr().out
    assert "## Conditions" in printed
    assert (run_copy / "report.json").is_file()


def test_run_no_trace_asks_the_driver_for_an_untraced_run(
    recorder: _Recorder, run_copy: Path
) -> None:
    status = cli.main(
        [
            "run",
            "--no-trace",
            "--labels",
            str(_LABELS),
            "--artifacts",
            str(run_copy.parent),
        ]
    )

    assert status == 0
    name, _args, kwargs = recorder.calls[1]
    assert name == "drive_run"
    assert kwargs["tracing_requested"] is False


def test_run_resume_resolves_the_run_and_resumes_it(
    recorder: _Recorder, run_copy: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = cli.main(
        [
            "run",
            "--resume",
            run_copy.name,
            "--artifacts",
            str(run_copy.parent),
            "--labels",
            str(_LABELS),
        ]
    )

    assert status == 0
    name, args, _kwargs = recorder.calls[1]
    assert name == "resume_run"
    assert args[0] == run_copy
    assert "## Conditions" in capsys.readouterr().out


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("timed out"),
        ChatNotFoundError("no chat 01K5CHAT in session 01K5SESS"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_a_run_stopped_by_the_service_or_its_database_is_reported_not_raised(
    recorder: _Recorder,
    run_copy: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    async def stopped(*_args: Any, **_kwargs: Any) -> Path:
        raise error

    monkeypatch.setattr(cli, "drive_run", stopped)

    status = cli.main(["run", "--labels", str(_LABELS), "--artifacts", str(run_copy)])

    assert status == 1
    assert f"golden_harness: {type(error).__name__}: {error}" in capsys.readouterr().err


def test_a_record_the_log_contract_cannot_describe_is_reported_not_raised(
    run_copy: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Scoring refuses such a record by name, naming the case; the command line says so
    # in a sentence rather than a traceback, as for every other failure it names.
    error = LogContractError(
        "G901 segment 0: reranking both completed and was unavailable"
    )

    def refused(*_args: Any, **_kwargs: Any) -> NoReturn:
        raise error

    monkeypatch.setattr(cli, "score_run", refused)

    status = cli.main(["score", "--run", str(run_copy), "--labels", str(_LABELS)])

    assert status == 1
    assert f"golden_harness: LogContractError: {error}" in capsys.readouterr().err


# `compare` (spec 013): two stored runs in, a comparison written beside neither of
# them. A finding is never a failure - the exit status is 0 whatever it finds (FR-006)
# - and a refusal to compare at all is the CLI's existing reported-failure path.

_COMPARE = Path(__file__).resolve().parent / "fixtures" / "runs" / "compare"
_COMPARE_LABELS = _COMPARE / "labels.json"


def test_compare_needs_both_runs_and_defaults_to_the_golden_labels() -> None:
    args = cli.parse_args(["compare", "--base", "01K6A", "--new", "01K6B"])

    assert args.command == "compare"
    assert (args.base, args.new) == ("01K6A", "01K6B")
    assert args.band is None
    assert args.artifacts == _REPO / ".run" / "evals"
    assert args.labels == _REPO / "evals" / "golden" / "cases.json"
    with pytest.raises(SystemExit):
        cli.parse_args(["compare", "--base", "01K6A"])
    with pytest.raises(SystemExit):
        cli.parse_args(["compare", "--new", "01K6B"])


def _compare(tmp_path: Path, base: str, new: str, *extra: str) -> int:
    return cli.main(
        [
            "compare",
            "--base",
            str(_COMPARE / base),
            "--new",
            str(_COMPARE / new),
            "--labels",
            str(_COMPARE_LABELS),
            "--artifacts",
            str(tmp_path),
            *extra,
        ]
    )


@pytest.mark.usefixtures("stack_is_down")
def test_compare_finds_movement_and_still_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _compare(tmp_path, "base", "verdict")

    assert status == 0
    printed = capsys.readouterr().out
    assert "## Metrics" in printed
    assert "G802" in printed


@pytest.mark.usefixtures("stack_is_down")
def test_compare_writes_its_record_under_the_artifacts_directory(
    tmp_path: Path,
) -> None:
    _compare(tmp_path, "base", "verdict")

    written = sorted((tmp_path / "comparisons").iterdir())
    assert len(written) == 1
    assert "__" in written[0].name
    assert (written[0] / "comparison.json").is_file()
    assert (written[0] / "comparison.md").is_file()


@pytest.mark.usefixtures("stack_is_down")
def test_compare_writes_nothing_into_either_input_run(tmp_path: Path) -> None:
    before = {
        name: sorted(path.name for path in (_COMPARE / name).rglob("*"))
        for name in ("base", "verdict")
    }

    _compare(tmp_path, "base", "verdict")

    for name, listing in before.items():
        assert sorted(path.name for path in (_COMPARE / name).rglob("*")) == listing


@pytest.mark.usefixtures("stack_is_down")
def test_compare_resolves_bare_run_ids_under_the_artifacts_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifacts = tmp_path / "artifacts"
    for name in ("base", "verdict"):
        shutil.copytree(_COMPARE / name, artifacts / name)

    status = cli.main(
        [
            "compare",
            "--base",
            "base",
            "--new",
            "verdict",
            "--labels",
            str(_COMPARE_LABELS),
            "--artifacts",
            str(artifacts),
        ]
    )

    assert status == 0
    assert "## Metrics" in capsys.readouterr().out


@pytest.mark.usefixtures("stack_is_down")
def test_a_label_digest_difference_exits_one_naming_the_cases(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _compare(tmp_path, "base", "labels")

    assert status == 1
    printed = capsys.readouterr().err
    assert "G802" in printed
    assert "Traceback" not in printed


@pytest.mark.usefixtures("stack_is_down")
def test_two_runs_with_no_case_in_common_exit_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    one = _only_cases(tmp_path / "one", ["G801"])
    other = _only_cases(tmp_path / "other", ["G802"])

    status = cli.main(
        [
            "compare",
            "--base",
            str(one),
            "--new",
            str(other),
            "--labels",
            str(_COMPARE_LABELS),
            "--artifacts",
            str(tmp_path / "artifacts"),
        ]
    )

    assert status == 1
    printed = capsys.readouterr().err
    assert "no case in common" in printed
    assert "Traceback" not in printed


def _only_cases(destination: Path, keep: list[str]) -> Path:
    """Copy the baseline fixture run, keeping only the named cases' records."""
    shutil.copytree(_COMPARE / "base", destination)
    for record in (destination / "cases").iterdir():
        if record.stem not in keep:
            record.unlink()
    return destination


@pytest.mark.usefixtures("stack_is_down")
def test_a_missing_run_directory_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = cli.main(
        [
            "compare",
            "--base",
            "01K6NOSUCHRUN",
            "--new",
            str(_COMPARE / "base"),
            "--labels",
            str(_COMPARE_LABELS),
            "--artifacts",
            str(tmp_path),
        ]
    )

    assert status == 1
    assert "01K6NOSUCHRUN" in capsys.readouterr().err


# `band` (spec 013): five stored runs in, one band out, and every refusal a named
# sentence. The band is written under the artifacts directory, as a comparison is.

_BAND = Path(__file__).resolve().parent / "fixtures" / "runs" / "band"
_BAND_LABELS = _BAND / "labels.json"
_BAND_FIVE = "run1,run2,run3,run4,run5"


def _band(tmp_path: Path, runs: str) -> int:
    return cli.main(
        [
            "band",
            "--runs",
            ",".join(str(_BAND / name) for name in runs.split(",")),
            "--labels",
            str(_BAND_LABELS),
            "--artifacts",
            str(tmp_path),
        ]
    )


def test_band_needs_its_runs_and_defaults_to_the_golden_labels() -> None:
    args = cli.parse_args(["band", "--runs", "01A,01B,01C,01D,01E"])

    assert args.command == "band"
    assert args.runs == ["01A", "01B", "01C", "01D", "01E"]
    assert args.labels == _REPO / "evals" / "golden" / "cases.json"
    with pytest.raises(SystemExit):
        cli.parse_args(["band"])


@pytest.mark.usefixtures("stack_is_down")
def test_a_built_band_exits_zero_and_is_written_under_the_artifacts_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _band(tmp_path, _BAND_FIVE)

    assert status == 0
    (written,) = sorted((tmp_path / "bands").iterdir())
    assert written.suffix == ".json"
    printed = capsys.readouterr().out
    assert "five observations" in printed
    assert written.stem in printed


@pytest.mark.usefixtures("stack_is_down")
def test_a_band_from_four_runs_exits_one_naming_the_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _band(tmp_path, "run1,run2,run3,run4")

    assert status == 1
    printed = capsys.readouterr().err
    assert "4" in printed and "Traceback" not in printed


@pytest.mark.usefixtures("stack_is_down")
@pytest.mark.parametrize(
    ("fifth", "named"),
    [
        ("other-conditions", "rerank_floor"),
        ("other-corpus", "corpus"),
        ("other-labels", "G802"),
        ("other-cases", "G806"),
        ("narrowed", "part of it"),
    ],
)
def test_each_band_refusal_exits_one_with_its_field_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], fifth: str, named: str
) -> None:
    status = _band(tmp_path, f"run1,run2,run3,run4,{fifth}")

    assert status == 1
    assert named in capsys.readouterr().err


@pytest.mark.usefixtures("stack_is_down")
def test_compare_marks_movements_against_a_band_given_by_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _band(tmp_path, _BAND_FIVE) == 0
    (written,) = sorted((tmp_path / "bands").iterdir())
    capsys.readouterr()

    status = cli.main(
        [
            "compare",
            "--base",
            str(_BAND / "run1"),
            "--new",
            str(_BAND / "run2"),
            "--band",
            written.stem,
            "--labels",
            str(_BAND_LABELS),
            "--artifacts",
            str(tmp_path),
        ]
    )

    assert status == 0
    printed = capsys.readouterr().out
    assert "within the observed range" in printed
    assert written.stem in printed


@pytest.mark.usefixtures("stack_is_down")
def test_compare_accepts_a_band_given_by_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _band(tmp_path, _BAND_FIVE) == 0
    (written,) = sorted((tmp_path / "bands").iterdir())
    capsys.readouterr()

    status = cli.main(
        [
            "compare",
            "--base",
            str(_BAND / "run1"),
            "--new",
            str(_BAND / "run2"),
            "--band",
            str(written),
            "--labels",
            str(_BAND_LABELS),
            "--artifacts",
            str(tmp_path / "elsewhere"),
        ]
    )

    assert status == 0
    assert "observed range" in capsys.readouterr().out


@pytest.mark.usefixtures("stack_is_down")
def test_a_band_that_does_not_exist_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = cli.main(
        [
            "compare",
            "--base",
            str(_BAND / "run1"),
            "--new",
            str(_BAND / "run2"),
            "--band",
            "01K6NOSUCHBAND",
            "--labels",
            str(_BAND_LABELS),
            "--artifacts",
            str(tmp_path),
        ]
    )

    assert status == 1
    assert "01K6NOSUCHBAND" in capsys.readouterr().err


@pytest.mark.usefixtures("stack_is_down")
def test_a_comparison_without_a_band_marks_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _compare(tmp_path, "base", "verdict")

    assert status == 0
    printed = capsys.readouterr().out
    assert "observed range" not in printed
    assert "No band was supplied" in printed
