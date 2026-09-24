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


def test_next_unknown_path_emits_the_envelope(workspace: Path) -> None:
    result = runner.invoke(app, ["work", "next", "work/feature-missing", "--workspace", str(workspace), "--json"])
    assert result.exit_code == exit_codes.AMBIGUOUS
    doc = json.loads(result.stdout)
    assert set(doc) == {"error"}
    assert doc["error"]["reason"] == "unresolved" and doc["error"]["payload"] is None
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
    assert payload["repo"] is None


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


def override_skill(workspace: Path, value: str) -> None:
    """Hand-edit the shared dispatch document, bypassing write-time validation."""
    path = workspace / "dispatch.yaml"
    path.write_text(
        f"pipeline:\n  rules:\n  - match: {{variant: exploration}}\n    skill: {json.dumps(value)}\n",
        encoding="utf-8",
    )


def test_next_reports_a_malformed_stage_skill_as_a_blocker(workspace: Path) -> None:
    path = file_item(workspace, "Alpha")
    override_skill(workspace, "a:b:c")
    result = runner.invoke(app, ["work", "next", path, "--workspace", str(workspace), "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == exit_codes.GENERIC
    assert payload["action"] is None
    assert payload["on_dispatch"] is None
    assert any("rule 0.skill" in blocker for blocker in payload["blockers"])
    assert any("dispatch.yaml" in blocker for blocker in payload["blockers"])


def test_next_reports_an_empty_stage_skill_as_a_blocker(workspace: Path) -> None:
    path = file_item(workspace, "Alpha")
    override_skill(workspace, "")
    result = runner.invoke(app, ["work", "next", path, "--workspace", str(workspace), "--json"])
    assert result.exit_code == exit_codes.GENERIC
    assert json.loads(result.stdout)["action"] is None


def test_next_renders_the_preflight_blocker_in_human_output(workspace: Path) -> None:
    path = file_item(workspace, "Alpha")
    override_skill(workspace, "a:b:c")
    result = runner.invoke(app, ["work", "next", path, "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert "blocked:" in result.stdout
    assert "dispatch:" not in result.stdout


def test_next_still_dispatches_a_valid_override_verbatim(workspace: Path) -> None:
    # The over-refusal regression at the CLI boundary: a bare name is valid
    # (D-002) and must reach `action.skill` unchanged.
    path = file_item(workspace, "Alpha")
    override_skill(workspace, "my-brainstormer")
    result = runner.invoke(app, ["work", "next", path, "--workspace", str(workspace), "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == exit_codes.SUCCESS
    assert payload["action"]["skill"] == "my-brainstormer"
    assert payload["blockers"] == []


def test_orchestrate_hard_fails_on_a_malformed_stage_skill(workspace: Path) -> None:
    # Auto-drive has no blockers channel to degrade into, so it keeps the
    # existing WorkspaceError -> SCHEMA_MISMATCH mapping. This is what stops
    # auto-drive dispatching a malformed name.
    path = file_item(workspace, "Alpha")
    override_skill(workspace, "a:b:c")
    result = runner.invoke(app, ["work", "orchestrate", path, "--workspace", str(workspace), "--json"])
    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert "rule 0.skill" in result.stderr


def test_ingest_queue_lists_a_terminal_item_with_an_uningested_design(workspace: Path) -> None:
    layout = work_main.resolve_workspace(str(workspace))
    page = layout.bundle_dir / "work" / "bug-a.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Bug\ntitle: bug-a\ndescription: d\nstatus: stable\n"
        "work_status: resolved\nphase: done\neffort: small\n"
        "opened: 2026-08-01\nupdated: 2026-08-02\naffects:\n- packages/a\n"
        "sources:\n  - id: design\n    resource: /work/bug-a/references/01-design.md\n"
        "    title: Design\n---\n\n## Summary\nd\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
    )
    artifact = layout.bundle_dir / "work" / "bug-a" / "references" / "01-design.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# spec\n", encoding="utf-8")

    result = runner.invoke(app, ["work", "ingest-queue", "--workspace", str(workspace), "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["pending"] == [
        {
            "path": "work/bug-a",
            "work_status": "resolved",
            "resource": "/work/bug-a/references/01-design.md",
            "origin": "work/bug-a/references/01-design.md",
        }
    ]


def test_ingest_queue_renders_an_empty_queue_in_human_mode(workspace: Path) -> None:
    result = runner.invoke(app, ["work", "ingest-queue", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.stdout
    assert "0 design spec(s) pending ingest" in result.stdout


def test_next_and_orchestrate_json_share_profile_and_provenance(workspace: Path):
    import subprocess

    from okf_io import load

    path = file_item(workspace, "Dispatch equivalence")
    page = workspace / "okf" / f"{path}.md"
    document = load(page)
    document.set("affects", ["packages/a"])
    page.write_text(document.serialize(), encoding="utf-8")
    (workspace / "dispatch.yaml").write_text(
        "pipeline:\n  rules:\n  - match: {}\n    model: original\n    reasoning_effort: high\n", encoding="utf-8"
    )
    (workspace / "dispatch.local.yaml").write_text(
        "pipeline:\n  rules:\n  - name: local-agent\n    match: {}\n    agent: codex\n", encoding="utf-8"
    )
    code = workspace.parent / "code"
    code.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(code)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(code),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.test",
            "commit",
            "--allow-empty",
            "-m",
            "base",
        ],
        check=True,
        capture_output=True,
    )
    manifest = workspace / "workspace.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("repositories: {}", f'repositories:\n  code:\n    path: "{code}"'),
        encoding="utf-8",
    )
    next_response = runner.invoke(app, ["work", "next", path, "--workspace", str(workspace), "--json"])
    plan_response = runner.invoke(app, ["work", "orchestrate", path, "--workspace", str(workspace), "--json"])
    assert next_response.exit_code == plan_response.exit_code == 0
    payload = json.loads(next_response.stdout)
    plan_payload = json.loads(plan_response.stdout)
    planned = plan_payload["dispatches"][0]
    assert payload["action"]["skill"] == planned["skill"]
    for field in ("agent", "model", "reasoning_effort", "skill", "mode"):
        assert payload["dispatch"]["profile"][field] == planned[field]
    assert payload["dispatch"]["provenance"] == planned["provenance"]
    assert payload["dispatch"]["provenance"]["model"]["reason"] == "agent-change"
    assert "permission_mode" not in plan_payload
    human = runner.invoke(app, ["work", "next", path, "--workspace", str(workspace)])
    assert "agent=codex model=default effort=default" in human.stdout
    assert "local-agent" in human.stdout and "agent-change" in human.stdout
