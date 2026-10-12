"""Kernel source, test imports and dependency groups never depend on a slice."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
SLICES = frozenset({"gw_orchestrate", "gw_work", "gw_pipeline", "gw_wiki"})


def orchestrate_imports(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            target = node.module or ""
            targets = [target, *(f"{target}.{alias.name}" if target else alias.name for alias in node.names)]
        else:
            continue
        found.extend(target for target in targets if target.split(".")[0] in SLICES)
    return found


@pytest.mark.parametrize(
    "source",
    [
        "import gw_orchestrate as o",
        "from gw_orchestrate import commands",
        "import os, gw_pipeline.transactions as tx",
        "from gw_pipeline import finish, transactions as tx",
        "from .gw_pipeline import transactions",
        "from . import gw_pipeline as pipeline",
        "if TYPE_CHECKING:\n    import gw_pipeline",
        "import gw_wiki",
        "import gw_work",
    ],
)
def test_rejects_slice_imports(source: str) -> None:
    assert orchestrate_imports(source)


@pytest.mark.parametrize(
    "source", ["# import gw_orchestrate", "text = 'from gw_orchestrate import commands'", "import gw_orchestrate_other"]
)
def test_ignores_comments_and_strings(source: str) -> None:
    assert orchestrate_imports(source) == []


def test_kernel_source_and_tests_have_no_slice_imports() -> None:
    found = [
        f"{path}:{target}"
        for root in (PACKAGE / "src", PACKAGE / "tests")
        for path in root.rglob("*.py")
        for target in orchestrate_imports(path.read_text(encoding="utf-8"))
    ]
    assert found == []


def test_kernel_manifest_has_no_slice_dependency() -> None:
    data = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = [*data["project"]["dependencies"]]
    for group in data.get("dependency-groups", {}).values():
        dependencies.extend(entry for entry in group if isinstance(entry, str))
    for group in data["project"].get("optional-dependencies", {}).values():
        dependencies.extend(group)
    # Runtime, optional and dev edges all have the same downward permission.
    for root in SLICES:
        distribution = root.replace("_", "-")
        assert all(not entry.startswith(distribution) for entry in dependencies)
        assert distribution not in data.get("tool", {}).get("uv", {}).get("sources", {})
