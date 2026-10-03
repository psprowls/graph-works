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
from importlib.util import resolve_name
from pathlib import Path

import pytest

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


PACKAGES = SRC.parents[2]
DISPLAY_CACHE = "graph_works_core.workspace.display_cache"
DISPLAY_CACHE_IMPORTERS = frozenset(
    SRC / module
    for module in (
        "workspace/repo_files.py",
        "wiki_page/citations.py",
        "code_read/commands.py",
        "workspace/citations.py",
    )
)


def _display_cache_offenders(paths: list[Path]) -> list[str]:
    found: list[str] = []
    for path in paths:
        if path in DISPLAY_CACHE_IMPORTERS:
            continue
        source_root = next(parent for parent in path.parents if parent.name == "src")
        package = ".".join(path.parent.relative_to(source_root).parts)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    module = resolve_name("." * node.level + module, package)
                targets = [module, *(f"{module}.{alias.name}" for alias in node.names)]
            else:
                continue
            if any(target == DISPLAY_CACHE or target.startswith(DISPLAY_CACHE + ".") for target in targets):
                found.append(f"{path}:{node.lineno}")
    return found


def test_only_display_read_modules_import_display_cache() -> None:
    paths = sorted(path for root in PACKAGES.glob("*/src") for path in root.rglob("*.py"))
    assert len(paths) > 100, "the walk is looking in the wrong place"
    offenders = _display_cache_offenders(paths)
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize(
    ("module", "source"),
    [
        ("graph_works_serve/routes.py", "import graph_works_core.workspace.display_cache as cache"),
        ("graph_works_serve/routes.py", "from graph_works_core.workspace.display_cache import open_display_cache"),
        ("graph_works_serve/routes.py", "from graph_works_core.workspace import display_cache as cache"),
        ("graph_works_core/workspace/transactions.py", "from . import display_cache"),
        ("graph_works_core/workspace/transactions.py", "from .display_cache import open_display_cache"),
        ("graph_works_core/work/commands.py", "from ..workspace import display_cache"),
        ("graph_works_core/work/commands.py", "from ..workspace.display_cache import open_display_cache"),
        ("graph_works_core/workspace/__init__.py", "from . import display_cache"),
        ("graph_works_core/work/__init__.py", "from ..workspace import display_cache"),
    ],
)
def test_display_cache_guard_catches_injected_imports(tmp_path: Path, module: str, source: str) -> None:
    path = tmp_path / "src" / module
    path.parent.mkdir(parents=True)
    path.write_text(source + "\n", encoding="utf-8", newline="")
    assert _display_cache_offenders([path]) == [f"{path}:1"]


def test_display_cache_guard_ignores_comments_and_other_imports(tmp_path: Path) -> None:
    path = tmp_path / "src" / "graph_works_core" / "workspace" / "transactions.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "# from . import display_cache\nfrom . import layout\nfrom .layout import WorkspaceLayout\n",
        encoding="utf-8",
        newline="",
    )
    assert _display_cache_offenders([path]) == []
