"""The half of the internal boundary import-linter cannot express: verified
against import-linter 2.13, grimp does not report an import of an *ancestor*
package as a dependency, so a `forbidden` contract naming
`gw_kernel` as `forbidden_modules` would report KEPT even if a
vertical wrote `from gw_kernel import WorkspaceLayout`. The `layers`
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

SRC = Path(__file__).resolve().parents[1] / "src" / "gw_kernel"


def _source_modules() -> list[Path]:
    return sorted(
        path
        for root in (
            SRC,
            SRC.parents[2] / "gw-orchestrate/src/gw_orchestrate",
            SRC.parents[2] / "gw-work/src/gw_work",
            SRC.parents[2] / "gw-pipeline/src/gw_pipeline",
            SRC.parents[2] / "gw-wiki/src/gw_wiki",
        )
        for path in root.rglob("*.py")
    )


def _module_id(path: Path) -> str:
    return path.relative_to(SRC.parents[3]).as_posix()


def test_no_module_imports_the_top_level_package() -> None:
    """Would invert the re-export direction: the top-level `__init__.py`
    aggregates every layer already, so a submodule importing it back would
    make every vertical load every other vertical through the front door.

    Only the bare top-level package name is forbidden. `from
    gw_kernel.workspace import layout` is a legal downward import and
    must not be flagged -- the rule is about the ancestor package itself, not
    its submodules.
    """
    offenders: list[str] = []
    for path in _source_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "gw_kernel":
                offenders.append(f"{_module_id(path)}:{node.lineno} from gw_kernel import ...")
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{_module_id(path)}:{node.lineno} import {alias.name}"
                    for alias in node.names
                    if alias.name == "gw_kernel"
                )
    assert not offenders, "\n".join(offenders)


PACKAGES = SRC.parents[2]
DISPLAY_CACHE = "gw_kernel.workspace.display_cache"
WIKI = PACKAGES / "gw-wiki/src/gw_wiki"
DISPLAY_CACHE_IMPORTERS = frozenset(
    {
        SRC / "workspace/repo_files.py",
        SRC / "workspace/citations.py",
        WIKI / "wiki_page/citations.py",
        WIKI / "code_read/commands.py",
    }
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
        ("gw_serve/routes.py", "import gw_kernel.workspace.display_cache as cache"),
        ("gw_serve/routes.py", "from gw_kernel.workspace.display_cache import open_display_cache"),
        ("gw_serve/routes.py", "from gw_kernel.workspace import display_cache as cache"),
        ("gw_pipeline/transactions.py", "from gw_kernel.workspace import display_cache"),
        ("gw_wiki/query/commands.py", "from gw_kernel.workspace import display_cache"),
        ("gw_pipeline/transactions.py", "from gw_kernel.workspace.display_cache import open_display_cache"),
        ("gw_kernel/example_vertical/commands.py", "from ..workspace import display_cache"),
        ("gw_kernel/example_vertical/commands.py", "from ..workspace.display_cache import open_display_cache"),
        ("gw_kernel/workspace/__init__.py", "from . import display_cache"),
        ("gw_kernel/example_vertical/__init__.py", "from ..workspace import display_cache"),
    ],
)
def test_display_cache_guard_catches_injected_imports(tmp_path: Path, module: str, source: str) -> None:
    path = tmp_path / "src" / module
    path.parent.mkdir(parents=True)
    path.write_text(source + "\n", encoding="utf-8", newline="")
    assert _display_cache_offenders([path]) == [f"{path}:1"]


def test_display_cache_guard_ignores_comments_and_other_imports(tmp_path: Path) -> None:
    path = tmp_path / "src" / "gw_kernel" / "workspace" / "transactions.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "# from . import display_cache\nfrom . import layout\nfrom .layout import WorkspaceLayout\n",
        encoding="utf-8",
        newline="",
    )
    assert _display_cache_offenders([path]) == []


@pytest.mark.parametrize(
    "package,module", [("gw-orchestrate", "gw_orchestrate"), ("gw-pipeline", "gw_pipeline"), ("gw-wiki", "gw_wiki")]
)
def test_slice_front_door_import_is_rejected(tmp_path, monkeypatch, package, module):
    path = tmp_path / f"packages/{package}/src/{module}/injected.py"
    path.parent.mkdir(parents=True)
    path.write_text("from gw_kernel import workspace\n", encoding="utf-8", newline="")
    monkeypatch.setattr(__import__(__name__), "_source_modules", lambda: [path])
    monkeypatch.setattr(__import__(__name__), "_module_id", lambda p: p.relative_to(tmp_path).as_posix())
    with pytest.raises(AssertionError, match=package):
        test_no_module_imports_the_top_level_package()
