"""Path-native read commands at the `gw work` boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from graph_works_cli.work_cli import main as work_main
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "works"
    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])
    assert result.exit_code == 0, result.output
    return root


def file_item(workspace: Path, title: str, *, kind: str = "Feature", parent: str | None = None) -> str:
    args = [
        "work",
        "file",
        "--title",
        title,
        "--kind",
        kind,
        "--summary",
        "One line",
        "--workspace",
        str(workspace),
        "--json",
    ]
    if parent is not None:
        args.extend(("--parent-path", parent))
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return str(json.loads(result.stdout)["path"])


def test_status_is_path_and_work_status_keyed(workspace: Path) -> None:
    path = file_item(workspace, "Alpha")
    result = runner.invoke(app, ["work", "status", "--workspace", str(workspace), "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == exit_codes.SUCCESS
    assert payload["by_work_status"] == {"open": 1}
    assert payload["resume"]["primary"]["path"] == path
    assert "slug" not in result.stdout and "workflow" + "_status" not in result.stdout


def test_status_human_output_names_the_canonical_path(workspace: Path) -> None:
    path = file_item(workspace, "Alpha")
    result = runner.invoke(app, ["work", "status", "--workspace", str(workspace)])
    assert result.exit_code == 0
    assert f"resume: {path}" in result.stdout


def test_next_emits_requested_and_selected_paths(workspace: Path) -> None:
    path = file_item(workspace, "Alpha")
    result = runner.invoke(app, ["work", "next", path, "--workspace", str(workspace), "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["requested_path"] == payload["selected_path"] == path
    assert "work_status" in payload and "slug" not in payload


def test_next_unknown_path_is_diagnostic_only(workspace: Path) -> None:
    result = runner.invoke(app, ["work", "next", "work/feature-missing", "--workspace", str(workspace), "--json"])
    assert result.exit_code == exit_codes.AMBIGUOUS
    assert result.stdout == ""
    assert "unknown work item" in result.stderr


def test_next_calls_the_provenance_guard(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = file_item(workspace, "Alpha")
    calls: list[str] = []
    monkeypatch.setattr(work_main, "warn_if_stale_routing", lambda: calls.append("guard"))
    runner.invoke(app, ["work", "next", path, "--workspace", str(workspace)])
    assert calls == ["guard"]


def test_regen_index_applies_by_default_and_names_indexes(workspace: Path) -> None:
    file_item(workspace, "Alpha")
    result = runner.invoke(app, ["work", "regen-index", "--workspace", str(workspace), "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert set(payload) == {"indexes", "warnings", "refusals", "applied", "rolled_back", "failures"}
    assert payload["applied"] is True and payload["rolled_back"] is False


def test_lint_projects_findings_explicitly(workspace: Path) -> None:
    file_item(workspace, "Alpha")
    result = runner.invoke(app, ["work", "lint", "--workspace", str(workspace), "--json"])
    payload = json.loads(result.stdout)
    assert set(payload) == {"ok", "findings"}
    assert all(set(item) == {"code", "severity", "message", "spec", "path", "line"} for item in payload["findings"])


def test_orchestrate_is_path_keyed_and_read_only(workspace: Path) -> None:
    path = file_item(workspace, "Alpha")
    result = runner.invoke(app, ["work", "orchestrate", path, "--workspace", str(workspace), "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["path"] == path
    assert "owner_path" in payload["decisions"]


def test_reconcile_context_degrades_to_warnings_with_path_keys(workspace: Path) -> None:
    path = file_item(workspace, "Alpha")
    result = runner.invoke(app, ["work", "reconcile-context", path, "--workspace", str(workspace), "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["path"] == path
    assert "owner_path" in payload and "warnings" in payload


def test_reconcile_context_rejects_a_named_non_repo(workspace: Path, tmp_path: Path) -> None:
    path = file_item(workspace, "Alpha")
    result = runner.invoke(
        app, ["work", "reconcile-context", path, "--repo", str(tmp_path), "--workspace", str(workspace)]
    )
    assert result.exit_code == exit_codes.NOT_IN_GIT_REPO
    assert result.stdout == ""
