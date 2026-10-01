"""Core's phase sets are the stage table's, and no core module spells one out (design §3.1, §5)."""

from __future__ import annotations

import ast
from pathlib import Path

from graph_works_core.guidance import claims as guidance_claims
from graph_works_core.orchestrate import claims as orchestrate_claims
from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.orchestrate import dispatch, stage_advance, workspace_prepare
from graph_works_core.workspace import dispatch_config, provenance
from work_tracker_okf.pipeline import (
    PACKAGED_DEFINITION,
    STAGE_TABLE,
    code_phases,
    dispatch_phases,
    phase_order,
    read_only_phases,
    results_phases,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "graph_works_core"
PHASE_NAMES = frozenset(phase_order())


def test_each_constant_equals_its_accessor() -> None:
    assert read_only_phases() == orchestrate.READ_ONLY_PHASES
    assert results_phases() == stage_advance.RESULTS_PHASES
    assert code_phases() == orchestrate_claims.CODE_WRITE_PHASES
    assert code_phases() == workspace_prepare._ITEM_PHASES
    assert code_phases() == dispatch._CODE_PHASES
    assert tuple(PACKAGED_DEFINITION.artifacts) == dispatch_config._ARTIFACT_STAGES
    assert dispatch_phases() == orchestrate.DISPATCH_PHASES
    assert dispatch_phases() == provenance.ACTIVE_WORK_PHASES
    assert tuple(row.stage for row in STAGE_TABLE) == guidance_claims.PIPELINE_PHASES


def test_read_only_and_results_are_complements_on_the_table() -> None:
    stages = frozenset(row.stage for row in STAGE_TABLE)
    assert read_only_phases() & results_phases() == frozenset()
    assert read_only_phases() | results_phases() == stages


def _literal_names(node: ast.AST) -> list[str] | None:
    """The string elements of a set / tuple / list literal, or of `frozenset({...})`."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"frozenset", "set"}:
        return _literal_names(node.args[0]) if len(node.args) == 1 else None
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return None


def _inside_literal_annotation(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    parent = parents.get(node)
    return (
        isinstance(parent, ast.Subscript)
        and isinstance(parent.value, (ast.Name, ast.Attribute))
        and (parent.value.id if isinstance(parent.value, ast.Name) else parent.value.attr) == "Literal"
    )


def test_no_core_module_spells_out_a_phase_set() -> None:
    offenders: list[str] = []
    for source in sorted(SRC.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            names = _literal_names(node)
            if names is None or _inside_literal_annotation(node, parents):
                continue
            if len([name for name in names if name in PHASE_NAMES]) >= 2:
                offenders.append(f"{source.relative_to(SRC)}:{node.lineno}")
    assert offenders == [], f"literal phase sets in core (derive them from work_tracker_okf.pipeline): {offenders}"
