"""The `gw work` surface: fifteen verbs, one forbidden import, three guarded verbs."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from graph_works_cli.cli import app
from typer.testing import CliRunner

runner = CliRunner()

SRC = Path(__file__).resolve().parents[1] / "src" / "graph_works_cli"
WORK_CLI = SRC / "work_cli"

VERBS = [
    ["work", "file"],
    ["work", "next"],
    ["work", "advance"],
    ["work", "status"],
    ["work", "lint"],
    ["work", "archive"],
    ["work", "regen-index"],
    ["work", "orchestrate"],
    ["work", "reconcile-context"],
    ["work", "adopt-child-specs"],
    ["work", "decision", "add"],
    ["work", "decision", "answer"],
    ["work", "decision", "list"],
    ["work", "decision", "supersede"],
    ["work", "decision", "overturn"],
]


def test_the_surface_is_exactly_fifteen_verbs() -> None:
    assert len(VERBS) == 15


@pytest.mark.parametrize("verb", VERBS, ids=lambda verb: " ".join(verb))
def test_every_verb_is_registered_and_resolves_through_help(verb: list[str]) -> None:
    result = runner.invoke(app, ["help", *verb, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["path"] == verb


def test_next_declares_descend_and_json() -> None:
    payload = json.loads(runner.invoke(app, ["help", "work", "next", "--json"]).stdout)
    opts = {opt for option in payload["options"] for opt in option["opts"]}
    assert {"--descend", "--json", "--workspace"} <= opts


def test_file_declares_every_donor_flag() -> None:
    payload = json.loads(runner.invoke(app, ["help", "work", "file", "--json"]).stdout)
    opts = {opt for option in payload["options"] for opt in option["opts"]}
    assert {
        "--title",
        "--kind",
        "--summary",
        "--affects",
        "--effort",
        "--slug-words",
        "--parent",
        "--depends-on",
        "--dep",
        "--blast-radius",
        "--target",
        "--owner",
        "--tags",
        "--json",
        "--workspace",
    } <= opts


def test_advance_declares_the_donor_provenance_pair() -> None:
    payload = json.loads(runner.invoke(app, ["help", "work", "advance", "--json"]).stdout)
    opts = {opt for option in payload["options"] for opt in option["opts"]}
    assert {"--owner", "--effort", "--resolved-in", "--worktree", "--branch"} <= opts


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_no_cli_module_imports_work_tracker_okf() -> None:
    """ADR-0013's boundary, asserted rather than reviewed. The CLI reaches work
    domain types only through `graph_works_core.work.commands`' re-exports."""
    offenders = sorted(
        str(path.relative_to(SRC)) for path in SRC.rglob("*.py") if "work_tracker_okf" in _imported_roots(path)
    )
    assert offenders == []


def test_exactly_three_verbs_guard_against_stale_routing() -> None:
    """The guard is routing-sensitivity, not a blanket warning: wiring it to a
    fourth verb makes it noise and it gets filtered out."""
    source = (WORK_CLI / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(WORK_CLI / "main.py"))
    guarded = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "warn_if_stale_routing"
            for call in ast.walk(node)
        )
    }
    assert guarded == {"next_stage", "advance", "orchestrate"}
