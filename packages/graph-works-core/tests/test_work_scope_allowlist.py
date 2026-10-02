"""Who may load the work scope. Adding a caller is a reviewed edit to ALLOWED.

The proof rule (design, "Scope"): the bundle is (a) never a `baseline_bundle=`/`bundle=`
into `workspace/transactions.py`, (b) never enters a lock-held context, and (c) is read
only through `load_items`, `concepts[<work path>]`, `unreadable_detail` and `root`.
AST, not grep: a docstring naming `load_work_bundle` is not a call.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "graph_works_core"
NAMES = frozenset({"load_work_bundle", "work_scope"})

ALLOWED = frozenset(
    {
        ("workspace/bundle.py", "load_work_bundle"),
        ("work/commands.py", "run_status"),
        ("work/commands.py", "run_work_list"),
        ("work/commands.py", "run_item_read"),
        ("work/commands.py", "run_touch_active_work"),
        ("work/commands.py", "run_work_queue"),
        ("work/commands.py", "run_lint"),
        ("work/commands.py", "run_open_decisions"),
        ("work/reconcile.py", "run_reconcile_context"),
        ("lint_drift/lanes.py", "_compose_work"),
        ("orchestrate/commands.py", "run_orchestrate"),
        ("workspace/finish.py", "inspect_finish"),
        ("workspace/finish.py", "plan_finish_cleanup"),
        ("orchestrate/workspace_prepare.py", "run_prepare_workspace"),
        ("orchestrate/gate.py", "_resolve"),
        ("orchestrate/asks.py", "run_ask"),
        ("orchestrate/asks.py", "run_ask_answer"),
        ("orchestrate/integrate.py", "run_integrate"),
        ("orchestrate/merge_workspace.py", "run_merge_workspace"),
        ("orchestrate/placement.py", "run_record_reader"),
        ("orchestrate/placement.py", "run_record_baseline"),
    }
)


def calls(path: Path, root: Path = SRC) -> set[tuple[str, str]]:
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
    paths = sorted(SRC.rglob("*.py"))
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
