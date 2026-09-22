"""Only `chat.observability` may import the tracing SDK.

Agent, retrieval and API code call its domain-shaped functions and never the provider's
own API, the same dependency rule the model provider is held to. Walked with `ast`
rather than grepped, so an import inside a function or a `from ... import` is caught
as surely as one at the top of a module.
"""

import ast
from pathlib import Path

_CHAT_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "chat"
_OBSERVABILITY = _CHAT_PACKAGE / "observability"
_FORBIDDEN = ("langfuse", "opentelemetry")


def _imported_modules(path: Path) -> list[str]:
    """Return every module `path` imports, at any depth of the file."""
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return modules


def _is_forbidden(module: str) -> bool:
    return any(module == root or module.startswith(f"{root}.") for root in _FORBIDDEN)


def test_only_the_observability_package_imports_the_tracing_sdk() -> None:
    offenders = [
        f"{path.relative_to(_CHAT_PACKAGE)}: {module}"
        for path in sorted(_CHAT_PACKAGE.rglob("*.py"))
        if _OBSERVABILITY not in path.parents
        for module in _imported_modules(path)
        if _is_forbidden(module)
    ]

    assert offenders == []


def test_the_observability_package_exists_and_does_import_it() -> None:
    # Guards the walk itself: a moved package would leave the boundary test above
    # passing over a tree that no longer contains the only permitted importer.
    modules = [
        module
        for path in _OBSERVABILITY.rglob("*.py")
        for module in _imported_modules(path)
    ]

    assert any(_is_forbidden(module) for module in modules)
