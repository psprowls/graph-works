"""The epic's checkable acceptance bar, as a test.

On *imports*, not on mentions: the landed packages name the retired modules in
provenance docstrings on purpose, and a grep for the bare name would fail on
prose that is doing its job.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RETIRED = {"graph_wiki_core", "wiki_io", "workspace_io", "model_adapter"}
LANGCHAIN_DECLARERS = {"graph-works-core", "models-io", "subagents-io"}


def _imported_roots(source: Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_workspace_has_source_trees_to_walk():
    # A guard on the guard: a wrong REPO_ROOT would make every check below
    # vacuously pass.
    assert len(list(REPO_ROOT.glob("packages/*/src"))) >= 10


def test_nothing_in_the_rebuild_imports_a_retired_package():
    offenders = {
        str(source.relative_to(REPO_ROOT)): sorted(_imported_roots(source) & RETIRED)
        for source in sorted(REPO_ROOT.glob("packages/*/src/**/*.py"))
        if _imported_roots(source) & RETIRED
    }
    assert offenders == {}


def test_langchain_is_declared_by_three_packages_only():
    declarers = set()
    for pyproject in sorted(REPO_ROOT.glob("packages/*/pyproject.toml")):
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project = data["project"]
        declared = list(project.get("dependencies", []))
        for extra in project.get("optional-dependencies", {}).values():
            declared.extend(extra)
        if any("langchain" in requirement for requirement in declared):
            declarers.add(project["name"])
    assert declarers <= LANGCHAIN_DECLARERS
    assert "graph-works-core" in declarers
