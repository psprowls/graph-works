"""The half of the internal boundary import-linter cannot express: verified
against import-linter 2.13, grimp does not report an import of an *ancestor*
package as a dependency, so a `forbidden` contract naming
`graph_works_core` as `forbidden_modules` would report KEPT even if a
vertical wrote `from graph_works_core import ArchiveRun`. The `layers`
contract in the root `pyproject.toml` covers the direction that tool
provably does catch — a lower layer importing a higher one — and this test
covers the one it cannot: any module, at any layer, naming its own ancestor
package.

Mirrors `packages/okf-ext/tests/test_ext_boundaries.py`'s
`test_no_capability_imports_the_top_level_package`, scoped to the one rule
this reorg's design spec asks for.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "graph_works_core"


def _source_modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _module_id(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def test_no_module_imports_the_top_level_package() -> None:
    """Would invert the re-export direction: the top-level `__init__.py`
    aggregates every layer already, so a submodule importing it back would
    make every vertical load every other vertical through the front door.

    Only the bare top-level package name is forbidden. `from
    graph_works_core.workspace import layout` is a legal downward import and
    must not be flagged -- the rule is about the ancestor package itself, not
    its submodules.
    """
    offenders: list[str] = []
    for path in _source_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "graph_works_core":
                offenders.append(f"{_module_id(path)}:{node.lineno} from graph_works_core import ...")
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{_module_id(path)}:{node.lineno} import {alias.name}"
                    for alias in node.names
                    if alias.name == "graph_works_core"
                )
    assert not offenders, "\n".join(offenders)
