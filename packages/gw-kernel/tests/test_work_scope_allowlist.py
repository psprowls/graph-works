"""Who may load the work scope. Adding a caller is a reviewed edit to ALLOWED.

The proof rule (design, "Scope"): the bundle is (a) never a `baseline_bundle=`/`bundle=`
into `gw_pipeline/transactions.py`, (b) never enters a lock-held context, and (c) is read
only through `load_items`, `concepts[<work path>]`, `unreadable_detail` and `root`.
AST, not grep: a docstring naming `load_work_bundle` is not a call.
"""

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
NAMES = frozenset({"load_work_bundle", "work_scope"})

ALLOWED = frozenset(
    {
        ("packages/gw-kernel/src/gw_kernel/workspace/bundle.py", "load_work_bundle"),
        ("packages/gw-work/src/gw_work/commands.py", "run_touch_active_work"),
        ("packages/gw-work/src/gw_work/commands.py", "run_lint"),
        ("packages/gw-work/src/gw_work/reconcile.py", "run_reconcile_context"),
        ("packages/gw-wiki/src/gw_wiki/lint_drift/lanes.py", "_compose_work"),
        ("packages/gw-orchestrate/src/gw_orchestrate/commands.py", "run_orchestrate"),
        ("packages/gw-pipeline/src/gw_pipeline/finish.py", "inspect_finish"),
        ("packages/gw-pipeline/src/gw_pipeline/finish.py", "plan_finish_cleanup"),
        ("packages/gw-orchestrate/src/gw_orchestrate/workspace_prepare.py", "run_prepare_workspace"),
        ("packages/gw-orchestrate/src/gw_orchestrate/gate.py", "_resolve"),
        ("packages/gw-orchestrate/src/gw_orchestrate/asks.py", "run_ask"),
        ("packages/gw-orchestrate/src/gw_orchestrate/asks.py", "run_ask_answer"),
        ("packages/gw-orchestrate/src/gw_orchestrate/integrate.py", "run_integrate"),
        ("packages/gw-orchestrate/src/gw_orchestrate/merge_workspace.py", "run_merge_workspace"),
        ("packages/gw-orchestrate/src/gw_orchestrate/placement.py", "run_record_reader"),
        ("packages/gw-orchestrate/src/gw_orchestrate/placement.py", "run_record_baseline"),
    }
)


def calls(path: Path, root: Path = REPO) -> set[tuple[str, str]]:
    """Every (module, top-level function) that calls a work-scope name in *path*."""
    found: set[tuple[str, str]] = set()
    rel = path.relative_to(root).as_posix()

    def visit(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            top = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and owner == "<module>"
            if isinstance(child, ast.Call):
                func = child.func
                callee = (
                    func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
                )
                if callee in NAMES:
                    found.add((rel, owner))
            visit(child, child.name if top else owner)

    visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)), "<module>")
    return found


def test_only_allowlisted_functions_load_the_work_scope() -> None:
    paths = sorted(path for root in SRC_ROOTS for path in root.rglob("*.py"))
    assert len(paths) > 100, "the walk is looking in the wrong place"
    assert set().union(*(calls(p) for p in paths)) == ALLOWED


def test_the_walker_attributes_nested_calls_to_the_top_level_function(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        "def outer(layout):\n    def observe():\n        return load_work_bundle(layout)\n    return observe()\n"
        "# load_work_bundle(layout) in a comment is not a call\n",
        encoding="utf-8",
        newline="",
    )
    assert calls(probe, tmp_path) == {("probe.py", "outer")}


@pytest.mark.parametrize(
    "package,module", [("gw-orchestrate", "gw_orchestrate"), ("gw-pipeline", "gw_pipeline"), ("gw-wiki", "gw_wiki")]
)
def test_slice_work_scope_call_is_found(tmp_path, package, module):
    path = tmp_path / f"packages/{package}/src/{module}/injected.py"
    path.parent.mkdir(parents=True)
    path.write_text("def added():\n    load_work_bundle(layout)\n", encoding="utf-8", newline="")
    found = calls(path, tmp_path)
    assert found == {(f"packages/{package}/src/{module}/injected.py", "added")}
    assert found - ALLOWED == found
