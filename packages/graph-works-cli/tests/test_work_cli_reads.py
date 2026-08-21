"""`gw work` read-only verbs at the CLI boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from graph_works_cli.work_cli import main as work_main
from graph_works_cli.workspace_resolution import resolve_workspace
from typer.testing import CliRunner

runner = CliRunner()

ITEM = """---
type: Feature
title: {slug}
description: d
status: stable
workflow_status: open
phase: {phase}
effort: medium
opened: 2026-08-01
updated: 2026-08-01
affects:
- packages/a
---

## Summary
d

## Plan

| Action | Done when | Rationale |
| --- | --- | --- |
"""

# An item that violates work lint rules: in-progress without owner
NONCONFORMANT_ITEM = """---
type: Feature
title: {slug}
description: d
status: stable
workflow_status: in-progress
phase: execute
effort: medium
opened: 2026-08-01
updated: 2026-08-01
affects:
- packages/a
---

## Summary
d

## Plan

| Action | Done when | Rationale |
| --- | --- | --- |
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "works"
    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])
    assert result.exit_code == 0, result.output
    return root


def write_item(workspace: Path, slug: str, *, phase: str = "plan", template: str = ITEM) -> Path:
    layout = resolve_workspace(str(workspace))
    target = layout.bundle_dir / "work" / f"{slug}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(template.format(slug=slug, phase=phase), encoding="utf-8")
    return target


def test_status_reads_the_live_bundle_with_no_sidecar_precondition(workspace: Path) -> None:
    write_item(workspace, "2026-08-01-feature-a")
    result = runner.invoke(app, ["work", "status", "--workspace", str(workspace), "--json"])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    payload = json.loads(result.stdout)
    assert payload["total"] == 1
    assert payload["by_phase"] == {"plan": 1}


