"""Every production apply_mutation caller names its workspace commit."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "graph_works_core"
ALLOWLIST: dict[str, str] = {}


def _calls() -> list[tuple[str, int, ast.Call]]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
                if name == "apply_mutation":
                    found.append((path.relative_to(SRC).as_posix(), node.lineno, node))
    return found


def test_production_callers_exist() -> None:
    assert len(_calls()) >= 11


def test_every_production_call_passes_a_workspace_commit() -> None:
    offenders = []
    for rel, line, call in _calls():
        if rel in ALLOWLIST:
            continue
        commit = next((k for k in call.keywords if k.arg == "commit"), None)
        if commit is None or (isinstance(commit.value, ast.Constant) and commit.value.value is None):
            offenders.append(f"{rel}:{line}")
    assert offenders == [], f"apply_mutation without a WorkspaceCommit: {offenders}"
