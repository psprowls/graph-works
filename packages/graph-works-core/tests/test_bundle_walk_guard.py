"""Pin filesystem enumeration exceptions; bundle commands use the shared loader.

This syntax boundary does not claim to detect arbitrary dynamic indirection.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGES = Path(__file__).resolve().parents[2]
SRC_ROOTS = tuple(PACKAGES / name / "src" for name in ("graph-works-core", "graph-works-cli", "graph-works-serve"))
# Exact lexical owners, never module-wide exceptions. Reasons define the scope.
ALLOWLIST = {
    ("graph_works_core/orchestrate/asks.py", "run_ask.target", "iterdir"): "Owned ask artifacts",
    ("graph_works_core/orchestrate/gate.py", "gate_wait_facts", "glob"): "Gate cache records",
    ("graph_works_core/orchestrate/gate.py", "live_pending", "glob"): "Gate cache records",
    ("graph_works_core/orchestrate/gate.py", "prune", "glob"): "Gate cache records",
    ("graph_works_core/orchestrate/gate.py", "_choose_record", "glob"): "Gate cache records",
    ("graph_works_core/orchestrate/gate.py", "recover_unrecorded", "glob"): "Gate cache records",
    ("graph_works_core/orchestrate/gate_index.py", "read_index", "glob"): "Caller-supplied receipt subtree pattern",
    ("graph_works_core/scan/commands.py", "emit_scan_worklist", "glob"): "Owned scan briefs",
    ("graph_works_core/scan/commands.py", "load_results_dir", "glob"): "Scan result artifacts",
    (
        "graph_works_core/scan/prose_refresh.py",
        "build_prose_refresh_tools.list_repo_tree",
        "iterdir",
    ): "Source repository browsing",
    (
        "graph_works_core/scan/repo_scan.py",
        "_bundle_state",
        "walk",
    ): "Stat snapshot explicitly prunes CLONE_PRUNE before descent",
    ("graph_works_core/transcript_capture.py", "_copy_transcript", "glob"): "Transcript sidechain input",
    ("graph_works_core/work/commands.py", "_owned_references", "rglob"): "One item's owned references",
    ("graph_works_core/workspace/bundle.py", "work_scope", "iterdir"): "Shared shallow scope discovery",
    ("graph_works_core/workspace/lane_facts.py", "lane_documents", "glob"): "Shallow lane metadata",
    ("graph_works_core/workspace/lane_facts.py", "has_lane_pages", "glob"): "Shallow lane metadata",
    ("graph_works_core/workspace/schema_read.py", "_read", "glob"): "Schema directory",
    ("graph_works_core/workspace/transactions.py", "_fsync_entry", "iterdir"): "Captured transaction entry durability",
    ("graph_works_core/workspace/transactions.py", "_copy_entry", "iterdir"): "Captured transaction entry copying",
    (
        "graph_works_core/workspace/transactions.py",
        "_apply_captured_mode",
        "iterdir",
    ): "Captured transaction entry modes",
    ("graph_works_core/workspace/transactions.py", "_remove_entry", "iterdir"): "Captured transaction entry removal",
    (
        "graph_works_core/workspace/transactions.py",
        "_copy_backup_to_live",
        "iterdir",
    ): "Captured transaction entry restore",
    ("graph_works_cli/wiki_cli/scan.py", "_reset_results_dir", "iterdir"): "Scan results cleanup",
}


def enumerations(module: str, source: str) -> list[tuple[tuple[str, str, str], int]]:
    tree = ast.parse(source)
    os_names = {"os"}
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            os_names.update(alias.asname or alias.name for alias in node.names if alias.name == "os")
        elif isinstance(node, ast.ImportFrom) and node.module == "os" and node.level == 0:
            aliases.update(
                (alias.asname or alias.name, alias.name) for alias in node.names if alias.name in {"walk", "scandir"}
            )
    found = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.owners = []

        def visit_FunctionDef(self, node):
            self.owners.append(node.name)
            self.generic_visit(node)
            self.owners.pop()

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

        def visit_Call(self, node):
            operation = None
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in {"glob", "rglob", "iterdir"} or (
                    node.func.attr in {"walk", "scandir"}
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in os_names
                ):
                    operation = node.func.attr
            elif isinstance(node.func, ast.Name):
                operation = aliases.get(node.func.id)
            if operation is not None:
                found.append(((module, ".".join(self.owners) or "<module>", operation), node.lineno))
            self.generic_visit(node)

    Visitor().visit(tree)
    return found


def violations(calls, allowlist):
    seen = {key for key, _ in calls}
    unexpected = [
        f"{module}:{line}: {owner} ({op})"
        for (module, owner, op), line in calls
        if (module, owner, op) not in allowlist
    ]
    stale = sorted(set(allowlist) - seen)
    return unexpected, stale


def test_shipped_enumerations_have_exact_scoped_exceptions():
    calls = []
    paths = [path for root in SRC_ROOTS for path in root.rglob("*.py")]
    assert len(paths) > 100, "source roots are wrong"
    for root in SRC_ROOTS:
        for path in sorted(root.rglob("*.py")):
            calls.extend(enumerations(path.relative_to(root).as_posix(), path.read_text(encoding="utf-8")))
    unexpected, stale = violations(calls, ALLOWLIST)
    assert unexpected == [], "\n".join(unexpected)
    assert stale == [], f"Unused exceptions: {stale}"
    assert all(ALLOWLIST.values())


@pytest.mark.parametrize(
    "call",
    [
        "root.rglob('*')",
        "root.glob('*')",
        "root.iterdir()",
        "os.walk(root)",
        "os.scandir(root)",
        "scan(root)",
        "walk(root)",
        "operating.walk(root)",
    ],
)
def test_new_walker_is_rejected(call):
    source = f"import os as operating\nfrom os import walk, scandir as scan\ndef new_command():\n    {call}\n"
    calls = enumerations("injected.py", source)
    unexpected, stale = violations(calls, {})
    assert len(unexpected) == 1
    assert "new_command" in unexpected[0]
    assert stale == []


def test_nested_function_does_not_inherit_an_exception():
    source = "def allowed():\n    root.iterdir()\n    def added():\n        root.iterdir()\n"
    allowed = {("injected.py", "allowed", "iterdir"): "Shallow metadata"}
    unexpected, stale = violations(enumerations("injected.py", source), allowed)
    assert unexpected == ["injected.py:4: allowed.added (iterdir)"]
    assert stale == []


def test_comments_and_strings_leave_exception_stale():
    source = "# os.walk(root)\ntext = 'root.rglob(\"*\")'\n"
    key = ("injected.py", "removed", "walk")
    assert violations(enumerations("injected.py", source), {key: "Removed walker"}) == ([], [key])
