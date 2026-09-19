"""Mechanical guards for graph-works-wire's boundary.

Wire is the one home of every typed-result -> plain-data projection, shared by
every interface. It must stay pure: no interface framework, no I/O, no
encoding (each interface encodes for itself). import-linter covers the
interface packages; this walk also catches what grimp cannot see -- stdlib
modules, and I/O reached through a `Path` method.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "graph_works_wire"
MODULE_NAMES = {
    "events.py",
    "__init__.py",
    "_jsonable.py",
    "agent_config.py",
    "config.py",
    "errors.py",
    "util.py",
    "wiki.py",
    "work.py",
}
FORBIDDEN_ROOTS = {
    "graph_works_cli",
    "graph_works_serve",
    "typer",
    "click",
    "starlette",
    "uvicorn",
    "json",
    "os",
    "shutil",
    "subprocess",
    "io",
}
FORBIDDEN_CALLS = {"open", "print", "input", "exec", "eval"}
FORBIDDEN_METHODS = {
    "read_text",
    "read_bytes",
    "write_text",
    "write_bytes",
    "open",
    "mkdir",
    "unlink",
    "rmdir",
    "touch",
    "iterdir",
    "glob",
    "rglob",
    "exists",
    "is_file",
    "is_dir",
    "stat",
    "resolve",
}


def modules() -> list[Path]:
    found = sorted(SRC.rglob("*.py"))
    assert found
    return found


def tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_module_set_is_complete() -> None:
    """A new module is a decision about the package's shape; name it here."""
    assert {path.name for path in modules()} == MODULE_NAMES


@pytest.mark.parametrize("path", modules(), ids=lambda path: path.name)
def test_no_forbidden_import(path: Path) -> None:
    roots: set[str] = set()
    for node in ast.walk(tree(path)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{path.name}: relative import"
            if node.module:
                roots.add(node.module.split(".")[0])
    assert not roots & FORBIDDEN_ROOTS, f"{path.name} imports {sorted(roots & FORBIDDEN_ROOTS)}"


@pytest.mark.parametrize("path", modules(), ids=lambda path: path.name)
def test_no_io_calls(path: Path) -> None:
    for node in ast.walk(tree(path)):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            assert node.func.id not in FORBIDDEN_CALLS, f"{path.name}:{node.lineno} calls {node.func.id}()"
        elif isinstance(node.func, ast.Attribute):
            assert node.func.attr not in FORBIDDEN_METHODS, f"{path.name}:{node.lineno} calls .{node.func.attr}()"