def test_status_human_output_names_the_resume_candidate(workspace: Path) -> None:
    write_item(workspace, "2026-08-01-feature-a")
    result = runner.invoke(app, ["work", "status", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    assert "1 item(s) under work/" in result.stdout
    assert "resume: 2026-08-01-feature-a" in result.stdout


def test_missing_workspace_exits_not_initialized(tmp_path: Path) -> None:
    result = runner.invoke(app, ["work", "status", "--workspace", str(tmp_path / "nope")])
    assert result.exit_code == exit_codes.NOT_INITIALIZED


def test_lint_emits_findings_as_json_and_exits_generic_when_not_ok(workspace: Path) -> None:
    write_item(workspace, "2026-08-01-feature-a", template=NONCONFORMANT_ITEM)
    result = runner.invoke(app, ["work", "lint", "--workspace", str(workspace), "--json"])
    payload = json.loads(result.stdout)
    assert set(payload) == {"ok", "findings"}
    assert payload["ok"] is False
    assert len(payload["findings"]) > 0
    assert result.exit_code == exit_codes.GENERIC


def test_lint_exits_success_when_ok(workspace: Path) -> None:
    write_item(workspace, "2026-08-01-feature-a")
    result = runner.invoke(app, ["work", "lint", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.SUCCESS


def test_lint_maps_a_config_error_to_schema_mismatch(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_root: Path, **_kwargs: object) -> object:
        raise work_main.ConfigError("bad declarations")

    monkeypatch.setattr(work_main, "load_config", boom)
    result = runner.invoke(app, ["work", "lint", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert "bad declarations" in result.stderr


def test_lint_config_os_error_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_root: Path, **_kwargs: object) -> object:
        raise OSError("disk gone")

    monkeypatch.setattr(work_main, "load_config", boom)
    result = runner.invoke(app, ["work", "lint", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert "disk gone" in result.stderr


def test_lint_run_lint_value_error_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise ValueError("bad rule config")

    monkeypatch.setattr(work_main.work, "run_lint", boom)
    result = runner.invoke(app, ["work", "lint", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert "bad rule config" in result.stderr


def test_lint_human_output_prints_findings(workspace: Path) -> None:
    write_item(workspace, "2026-08-01-feature-a", template=NONCONFORMANT_ITEM)
    result = runner.invoke(app, ["work", "lint", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert "state.in-progress-without-owner" in result.stderr
    assert "`workflow_status: in-progress` with no `owner`" in result.stderr


def test_status_run_status_value_error_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise ValueError("bad bundle")

    monkeypatch.setattr(work_main.work, "run_status", boom)
    result = runner.invoke(app, ["work", "status", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert "bad bundle" in result.stderr


def test_status_run_status_os_error_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("disk gone")

    monkeypatch.setattr(work_main.work, "run_status", boom)
    result = runner.invoke(app, ["work", "status", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert "disk gone" in result.stderr


def test_regen_index_value_error_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise ValueError("bad index")

    monkeypatch.setattr(work_main.work, "run_regen_index", boom)
    result = runner.invoke(app, ["work", "regen-index", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert "bad index" in result.stderr


def test_regen_index_applies_by_default(workspace: Path) -> None:
    write_item(workspace, "2026-08-01-feature-a")
    result = runner.invoke(app, ["work", "regen-index", "--workspace", str(workspace), "--json"])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    payload = json.loads(result.stdout)
    layout = resolve_workspace(str(workspace))
    index_path = layout.bundle_dir / "work" / "index.md"
    assert payload["path"].endswith("work/index.md")
    assert "2026-08-01-feature-a" in index_path.read_text(encoding="utf-8")


def test_regen_index_dry_run_writes_nothing(workspace: Path) -> None:
    write_item(workspace, "2026-08-01-feature-a")
    layout = resolve_workspace(str(workspace))
    index = layout.bundle_dir / "work" / "index.md"
    before = index.read_text(encoding="utf-8") if index.exists() else None
    result = runner.invoke(app, ["work", "regen-index", "--workspace", str(workspace), "--dry-run"])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    after = index.read_text(encoding="utf-8") if index.exists() else None
    assert after == before


def test_regen_index_nothing_to_do_when_already_reconciled(workspace: Path) -> None:
    write_item(workspace, "2026-08-01-feature-a")
    # First run: reconcile the index
    runner.invoke(app, ["work", "regen-index", "--workspace", str(workspace)])
    # Second run: should have nothing to do
    result = runner.invoke(app, ["work", "regen-index", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.SUCCESS
    assert "nothing to do" in result.stdout


def test_next_emits_every_contracted_key(workspace: Path) -> None:
    write_item(workspace, "2026-08-01-feature-a", phase="plan")
    result = runner.invoke(app, ["work", "next", "2026-08-01-feature-a", "--workspace", str(workspace), "--json"])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    payload = json.loads(result.stdout)
    for key in ("phase", "status", "blockers", "on_complete", "action", "normalized", "child_rollup"):
        assert key in payload
    assert payload["action"]["skill"] == "writing-plans"
    assert payload["artifact"]["path"].endswith("02-plan-plan.md")
    assert Path(payload["artifact"]["path"]).is_absolute()


def test_next_calls_the_provenance_guard_before_core(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(work_main, "warn_if_stale_routing", lambda: calls.append("guard"))
    real_run_next = work_main.work.run_next

    def spying_run_next(*args: object, **kwargs: object) -> object:
        calls.append("core")
        return real_run_next(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(work_main.work, "run_next", spying_run_next)
    write_item(workspace, "2026-08-01-feature-a")
    runner.invoke(app, ["work", "next", "2026-08-01-feature-a", "--workspace", str(workspace)])
    assert calls == ["guard", "core"]


def test_next_self_heals_the_spec_pointer_once(workspace: Path) -> None:
    layout = work_main.resolve_workspace(str(workspace))
    slug = "2026-08-01-feature-a"
    write_item(workspace, slug, phase="design")
    spec = layout.bundle_dir / "work" / slug / "references" / "01-design-spec.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(f"# Design spec — {slug}\n", encoding="utf-8")

    first = runner.invoke(app, ["work", "next", slug, "--workspace", str(workspace), "--json"])
    assert json.loads(first.stdout)["normalized"] == {"spec_doc": f"work/{slug}/references/01-design-spec.md"}

    second = runner.invoke(app, ["work", "next", slug, "--workspace", str(workspace), "--json"])
    assert json.loads(second.stdout)["normalized"] is None


def test_next_unknown_slug_exits_ambiguous(workspace: Path) -> None:
    result = runner.invoke(app, ["work", "next", "missing", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.AMBIGUOUS
    assert "missing" in result.stderr


def test_next_exits_generic_when_blocked(workspace: Path) -> None:
    write_item(workspace, "2026-08-01-feature-a", phase="done")
    result = runner.invoke(app, ["work", "next", "2026-08-01-feature-a", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert "blocked:" in result.stdout


def test_next_maps_a_workspace_error_to_schema_mismatch(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise work_main.WorkspaceError("bad routing config")

    monkeypatch.setattr(work_main.work, "run_next", boom)
    write_item(workspace, "2026-08-01-feature-a")
    result = runner.invoke(app, ["work", "next", "2026-08-01-feature-a", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.SCHEMA_MISMATCH


def test_next_run_next_os_error_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("disk gone")

    monkeypatch.setattr(work_main.work, "run_next", boom)
    write_item(workspace, "2026-08-01-feature-a")
    result = runner.invoke(app, ["work", "next", "2026-08-01-feature-a", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert "disk gone" in result.stderr


def test_next_reports_route_warnings_in_json_and_human_mode(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import dataclasses

    real_run_next = work_main.work.run_next

    def patched(*args: object, **kwargs: object) -> object:
        result = real_run_next(*args, **kwargs)  # type: ignore[arg-type]
        return dataclasses.replace(result, warnings=("heads up",))

    monkeypatch.setattr(work_main.work, "run_next", patched)
    write_item(workspace, "2026-08-01-feature-a")

    json_result = runner.invoke(app, ["work", "next", "2026-08-01-feature-a", "--workspace", str(workspace), "--json"])
    assert json_result.exit_code == exit_codes.SUCCESS, json_result.output
    assert "heads up" in json_result.stderr

    human_result = runner.invoke(app, ["work", "next", "2026-08-01-feature-a", "--workspace", str(workspace)])
    assert human_result.exit_code == exit_codes.SUCCESS, human_result.output
    assert "heads up" in human_result.stderr


def test_next_descend_reports_the_leaf_path(workspace: Path) -> None:
    epic = "2026-08-01-epic-e"
    child = "2026-08-02-feature-a"
    epic_template = """---
type: Epic
title: {slug}
description: d
status: stable
workflow_status: open
phase: execute
opened: 2026-08-01
updated: 2026-08-01
---

## Summary
d

## Plan
"""
    write_item(workspace, epic, template=epic_template)
    write_item(workspace, child, phase="plan")
    layout = work_main.resolve_workspace(str(workspace))
    child_page = layout.bundle_dir / "work" / f"{child}.md"
    text = child_page.read_text(encoding="utf-8")
    child_page.write_text(text.replace("---\n\n## Summary", f"parent: {epic}\n---\n\n## Summary"), encoding="utf-8")

    result = runner.invoke(app, ["work", "next", epic, "--descend", "--workspace", str(workspace), "--json"])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    payload = json.loads(result.stdout)
    assert payload["descent"]["from"] == epic
    assert payload["descent"]["leaf"] == child

    human_result = runner.invoke(app, ["work", "next", epic, "--descend", "--workspace", str(workspace)])
    assert human_result.exit_code == exit_codes.SUCCESS, human_result.output
    assert "descent:" in human_result.stdout


def test_next_reports_child_rollup_for_an_epic(workspace: Path) -> None:
    epic = "2026-08-01-epic-e"
    child = "2026-08-02-feature-a"
    epic_template = """---
type: Epic
title: {slug}
description: d
status: stable
workflow_status: open
phase: plan
opened: 2026-08-01
updated: 2026-08-01
---

## Summary
d

## Plan
"""
    write_item(workspace, epic, template=epic_template)
    write_item(workspace, child, phase="plan")
    layout = work_main.resolve_workspace(str(workspace))
    child_page = layout.bundle_dir / "work" / f"{child}.md"
    text = child_page.read_text(encoding="utf-8")
    child_page.write_text(text.replace("---\n\n## Summary", f"parent: {epic}\n---\n\n## Summary"), encoding="utf-8")

    result = runner.invoke(app, ["work", "next", epic, "--workspace", str(workspace), "--json"])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    payload = json.loads(result.stdout)
    assert payload["child_rollup"] == {"total": 1, "terminal": 0, "open_slugs": [child]}


def test_next_descend_reports_blocked_when_no_dep_ready_child(workspace: Path) -> None:
    epic = "2026-08-01-epic-e"
    child = "2026-08-02-feature-a"
    epic_template = """---
type: Epic
title: {slug}
description: d
status: stable
workflow_status: open
phase: execute
opened: 2026-08-01
updated: 2026-08-01
---

## Summary
d

## Plan
"""
    child_template = """---
type: Feature
title: {slug}
description: d
status: stable
workflow_status: mitigated
phase: execute
effort: medium
opened: 2026-08-01
updated: 2026-08-01
parent: 2026-08-01-epic-e
affects:
- packages/a
---

## Summary
d

## Plan

| Action | Done when | Rationale |
| --- | --- | --- |
"""
    write_item(workspace, epic, template=epic_template)
    write_item(workspace, child, template=child_template)

    result = runner.invoke(app, ["work", "next", epic, "--descend", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert "--descend: no dep-ready child" in result.stdout


def test_next_self_heals_the_spec_pointer_in_human_mode(workspace: Path) -> None:
    layout = work_main.resolve_workspace(str(workspace))
    slug = "2026-08-01-feature-a"
    write_item(workspace, slug, phase="design")
    spec = layout.bundle_dir / "work" / slug / "references" / "01-design-spec.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(f"# Design spec — {slug}\n", encoding="utf-8")

    result = runner.invoke(app, ["work", "next", slug, "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    assert "[fix] stamped spec_doc:" in result.stdout


def test_orchestrate_emits_the_eight_contracted_keys(workspace: Path) -> None:
    write_item(workspace, "2026-08-01-feature-a", phase="plan")
    result = runner.invoke(
        app, ["work", "orchestrate", "2026-08-01-feature-a", "--workspace", str(workspace), "--json"]
    )
    assert result.exit_code == exit_codes.SUCCESS, result.output
    payload = json.loads(result.stdout)
    for key in (
        "terminal",
        "max_parallel",
        "slots_free",
        "permission_mode",
        "live",
        "dispatches",
        "advances",
        "blocked",
    ):
        assert key in payload


def test_orchestrate_splits_live_keys_and_writes_nothing(workspace: Path) -> None:
    slug = "2026-08-01-feature-a"
    page = write_item(workspace, slug, phase="plan")
    before = page.read_bytes()
    result = runner.invoke(
        app,
        ["work", "orchestrate", slug, "--live", f"{slug}#plan, other#design", "--workspace", str(workspace), "--json"],
    )
    assert result.exit_code == exit_codes.SUCCESS, result.output
    assert json.loads(result.stdout)["live"] == [f"{slug}#plan", "other#design"]
    assert page.read_bytes() == before


def test_orchestrate_calls_the_provenance_guard(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(work_main, "warn_if_stale_routing", lambda: calls.append("guard"))
    write_item(workspace, "2026-08-01-feature-a")
    runner.invoke(app, ["work", "orchestrate", "2026-08-01-feature-a", "--workspace", str(workspace)])
    assert calls == ["guard"]


def test_orchestrate_maps_a_workspace_error_to_schema_mismatch(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise work_main.WorkspaceError("workflow.auto_drive.max_parallel: expected an int")

    monkeypatch.setattr(work_main, "run_orchestrate", boom)
    write_item(workspace, "2026-08-01-feature-a")
    result = runner.invoke(app, ["work", "orchestrate", "2026-08-01-feature-a", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.SCHEMA_MISMATCH


def test_orchestrate_run_orchestrate_value_error_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise ValueError("bad routing table")

    monkeypatch.setattr(work_main, "run_orchestrate", boom)
    write_item(workspace, "2026-08-01-feature-a")
    result = runner.invoke(app, ["work", "orchestrate", "2026-08-01-feature-a", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.AMBIGUOUS
    assert "bad routing table" in result.stderr


def test_orchestrate_run_orchestrate_os_error_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("disk gone")

    monkeypatch.setattr(work_main, "run_orchestrate", boom)
    write_item(workspace, "2026-08-01-feature-a")
    result = runner.invoke(app, ["work", "orchestrate", "2026-08-01-feature-a", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert "disk gone" in result.stderr


def test_orchestrate_human_output_renders_dispatches(workspace: Path) -> None:
    write_item(workspace, "2026-08-01-feature-a", phase="plan")
    result = runner.invoke(app, ["work", "orchestrate", "2026-08-01-feature-a", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    assert "terminal=" in result.stdout
    assert "dispatch" in result.stdout


def test_reconcile_context_degrades_to_warnings_and_still_succeeds(workspace: Path) -> None:
    slug = "2026-08-01-feature-a"
    write_item(workspace, slug, phase="design")
    result = runner.invoke(app, ["work", "reconcile-context", slug, "--workspace", str(workspace), "--json"])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    payload = json.loads(result.stdout)
    assert sorted(payload) == [
        "anchor_source",
        "cited_decisions",
        "commit_range",
        "commits_since",
        "contradictions",
        "diff_command",
        "epic_slug",
        "has_open_decision",
        "landed_siblings",
        "slug",
        "spec_anchor_commit",
        "spec_path",
        "touched_paths",
        "warnings",
    ]
    assert payload["warnings"]


def test_reconcile_context_unknown_slug_exits_ambiguous(workspace: Path) -> None:
    result = runner.invoke(app, ["work", "reconcile-context", "missing", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.AMBIGUOUS


def test_reconcile_context_rejects_a_repo_that_is_not_a_git_repository(workspace: Path, tmp_path: Path) -> None:
    write_item(workspace, "2026-08-01-feature-a", phase="design")
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    result = runner.invoke(
        app,
        ["work", "reconcile-context", "2026-08-01-feature-a", "--repo", str(not_a_repo), "--workspace", str(workspace)],
    )
    assert result.exit_code == exit_codes.NOT_IN_GIT_REPO
    assert str(not_a_repo) in result.stderr


def test_reconcile_context_writes_nothing(workspace: Path) -> None:
    slug = "2026-08-01-feature-a"
    page = write_item(workspace, slug, phase="design")
    before = page.read_bytes()
    runner.invoke(app, ["work", "reconcile-context", slug, "--workspace", str(workspace)])
    assert page.read_bytes() == before


def test_reconcile_context_accepts_an_explicit_git_repository(workspace: Path, tmp_path: Path) -> None:
    write_item(workspace, "2026-08-01-feature-a", phase="design")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    result = runner.invoke(
        app,
        [
            "work",
            "reconcile-context",
            "2026-08-01-feature-a",
            "--repo",
            str(repo),
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert result.exit_code == exit_codes.SUCCESS, result.output


def test_reconcile_context_maps_a_workspace_error_to_schema_mismatch(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import graph_works_cli.work_cli.reconcile as reconcile_module

    def boom(*_args: object, **_kwargs: object) -> object:
        raise reconcile_module.WorkspaceError("bad config")

    monkeypatch.setattr(reconcile_module, "run_reconcile_context", boom)
    write_item(workspace, "2026-08-01-feature-a", phase="design")
    result = runner.invoke(app, ["work", "reconcile-context", "2026-08-01-feature-a", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.SCHEMA_MISMATCH


def test_reconcile_context_run_reconcile_context_os_error_is_reported(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import graph_works_cli.work_cli.reconcile as reconcile_module

    def boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("disk gone")

    monkeypatch.setattr(reconcile_module, "run_reconcile_context", boom)
    write_item(workspace, "2026-08-01-feature-a", phase="design")
    result = runner.invoke(app, ["work", "reconcile-context", "2026-08-01-feature-a", "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert "disk gone" in result.stderr


def test_reconcile_context_human_output_renders(workspace: Path) -> None:
    slug = "2026-08-01-feature-a"
    write_item(workspace, slug, phase="design")
    result = runner.invoke(app, ["work", "reconcile-context", slug, "--workspace", str(workspace)])
    assert result.exit_code == exit_codes.SUCCESS, result.output
    assert f"{slug}: epic=" in result.stdout
