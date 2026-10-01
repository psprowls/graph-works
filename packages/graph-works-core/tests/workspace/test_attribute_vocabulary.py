"""dispatch_config holds no attribute vocabulary of its own (design §3.1)."""

from __future__ import annotations

import ast
from pathlib import Path

import graph_works_core.workspace as workspace_pkg
from graph_works_core.workspace import dispatch, dispatch_config
from graph_works_core.workspace.layout import layout_for
from work_tracker_okf.pipeline import ATTRIBUTES as PIPELINE_ATTRIBUTES

WORKSPACE_DIR = Path(workspace_pkg.__file__).parent


def test_dispatch_exports_the_one_vocabulary() -> None:
    assert frozenset(PIPELINE_ATTRIBUTES) == dispatch.ATTRIBUTES
    assert "ATTRIBUTES" in dispatch.__all__


def test_dispatch_config_has_no_private_copy() -> None:
    assert not hasattr(dispatch_config, "_ATTRIBUTES")


def _spelled_attribute_sets(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        elements: list[ast.expr] = []
        if isinstance(node, ast.Set | ast.List | ast.Tuple):
            elements = list(node.elts)
        elif isinstance(node, ast.Dict):
            elements = [key for key in node.keys if key is not None]
        names = {e.value for e in elements if isinstance(e, ast.Constant) and isinstance(e.value, str)}
        if len(names & dispatch.ATTRIBUTES) >= 3:
            lines.append(node.lineno)
    return lines


def test_no_workspace_module_spells_the_attribute_names_as_a_set() -> None:
    offenders = {}
    for module in sorted(WORKSPACE_DIR.glob("*.py")):
        if module.name == "dispatch.py":
            continue
        found = _spelled_attribute_sets(ast.parse(module.read_text(encoding="utf-8")))
        if found:
            offenders[module.name] = found
    assert offenders == {}


def test_undeclared_attributes_default_to_the_vocabulary(tmp_path: Path) -> None:
    (tmp_path / "workspace.yaml").write_text(
        "version: 1\nworkflow:\n  dispatch_rules: dispatch.yaml\n", encoding="utf-8", newline=""
    )
    (tmp_path / "dispatch.yaml").write_text("{}\n", encoding="utf-8", newline="")
    assert dispatch_config.load_dispatch_config(layout_for(tmp_path)).attributes == dispatch.ATTRIBUTES
