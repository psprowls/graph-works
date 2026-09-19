"""graph-works-serve's import boundary, walked by AST (grimp misses some shapes).

`graph_works_cli`, `typer` and `click` never appear in src. Starlette is
confined to `app.py`, uvicorn to `main.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "graph_works_serve"
FORBIDDEN = {"graph_works_cli", "typer", "click"}
CONFINED = {"starlette": {"app.py"}, "uvicorn": {"main.py"}}


def _roots(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


MODULES = sorted(SRC.glob("*.py"))


@pytest.mark.parametrize("module", MODULES, ids=lambda p: p.name)
def test_no_interface_or_cli_framework(module: Path) -> None:
    assert not (_roots(module) & FORBIDDEN)


@pytest.mark.parametrize("module", MODULES, ids=lambda p: p.name)
def test_frameworks_stay_in_their_module(module: Path) -> None:
    for root, allowed in CONFINED.items():
        if root in _roots(module):
            assert module.name in allowed, f"{root} imported from {module.name}"


def test_the_walk_sees_modules() -> None:
    assert {"context.py", "__init__.py"} <= {p.name for p in MODULES}


def test_only_watch_imports_watchfiles() -> None:
    importers = {path.name for path in SRC.rglob("*.py") if "watchfiles" in _roots(path)}
    assert importers == {"watch.py"}


@pytest.mark.parametrize("name", ["coalesce.py", "hub.py", "sse.py"])
def test_change_stream_cores_are_framework_free(name: str) -> None:
    assert not _roots(SRC / name) & {"starlette", "uvicorn", "watchfiles", "graph_works_cli"}
