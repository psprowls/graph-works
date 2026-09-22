"""The frozen path-native `gw work` surface and its layering boundary."""

from __future__ import annotations

import ast
import json
import sys
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
    ["work", "record-placement"],
    ["work", "touch-active-work"],
    ["work", "status"],
    ["work", "lint"],
    ["work", "archive"],
    ["work", "regen-index"],
    ["work", "reparent"],
    ["work", "adopt"],
    ["work", "orchestrate"],
    ["work", "reconcile-context"],
    ["work", "decision", "add"],
    ["work", "decision", "answer"],
    ["work", "decision", "list"],
    ["work", "decision", "supersede"],
    ["work", "decision", "overturn"],
]


def test_the_surface_is_exactly_eighteen_verbs() -> None:
    assert len(VERBS) == 18


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


@pytest.mark.parametrize(
    "verb",
    [
        ["work", "next"],
        ["work", "advance"],
        ["work", "archive"],
        ["work", "reparent"],
        ["work", "adopt"],
        ["work", "orchestrate"],
        ["work", "reconcile-context"],
        ["work", "decision", "add"],
        ["work", "decision", "answer"],
        ["work", "decision", "list"],
        ["work", "decision", "supersede"],
        ["work", "decision", "overturn"],
    ],
    ids=lambda verb: " ".join(verb),
)
def test_every_positional_work_identifier_is_named_path(verb: list[str]) -> None:
    payload = json.loads(runner.invoke(app, ["help", *verb, "--json"]).stdout)
    assert payload["arguments"][0]["name"] == "path"


def test_file_declares_only_the_path_native_identity_and_dependency_flags() -> None:
    payload = json.loads(runner.invoke(app, ["help", "work", "file", "--json"]).stdout)
    opts = {opt for option in payload["options"] for opt in option["opts"]}
    assert {
        "--title",
        "--kind",
        "--summary",
        "--affects",
        "--effort",
        "--name",
        "--parent-path",
        "--dep",
        "--blast-radius",
        "--version",
        "--target-date",
        "--owner",
        "--tags",
        "--json",
        "--workspace",
    } <= opts
    assert "--slug" + "-words" not in opts
    assert "--depends" + "-on" not in opts
    assert "--parent" not in opts


def test_advance_declares_the_donor_provenance_pair() -> None:
    payload = json.loads(runner.invoke(app, ["help", "work", "advance", "--json"]).stdout)
    opts = {opt for option in payload["options"] for opt in option["opts"]}
    assert {"--owner", "--effort", "--resolved-in", "--released-at", "--worktree", "--branch"} <= opts


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_work_cli_imports_domain_behavior_only_through_graph_works_core() -> None:
    """ADR-0013's boundary, asserted rather than reviewed.

    The work CLI may depend on its own interface package, Typer, and the Python
    standard library. Every domain/config/schema dependency must arrive through
    ``graph_works_core`` rather than a lower-level sibling package.
    """
    allowed = {*sys.stdlib_module_names, "graph_works_cli", "graph_works_core", "graph_works_wire", "typer"}
    offenders = {
        str(path.relative_to(SRC)): sorted(_imported_roots(path) - allowed)
        for path in WORK_CLI.rglob("*.py")
        if _imported_roots(path) - allowed
    }
    assert offenders == {}


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


def test_advance_declares_the_return_flag() -> None:
    payload = json.loads(runner.invoke(app, ["help", "work", "advance", "--json"]).stdout)
    opts = {opt for option in payload["options"] for opt in option["opts"]}
    assert "--return" in opts


def test_advance_declares_the_inference_opt_out() -> None:
    payload = json.loads(runner.invoke(app, ["help", "work", "advance", "--json"]).stdout)
    opts = {opt for option in payload["options"] for opt in option["opts"]}
    assert "--no-infer-worktree" in opts


def test_record_placement_declares_its_observation_and_no_lifecycle_flags() -> None:
    payload = json.loads(runner.invoke(app, ["help", "work", "record-placement", "--json"]).stdout)
    opts = {opt for option in payload["options"] for opt in option["opts"]}
    assert {"--root", "--phase", "--worktree", "--branch", "--dry-run", "--workspace", "--json"} <= opts
    assert not opts & {"--effort", "--owner", "--resolved-in", "--released-at", "--return", "--start-sha"}


def test_touch_active_work_declares_only_workspace_and_json() -> None:
    payload = json.loads(runner.invoke(app, ["help", "work", "touch-active-work", "--json"]).stdout)
    opts = {opt for option in payload["options"] for opt in option["opts"]}
    assert opts - {"--help"} == {"--workspace", "--json"}
