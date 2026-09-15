"""Scoring is a pure function of a stored run: no scoring module imports a client."""

import ast
from pathlib import Path

import golden_harness.scoring
import pytest

_SCORING = Path(golden_harness.scoring.__file__).parent
_FORBIDDEN = ("httpx", "grpc", "sqlalchemy", "golden_harness.driver")


def _modules() -> list[Path]:
    return sorted(_SCORING.glob("*.py"))


def _imported_names(source: Path) -> list[str]:
    package = "golden_harness.scoring"
    names: list[str] = []
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parent = package.rsplit(".", node.level - 1)[0]
                base = f"{parent}.{base}" if base else parent
            names.append(base)
            names.extend(f"{base}.{alias.name}" for alias in node.names)
    return names


def _forbidden(name: str) -> bool:
    return any(name == root or name.startswith(f"{root}.") for root in _FORBIDDEN)


def test_the_scoring_modules_this_phase_needs_exist() -> None:
    assert {"alignment.py", "metric.py"} <= {path.name for path in _modules()}


@pytest.mark.parametrize("module", _modules(), ids=lambda path: path.name)
def test_a_scoring_module_imports_no_client_and_nothing_from_the_driver(
    module: Path,
) -> None:
    offending = [name for name in _imported_names(module) if _forbidden(name)]

    assert offending == []


@pytest.mark.parametrize(
    "statement",
    [
        "import httpx",
        "import grpc.aio",
        "from sqlalchemy.ext.asyncio import AsyncSession",
        "from golden_harness.driver.logslice import read_slice",
        "from golden_harness import driver",
        "from ..driver import run",
        "from .. import driver",
    ],
)
def test_the_check_recognises_every_way_of_importing_a_forbidden_name(
    tmp_path: Path, statement: str
) -> None:
    source = tmp_path / "probe.py"
    source.write_text(statement + "\n")

    assert any(_forbidden(name) for name in _imported_names(source))
