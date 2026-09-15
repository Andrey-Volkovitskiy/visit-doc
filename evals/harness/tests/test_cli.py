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
    printed = capsys.readouterr().out
    assert "## Conditions" in printed
    assert (run_copy / "report.json").is_file()


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
