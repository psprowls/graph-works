"""Static read-index imports belong at kernel's session seam and wiki's utility owner.

Ancestor imports (`import okf_ext` and `from okf_ext import *`) also admit
producer access and are forbidden outside the seam. Dynamic imports and
subsequent attribute access through other aliases are outside this AST guard.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGES = Path(__file__).resolve().parents[2]
SRC_ROOTS = tuple(
    PACKAGES / name / "src"
    for name in (
        "gw-kernel",
        "gw-cli",
        "gw-serve",
        "gw-wire",
        "gw-orchestrate",
        "gw-work",
        "gw-pipeline",
        "gw-wiki",
    )
)
CORE = PACKAGES / "gw-kernel" / "src" / "gw_kernel"
WIKI = PACKAGES / "gw-wiki" / "src" / "gw_wiki"
PRODUCER = "okf_ext.readindex"


def _identity(path: Path) -> str:
    repo = PACKAGES.parent
    return path.relative_to(repo).as_posix() if path.is_relative_to(repo) else str(path)


def _imports_read_index(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name in ("okf_ext", PRODUCER) or alias.name.startswith(PRODUCER + ".") for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.level == 0:
        module = node.module or ""
        return (
            module == PRODUCER
            or module.startswith(PRODUCER + ".")
            or (module == "okf_ext" and any(alias.name in ("readindex", "*") for alias in node.names))
        )
    return False


def _admitted(path: Path) -> bool:
    return (path.parent == CORE / "read_session" and path.suffix == ".py") or path == WIKI / "util" / "read_index.py"


def _offenders(paths: list[Path]) -> list[str]:
    return [
        f"{_identity(path)}:{node.lineno} {ast.unparse(node)}"
        for path in paths
        if not _admitted(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        if isinstance(node, (ast.Import, ast.ImportFrom)) and _imports_read_index(node)
    ]


def test_only_kernel_read_session_and_wiki_util_import_read_index() -> None:
    paths = sorted(path for root in SRC_ROOTS for path in root.rglob("*.py"))
    assert len(paths) > 100, "the walk is looking in the wrong place"
    for root in SRC_ROOTS:
        assert list(root.rglob("*.py")), f"source root missing or empty: {root}"
    assert not (found := _offenders(paths)), "\n".join(found)


@pytest.mark.parametrize(
    "source",
    [
        "import okf_ext.readindex",
        "import okf_ext.readindex as index",
        "import os, okf_ext.readindex as index",
        "import okf_ext.readindex.model as model",
        "import okf_ext",
        "import okf_ext as ext",
        "from okf_ext import readindex",
        "from okf_ext import readindex as index, bundle",
        "from okf_ext import *",
        "from okf_ext.readindex import open_index as opener",
        "from okf_ext.readindex import *",
        "from okf_ext.readindex.model import IndexRow as Row",
        "if False:\n    import okf_ext.readindex as index",
    ],
)
def test_boundary_catches_static_import_forms(tmp_path: Path, source: str) -> None:
    path = tmp_path / "injected.py"
    path.write_text(source + "\n", encoding="utf-8", newline="")
    found = _offenders([path])
    assert len(found) == 1
    assert found[0].startswith(f"{path}:")


@pytest.mark.parametrize(
    "source",
    [
        "# import okf_ext.readindex\nx = 1",
        "text = 'from okf_ext import readindex'",
        "import okf_ext.readindex_other as other",
        "from okf_ext.readindex_other import Row",
        "from okf_ext import bundle as readindex",
        "from .okf_ext import readindex",
    ],
)
def test_boundary_ignores_unrelated_syntax(tmp_path: Path, source: str) -> None:
    path = tmp_path / "unrelated.py"
    path.write_text(source + "\n", encoding="utf-8", newline="")
    assert _offenders([path]) == []


@pytest.mark.parametrize("relative", ["read_session/__init__.py", "read_session/index.py"])
def test_boundary_admits_only_named_core_seams(relative: str) -> None:
    assert _admitted(CORE / relative)


@pytest.mark.parametrize(
    "path",
    [
        CORE / "read_session" / "nested" / "index.py",
        CORE / "read_session_extra" / "index.py",
        CORE / "read_session" / "index.txt",
        CORE / "util" / "read_index_extra.py",
        CORE / "workspace" / "read_index.py",
        SRC_ROOTS[1] / "gw_cli" / "read_session" / "index.py",
        SRC_ROOTS[2] / "gw_serve" / "util" / "read_index.py",
        SRC_ROOTS[3] / "gw_wire" / "read_session" / "index.py",
    ],
)
def test_boundary_rejects_other_locations(path: Path) -> None:
    assert not _admitted(path)


@pytest.mark.parametrize(
    "package,module", [("gw-orchestrate", "gw_orchestrate"), ("gw-pipeline", "gw_pipeline"), ("gw-wiki", "gw_wiki")]
)
def test_slice_read_index_import_is_rejected(tmp_path, package, module):
    path = tmp_path / f"packages/{package}/src/{module}/injected.py"
    path.parent.mkdir(parents=True)
    path.write_text("import okf_ext.readindex\n", encoding="utf-8", newline="")
    assert _offenders([path]) == [f"{path}:1 import okf_ext.readindex"]


def test_boundary_admits_wiki_util_exactly() -> None:
    assert _admitted(WIKI / "util/read_index.py")
    assert not _admitted(CORE / "util/read_index.py")
    assert not _admitted(WIKI / "workspace/read_index.py")
    assert not _admitted(WIKI / "util/read_index_extra.py")
