"""Filesystem shell for canonical orchestration plans and stage mutations."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import layout_for
from okf_io import load

TODAY = date(2026, 8, 23)


def _workspace(tmp_path: Path, manifest: str = "version: 1\n"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace.yaml").write_text(manifest, encoding="utf-8")
    layout = layout_for(tmp_path)
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    layout.cache_dir.mkdir(parents=True, exist_ok=True)
    return layout


def _write(layout, path: str, *, type: str = "Feature", phase: str = "plan", work_status: str = "open") -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: {type}\ntitle: {path}\ndescription: d\nstatus: stable\n"
        f"work_status: {work_status}\nphase: {phase}\neffort: medium\nopened: 2026-08-01\n"
        "updated: 2026-08-01\naffects:\n- packages/a\n---\n\n## Summary\nd\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
    )


def _initialized_workspace(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "packages/a").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Orchestrate")).layout
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def test_lone_item_shell_returns_canonical_dispatch(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    path = "work/feature-a"
    _write(layout, path)
    result = orchestrate.run_orchestrate(layout, path)
    assert [dispatch.slug for dispatch in result.dispatches] == [path]
    assert result.path == path
    assert result.decisions_owner_path == path


def test_orchestration_keys_and_live_tokens_use_full_paths(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    path = "work/release-cutover"
    _write(layout, path)
    result = orchestrate.run_orchestrate(layout, path, live=(f"{path}#plan",))
    assert all(dispatch.key.startswith("work/") and "#" in dispatch.key for dispatch in result.plan.dispatches)
    assert result.live == (f"{path}#plan",)


def test_nearest_feature_owns_decision_context(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    owner = "work/epic-a/children/feature-a"
    child = f"{owner}/children/bug-a"
    _write(layout, "work/epic-a", type="Epic", phase="execute")
    _write(layout, owner, phase="execute")
    _write(layout, child, type="Bug", phase="design")
    result = orchestrate.run_orchestrate(layout, child)
    assert result.decisions_owner_path == owner
    assert result.decisions_ledger_path == str(layout.bundle_dir / f"{owner}/references/00-decisions.md")


def test_open_decision_blocks_exact_canonical_path(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    owner = "work/epic-a"
    child = f"{owner}/children/feature-a"
    _write(layout, owner, type="Epic", phase="execute")
    _write(layout, child, phase="design")
    ledger = layout.bundle_dir / f"{child}/references/00-decisions.md"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(f"# Decisions\n\n## D-001 — question\nstatus: open\naffects: [{child}]\n", encoding="utf-8")
    result = orchestrate.run_orchestrate(layout, child)
    assert [(blocked.path, blocked.kind) for blocked in result.blocked] == [(child, "decisions")]


def test_bad_manifest_scalar_refuses(tmp_path: Path) -> None:
    layout = _workspace(tmp_path, 'version: 1\nworkflow:\n  auto_drive:\n    max_parallel: "4"\n')
    _write(layout, "work/feature-a")
    with pytest.raises(WorkspaceError, match="expects an integer"):
        orchestrate.run_orchestrate(layout, "work/feature-a")


def test_dry_run_stage_advance_writes_nothing(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    path = "work/feature-a"
    _write(layout, path)
    before = (layout.bundle_dir / f"{path}.md").read_bytes()
    result = orchestrate.run_stage_advance(layout, path, today=TODAY)
    assert result.application is None
    assert (layout.bundle_dir / f"{path}.md").read_bytes() == before


def test_live_stage_advance_is_journaled_and_path_pointer_is_written(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    path = "work/feature-a"
    _write(layout, path)
    from graph_works_core.work import commands as work

    assert work.run_regen_indexes(layout, dry_run=False).application.ok
    result = orchestrate.run_stage_advance(layout, path, today=TODAY, dry_run=False)
    assert result.application is not None and result.application.ok
    assert result.application.journal.is_file()
    assert load(layout.bundle_dir / f"{path}.md").fm_data()["phase"] == "execute"
    assert json.loads((layout.cache_dir / "active-work.json").read_text(encoding="utf-8"))["path"] == path


def test_live_stage_advance_forwards_explicit_worktree_stamping(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    path = "work/feature-a"
    _write(layout, path)
    from graph_works_core.work import commands as work

    assert work.run_regen_indexes(layout, dry_run=False).application.ok
    result = orchestrate.run_stage_advance(
        layout,
        path,
        today=TODAY,
        worktree="/wt/feature-a",
        branch="feature/a",
        dry_run=False,
    )
    assert result.application is not None and result.application.ok
    changes = {change.key: change.after for change in result.outcome.plan.changes}
    assert changes["worktree"] == "/wt/feature-a"
    assert changes["branch"] == "feature/a"
    written = load(layout.bundle_dir / f"{path}.md").fm_data()
    assert written["worktree"] == "/wt/feature-a"
    assert written["branch"] == "feature/a"


def test_refused_stage_advance_writes_no_page_pointer_or_journal(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    epic = "work/epic-a"
    child = f"{epic}/children/feature-a"
    _write(layout, epic, type="Epic", phase="execute")
    _write(layout, child)
    page = layout.bundle_dir / f"{epic}.md"
    before = page.read_bytes()
    result = orchestrate.run_stage_advance(layout, epic, today=TODAY, dry_run=False)
    assert result.outcome.plan.refusal is not None
    assert result.application is None
    assert result.results_path is None and result.pointer_path is None
    assert page.read_bytes() == before
    assert not (layout.cache_dir / "active-work.json").exists()


def test_release_finish_forwards_released_at_to_the_domain_plan(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    path = "work/release-v1"
    _write(layout, path, type="Release", phase="finish")
    result = orchestrate.run_stage_advance(layout, path, today=TODAY, released_at=TODAY)
    assert result.outcome.plan.refusal is None
    assert any(change.key == "released_at" and change.after == TODAY for change in result.outcome.plan.changes)


def test_stage_advance_unknown_path_returns_domain_refusal(tmp_path: Path) -> None:
    result = orchestrate.run_stage_advance(_workspace(tmp_path), "work/missing", today=TODAY)
    assert result.outcome.plan.refusal == "unknown-path"


def test_terminal_orchestration_short_circuits(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    path = "work/feature-a"
    _write(layout, path, phase="done", work_status="resolved")
    assert orchestrate.run_orchestrate(layout, path).terminal is True


def test_explicit_repo_skips_declared_repo_resolution(tmp_path: Path, monkeypatch) -> None:
    layout = _workspace(tmp_path / "workspace")
    repo = tmp_path / "code"
    repo.mkdir()
    path = "work/feature-a"
    _write(layout, path)
    monkeypatch.setattr(orchestrate, "resolve_repo", lambda *args, **kwargs: pytest.fail("must not resolve"))
    monkeypatch.setattr(orchestrate, "_checkout_is_dirty", lambda candidate: False)
    monkeypatch.setattr(orchestrate, "default_base", lambda candidate: "trunk")
    result = orchestrate.run_orchestrate(layout, path, repo=repo)
    assert result.warnings == ()
    assert result.dispatches[0].worktree.action == "main"
    assert result.dispatches[0].worktree.path == str(repo)
    assert result.dispatches[0].merge_target == "trunk"


def test_declared_repo_resolution_note_is_preserved(tmp_path: Path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    path = "work/feature-a"
    _write(layout, path)
    monkeypatch.setattr(orchestrate, "resolve_repo", lambda *args, **kwargs: (None, "repo unavailable"))
    result = orchestrate.run_orchestrate(layout, path)
    assert "repo unavailable" in result.warnings
