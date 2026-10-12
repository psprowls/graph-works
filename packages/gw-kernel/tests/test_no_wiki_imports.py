"""Kernel source and tests never import the wiki slice, including ancestors."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
ROOTS = (PACKAGE / "src", PACKAGE / "tests")


def _offenders(paths: list[Path]) -> list[str]:
    found: list[str] = []
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                targets = [module, *(f"{module}.{alias.name}" if module else alias.name for alias in node.names)]
            else:
                continue
            if any(target.split(".")[0] == "gw_wiki" for target in targets):
                found.append(f"{path}:{node.lineno} wiki import")
    return found


@pytest.mark.parametrize("root", ["src", "tests"])
@pytest.mark.parametrize(
    "source",
    [
        "import gw_wiki",
        "import gw_wiki.query.commands as query",
        "from gw_wiki import query",
        "from gw_wiki.query import commands",
        "from . import gw_wiki as wiki",
        "from .gw_wiki import query",
        "if TYPE_CHECKING:\n    from gw_wiki import query",
    ],
)
def test_each_kernel_root_rejects_planted_import(tmp_path: Path, root: str, source: str) -> None:
    path = tmp_path / root / "injected.py"
    path.parent.mkdir(parents=True)
    path.write_text(source + "\n", encoding="utf-8", newline="")
    found = _offenders([path])
    assert len(found) == 1
    assert found[0].startswith(f"{path}:")


@pytest.mark.parametrize(
    "source",
    [
        "from gw_kernel.workspace import layout",
        "from okf_io import Document",
        "text = 'from gw_wiki import query'",
        "# import gw_wiki",
        "import gw_wiki_other",
    ],
)
def test_downward_imports_and_string_literals_are_not_wiki_imports(tmp_path: Path, source: str) -> None:
    path = tmp_path / "legal.py"
    path.write_text(source + "\n", encoding="utf-8", newline="")
    assert _offenders([path]) == []


def test_shipped_kernel_source_and_tests_do_not_import_wiki() -> None:
    for root in ROOTS:
        paths = sorted(root.rglob("*.py"))
        assert paths
        assert _offenders(paths) == []
