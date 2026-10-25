"""Production mutations name commits except explicitly reviewed rollback calls."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "gw_kernel"
REPO = Path(__file__).resolve().parents[3]
SRC_ROOTS = (
    SRC,
    REPO / "packages/gw-orchestrate/src/gw_orchestrate",
    REPO / "packages/gw-work/src/gw_work",
    REPO / "packages/gw-pipeline/src/gw_pipeline",
    REPO / "packages/gw-wiki/src/gw_wiki",
)
# Exactly one explicit commit=None call on the `restore` plan is permitted in
# each reviewed rollback function. Normal lifecycle publication must still name
# a WorkspaceCommit; changing line numbers cannot expand these exceptions.
CallKey = tuple[str, str, str]
CallSite = tuple[str, str, int, ast.Call]
ROLLBACK_EXEMPTIONS: dict[CallKey, str] = {
    (
        "packages/gw-orchestrate/src/gw_orchestrate/execute_return.py",
        "_restore_local",
        "restore",
    ): "Restore proven local publication preimages before guarded index restoration; rollback must not commit.",
}


def _calls_in(source: str, rel: str) -> list[CallSite]:
    found: list[CallSite] = []

    def visit(node: ast.AST, scope: tuple[str, ...] = ()) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = (*scope, node.name)
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name == "apply_mutation":
                found.append((rel, ".".join(scope) or "<module>", node.lineno, node))
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(ast.parse(source))
    return found


def _calls() -> list[CallSite]:
    return [
        call
        for root in SRC_ROOTS
        for path in sorted(root.rglob("*.py"))
        for call in _calls_in(path.read_text(encoding="utf-8"), path.relative_to(REPO).as_posix())
    ]


def _violations(calls: list[CallSite], exemptions: dict[CallKey, str]) -> tuple[list[str], list[CallKey]]:
    offenders: list[str] = []
    used: set[CallKey] = set()
    for rel, function, line, call in calls:
        commit = next((k for k in call.keywords if k.arg == "commit"), None)
        explicit_none = commit is not None and isinstance(commit.value, ast.Constant) and commit.value.value is None
        if commit is not None and not explicit_none:
            continue
        plan = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "plan"), None)
        key = (rel, function, plan.id if isinstance(plan, ast.Name) else "")
        if explicit_none and key in exemptions and key not in used:
            used.add(key)
        else:
            offenders.append(f"{rel}:{line} ({function})")
    return offenders, sorted(set(exemptions) - used)


def test_production_callers_exist() -> None:
    assert len(_calls()) >= 11


def test_every_production_call_passes_a_workspace_commit() -> None:
    offenders, stale = _violations(_calls(), ROLLBACK_EXEMPTIONS)
    assert offenders == [], f"apply_mutation without a WorkspaceCommit: {offenders}"
    assert stale == [], f"unused guarded rollback exemptions: {stale}"


def test_guarded_rollback_exemptions_do_not_hide_another_function() -> None:
    source = """
def apply_publication():
    apply_mutation(layout, restore, commit=None)
def publish_normally():
    apply_mutation(layout, plan, commit=None)
"""
    calls = _calls_in(source, "packages/gw-orchestrate/src/gw_orchestrate/execute_return.py")
    offenders, stale = _violations(
        calls,
        {("packages/gw-orchestrate/src/gw_orchestrate/execute_return.py", "apply_publication", "restore"): "rollback"},
    )
    assert offenders == ["packages/gw-orchestrate/src/gw_orchestrate/execute_return.py:5 (publish_normally)"]
    assert stale == []


def test_guarded_rollback_exemption_has_one_exact_occurrence() -> None:
    source = """
def apply_publication():
    apply_mutation(layout, restore, commit=None)
    apply_mutation(layout, restore, commit=None)
"""
    calls = _calls_in(source, "packages/gw-orchestrate/src/gw_orchestrate/execute_return.py")
    offenders, stale = _violations(
        calls,
        {("packages/gw-orchestrate/src/gw_orchestrate/execute_return.py", "apply_publication", "restore"): "rollback"},
    )
    assert offenders == ["packages/gw-orchestrate/src/gw_orchestrate/execute_return.py:4 (apply_publication)"]
    assert stale == []


def test_guarded_rollback_exemption_rejects_missing_commit_and_wrong_plan() -> None:
    source = """
def apply_publication():
    apply_mutation(layout, restore)
    apply_mutation(layout, plan, commit=None)
"""
    calls = _calls_in(source, "packages/gw-orchestrate/src/gw_orchestrate/execute_return.py")
    offenders, stale = _violations(
        calls,
        {("packages/gw-orchestrate/src/gw_orchestrate/execute_return.py", "apply_publication", "restore"): "rollback"},
    )
    assert offenders == [
        "packages/gw-orchestrate/src/gw_orchestrate/execute_return.py:3 (apply_publication)",
        "packages/gw-orchestrate/src/gw_orchestrate/execute_return.py:4 (apply_publication)",
    ]
    assert stale == [("packages/gw-orchestrate/src/gw_orchestrate/execute_return.py", "apply_publication", "restore")]


def test_removed_rollback_exemption_is_reported_stale() -> None:
    calls = _calls_in(
        "def apply_publication():\n    apply_mutation(layout, restore, commit=workspace_commit)\n",
        "packages/gw-orchestrate/src/gw_orchestrate/execute_return.py",
    )
    offenders, stale = _violations(
        calls,
        {("packages/gw-orchestrate/src/gw_orchestrate/execute_return.py", "apply_publication", "restore"): "rollback"},
    )
    assert offenders == []
    assert stale == [("packages/gw-orchestrate/src/gw_orchestrate/execute_return.py", "apply_publication", "restore")]


@pytest.mark.parametrize(
    "package,module", [("gw-orchestrate", "gw_orchestrate"), ("gw-pipeline", "gw_pipeline"), ("gw-wiki", "gw_wiki")]
)
def test_slice_mutation_without_commit_is_rejected(package, module):
    module = f"packages/{package}/src/{module}/injected.py"
    found, stale = _violations(_calls_in("def added():\n    apply_mutation(layout, plan)\n", module), {})
    assert found == [f"{module}:2 (added)"]
    assert stale == []
