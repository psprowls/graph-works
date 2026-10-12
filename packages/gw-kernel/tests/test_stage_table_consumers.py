"""Core's phase sets are the stage table's, and no core module spells one out (design §3.1, §5)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from gw_kernel.guidance import claims as guidance_claims
from gw_kernel.workspace import dispatch_config, provenance
from work_tracker_okf.pipeline import (
    PACKAGED_DEFINITION,
    STAGE_TABLE,
    dispatch_phases,
    phase_order,
    read_only_phases,
    results_phases,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "gw_kernel"
SRC_ROOTS = (
    SRC,
    SRC.parents[2] / "gw-pipeline/src/gw_pipeline",
    SRC.parents[2] / "gw-orchestrate/src/gw_orchestrate",
    SRC.parents[2] / "gw-work/src/gw_work",
)
REPO = SRC.parents[3]
PHASE_NAMES = frozenset(phase_order())


def test_each_constant_equals_its_accessor() -> None:
    assert tuple(PACKAGED_DEFINITION.artifacts) == dispatch_config._ARTIFACT_STAGES
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


def phase_set_offenders(paths: list[Path], *, root: Path = REPO) -> list[str]:
    offenders: list[str] = []
    for source in paths:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            names = _literal_names(node)
            if names is None or _inside_literal_annotation(node, parents):
                continue
            if len([name for name in names if name in PHASE_NAMES]) >= 2:
                offenders.append(f"{source.relative_to(root)}:{node.lineno}")
    return offenders


def test_no_core_module_spells_out_a_phase_set() -> None:
    paths = sorted(path for source_root in SRC_ROOTS for path in source_root.rglob("*.py"))
    offenders = phase_set_offenders(paths)
    assert offenders == [], f"literal phase sets in core (derive them from work_tracker_okf.pipeline): {offenders}"


@pytest.mark.parametrize("package,module", [("gw-orchestrate", "gw_orchestrate"), ("gw-pipeline", "gw_pipeline")])
def test_slice_literal_phase_set_is_found(tmp_path: Path, package: str, module: str) -> None:
    path = tmp_path / f"packages/{package}/src/{module}/injected.py"
    path.parent.mkdir(parents=True)
    path.write_text("phases = {'design', 'execute'}\n", encoding="utf-8", newline="")
    assert phase_set_offenders([path], root=tmp_path) == [f"packages/{package}/src/{module}/injected.py:1"]
