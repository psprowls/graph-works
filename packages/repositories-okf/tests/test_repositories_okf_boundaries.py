"""Band 2, held mechanically: this package imports okf-io and okf-ext, and no other workspace package.

The root import-linter contract "Nothing below band 3 imports the application band" catches an edge
into core from here; this walk derives the sibling set from the filesystem so a package added later
is covered with no edit.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "repositories_okf"
PACKAGES_DIR = Path(__file__).resolve().parents[3] / "packages"
ALLOWED_SIBLINGS = {"okf_io", "okf_ext"}
PROCESS_MODULES = {"git.py"}
CLOCK_CALLS = {"now", "today", "utcnow"}


def modules() -> list[Path]:
    found = sorted(SRC.rglob("*.py"))
    assert found, f"no modules found under {SRC}"
    return found


def _is_type_checking_guard(node: ast.AST) -> bool:
    return isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"


def _runtime_nodes(node: ast.AST) -> list[ast.AST]:
    """Every node under *node* except those inside an `if TYPE_CHECKING:` body, which never executes."""
    found: list[ast.AST] = []
    for child in ast.iter_child_nodes(node):
        found.append(child)
        if _is_type_checking_guard(child):
            assert isinstance(child, ast.If)
            found.extend(n for orelse in child.orelse for n in [orelse, *_runtime_nodes(orelse)])
        else:
            found.extend(_runtime_nodes(child))
    return found


def import_roots(path: Path) -> set[str]:
    """Import roots executed at runtime; a `TYPE_CHECKING`-only import (a type annotation) is not one."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in _runtime_nodes(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def sibling_import_names() -> set[str]:
    names = {
        src_dir.name
        for pkg in PACKAGES_DIR.iterdir()
        if (pkg / "src").is_dir()
        for src_dir in (pkg / "src").iterdir()
        if src_dir.is_dir()
    }
    assert "repositories_okf" in names
    return names - {"repositories_okf"}


def test_the_sibling_walk_sees_more_than_the_allowed_pair() -> None:
    assert len(sibling_import_names() - ALLOWED_SIBLINGS) >= 2


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_no_module_imports_a_sibling_other_than_okf_io_and_okf_ext(module: Path) -> None:
    assert not (import_roots(module) & (sibling_import_names() - ALLOWED_SIBLINGS))


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_no_module_runs_processes_or_reads_the_clock(module: Path) -> None:
    """`init.install_bundle` takes `today=`; nothing here reads the clock or shells out to git."""
    assert not (import_roots(module) & {"datetime", "time"})
    if module.name not in PROCESS_MODULES:
        assert "subprocess" not in import_roots(module)
    tree = ast.parse(module.read_text(encoding="utf-8"))
    assert (
        not {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        & CLOCK_CALLS
    )


def test_a_type_checking_only_import_is_not_a_runtime_import(tmp_path: Path) -> None:
    source = tmp_path / "m.py"
    source.write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from datetime import date\nimport json\n",
        encoding="utf-8",
        newline="",
    )
    assert import_roots(source) == {"typing", "json"}
