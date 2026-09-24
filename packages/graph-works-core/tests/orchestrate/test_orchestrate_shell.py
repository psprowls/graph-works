"""Filesystem shell for canonical orchestration plans and stage mutations."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.orchestrate import stage_advance as stage
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import layout_for
from graph_works_core.workspace.repos import ItemRepo
from okf_io import load

TODAY = date(2026, 8, 23)


def _workspace(tmp_path: Path, manifest: str = "version: 1\n"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    if "workflow:" in manifest:
        manifest = manifest.replace("workflow:\n", "workflow:\n  dispatch_rules: dispatch.yaml\n")
    else:
        manifest += "workflow:\n  dispatch_rules: dispatch.yaml\n"
    (tmp_path / "dispatch.yaml").write_text("pipeline:\n  rules: []\n", encoding="utf-8")
    (tmp_path / "workspace.yaml").write_text(manifest, encoding="utf-8")
    layout = layout_for(tmp_path)
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    layout.cache_dir.mkdir(parents=True, exist_ok=True)
    return layout


def _write(
    layout,
    path: str,
    *,
    type: str = "Feature",
    phase: str | None = "plan",
    work_status: str = "open",
    affects: tuple[str, ...] = ("packages/a",),
) -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    rendered = "affects: []" if not affects else "affects:\n" + "".join(f"- {entry}\n" for entry in affects).rstrip()
    phase_line = f"phase: {phase}\n" if phase is not None else ""
    page.write_text(
        f"---\ntype: {type}\ntitle: {path}\ndescription: d\nstatus: stable\n"
        f"work_status: {work_status}\n{phase_line}effort: medium\nopened: 2026-08-01\n"
        f"updated: 2026-08-01\n{rendered}\n---\n\n## Summary\nd\n\n## Plan\n\n"
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


def _declare_repo(monkeypatch, tmp_path: Path) -> Path:
    """Declare a real code repository for shell dispatch tests."""
    code = _git_repo(tmp_path / "code")
    monkeypatch.setattr(orchestrate, "resolve_item_repo", lambda *args, **kwargs: ItemRepo("code", code, "sole"))
    return code


def test_lone_item_shell_returns_canonical_dispatch(tmp_path: Path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    path = "work/feature-a"
    _write(layout, path, phase="design")
    _declare_repo(monkeypatch, tmp_path)
    result = orchestrate.run_orchestrate(layout, path)
    assert [dispatch.slug for dispatch in result.dispatches] == [path]
    assert result.path == path
    assert result.decisions_owner_path == path


def test_orchestration_keys_are_session_names_and_live_tokens_match(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    path = "work/release-cutover"
    _write(layout, path)
    live = orchestrate.session_name(path, "Feature", "plan")
    result = orchestrate.run_orchestrate(layout, path, live=(live,))
    assert all(dispatch.key.startswith("gw-") and "#" not in dispatch.key for dispatch in result.plan.dispatches)
    assert all(dispatch.slug.startswith("work/") for dispatch in result.plan.dispatches)
    assert result.live == (live,)
    assert not [w for w in result.plan.warnings if "matches no known item" in w]


def test_an_unknown_live_key_refuses_the_shell_plan(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    path = "work/release-cutover"
    _write(layout, path)
    with pytest.raises(ValueError, match="gw-plan-nobody-00000000"):
        orchestrate.run_orchestrate(layout, path, live=("gw-plan-nobody-00000000",))


def test_nearest_feature_owns_decision_context(tmp_path: Path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    owner = "work/epic-a/children/feature-a"
    child = f"{owner}/children/bug-a"
    _write(layout, "work/epic-a", type="Epic", phase="execute")
    _write(layout, owner, phase="execute")
    _write(layout, child, type="Bug", phase="design")
    _declare_repo(monkeypatch, tmp_path)
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
    result = stage.run_stage_advance(layout, path, today=TODAY)
    assert result.application is None
    assert (layout.bundle_dir / f"{path}.md").read_bytes() == before


def _pointer(layout) -> dict[str, str] | None:
    target = layout.cache_dir / "active-work.json"
    return json.loads(target.read_text(encoding="utf-8")) if target.exists() else None


def test_live_stage_exit_is_journaled_and_does_not_stamp_the_pointer(tmp_path: Path) -> None:
    """An exit transition runs inside the session it ends: stamping the next
    phase here is what mislabelled that session's transcript."""
    layout = _initialized_workspace(tmp_path)
    path = "work/feature-a"
    _write(layout, path)
    from graph_works_core.work import commands as work

    assert work.run_regen_indexes(layout, dry_run=False).application.ok
    result = stage.run_stage_advance(layout, path, today=TODAY, dry_run=False)
    assert result.application is not None and result.application.ok
    assert result.application.journal.is_file()
    assert result.outcome.plan.trigger == "complete"
    assert load(layout.bundle_dir / f"{path}.md").fm_data()["phase"] == "execute"
    assert result.pointer_path is None
    assert _pointer(layout) is None


def test_live_stage_exit_leaves_an_existing_pointer_on_the_session_phase(tmp_path: Path) -> None:
    from graph_works_core.work import commands as work
    from graph_works_core.workspace import provenance

    layout = _initialized_workspace(tmp_path)
    path = "work/feature-a"
    _write(layout, path)
    assert work.run_regen_indexes(layout, dry_run=False).application.ok
    provenance.write_active_work(layout, path, "plan", updated=TODAY.isoformat())
    stage.run_stage_advance(layout, path, today=TODAY, dry_run=False)
    assert _pointer(layout) == {"path": path, "phase": "plan", "updated": TODAY.isoformat()}


def test_live_dispatch_transition_stamps_the_entered_phase(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    path = "work/bug-a"
    _write(layout, path, type="Bug", phase=None)
    from graph_works_core.work import commands as work

    assert work.run_regen_indexes(layout, dry_run=False).application.ok
    result = stage.run_stage_advance(layout, path, today=TODAY, dry_run=False)
    assert result.outcome.plan.trigger == "dispatch"
    assert result.pointer_path == layout.cache_dir / "active-work.json"
    assert _pointer(layout) == {"path": path, "phase": "design", "updated": TODAY.isoformat()}


def test_execute_dispatch_transition_without_a_phase_change_still_stamps(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    path = "work/feature-a"
    _write(layout, path, phase="execute", work_status="accepted")
    from graph_works_core.work import commands as work

    assert work.run_regen_indexes(layout, dry_run=False).application.ok
    result = stage.run_stage_advance(layout, path, today=TODAY, owner="pat", dry_run=False)
    assert result.application is not None and result.application.ok
    assert result.outcome.plan.trigger == "dispatch"
    assert _pointer(layout) == {"path": path, "phase": "execute", "updated": TODAY.isoformat()}


def test_return_transition_does_not_restamp_the_pointer(tmp_path: Path) -> None:
    from graph_works_core.work import commands as work
    from graph_works_core.workspace import provenance

    layout = _initialized_workspace(tmp_path)
    path = "work/feature-a"
    _write(layout, path, phase="finish", work_status="in-progress")
    assert work.run_regen_indexes(layout, dry_run=False).application.ok
    provenance.write_active_work(layout, path, "finish", updated=TODAY.isoformat())
    result = stage.run_stage_advance(layout, path, today=TODAY, return_=True, dry_run=False)
    assert result.application is not None and result.application.ok
    assert result.outcome.plan.trigger == "return"
    assert result.pointer_path is None
    assert _pointer(layout) == {"path": path, "phase": "finish", "updated": TODAY.isoformat()}


def test_live_stage_advance_forwards_explicit_worktree_stamping(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    path = "work/feature-a"
    _write(layout, path)
    from graph_works_core.work import commands as work

    assert work.run_regen_indexes(layout, dry_run=False).application.ok
    result = stage.run_stage_advance(
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
    result = stage.run_stage_advance(layout, epic, today=TODAY, dry_run=False)
    assert result.outcome.plan.refusal is not None
    assert result.application is None
    assert result.results_path is None and result.pointer_path is None
    assert page.read_bytes() == before
    assert not (layout.cache_dir / "active-work.json").exists()


def test_release_finish_forwards_released_at_to_the_domain_plan(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    path = "work/release-v1"
    _write(layout, path, type="Release", phase="finish")
    result = stage.run_stage_advance(layout, path, today=TODAY, released_at=TODAY)
    assert result.outcome.plan.refusal is None
    assert any(change.key == "released_at" and change.after == TODAY for change in result.outcome.plan.changes)


def test_stage_advance_unknown_path_returns_domain_refusal(tmp_path: Path) -> None:
    result = stage.run_stage_advance(_workspace(tmp_path), "work/missing", today=TODAY)
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
    _write(layout, path, phase="design")
    monkeypatch.setattr(orchestrate, "resolve_item_repo", lambda *args, **kwargs: pytest.fail("must not resolve"))
    monkeypatch.setattr(orchestrate, "_checkout_is_dirty", lambda candidate: False)
    monkeypatch.setattr(orchestrate, "default_base", lambda candidate: "trunk")
    result = orchestrate.run_orchestrate(layout, path, repo=repo)
    assert result.warnings == ()
    # Cold start mints the epic worktree even with an explicit repo path known
    # (rule 4a is deleted) -- `resolve_item_repo` still must not be called, since
    # `repo=` bypasses declared-repo resolution regardless of placement.
    assert result.dispatches[0].worktree.action == "create-top-level"
    assert result.dispatches[0].worktree.path is None
    assert result.dispatches[0].merge_target == "trunk"
    assert result.code_repo == str(repo)


def test_declared_repo_resolution_note_is_preserved(tmp_path: Path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    path = "work/feature-a"
    _write(layout, path)
    monkeypatch.setattr(
        orchestrate, "resolve_item_repo", lambda *args, **kwargs: ItemRepo(None, None, "sole", "repo unavailable")
    )
    result = orchestrate.run_orchestrate(layout, path)
    assert "repo unavailable" in result.warnings


def test_the_declared_code_repo_is_reported_even_when_its_checkout_is_withheld(tmp_path: Path, monkeypatch) -> None:
    layout = _workspace(tmp_path / "workspace")
    path = "work/feature-a"
    _write(layout, path, phase="design")
    code = _declare_repo(monkeypatch, tmp_path)
    (code / "dirty.txt").write_text("local edit\n", encoding="utf-8")
    result = orchestrate.run_orchestrate(layout, path)
    assert result.code_repo == str(code)
    assert result.dispatches[0].worktree.action == "create-top-level"


def test_no_declared_code_repo_reports_null_and_blocks_a_worktree_creation(tmp_path: Path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    path = "work/feature-a"
    _write(layout, path, phase="design")
    monkeypatch.setattr(
        orchestrate, "resolve_item_repo", lambda *args, **kwargs: ItemRepo(None, None, "sole", "repo unavailable")
    )
    result = orchestrate.run_orchestrate(layout, path)
    assert result.code_repo is None
    assert "repo unavailable" in result.warnings
    assert result.dispatches == ()
    assert [blocked.kind for blocked in result.blocked] == ["worktree-unprovable"]


def test_worktree_inventory_parses_porcelain_output(monkeypatch) -> None:
    porcelain = (
        "worktree /repo\n"
        "HEAD 8a1f0000000000000000000000000000000000aa\n"
        "branch refs/heads/main\n"
        "\n"
        "worktree /wt/bug-x\n"
        "HEAD 3c2d0000000000000000000000000000000000bb\n"
        "branch refs/heads/psprowls/bug-x-1a2b3c4d\n"
        "\n"
        "worktree /wt/detached\n"
        "HEAD 9f0e0000000000000000000000000000000000cc\n"
        "detached\n"
        "\n"
    )
    monkeypatch.setattr(orchestrate, "run_git", lambda cwd, *args: porcelain)

    inventory = orchestrate._worktree_inventory(Path("/repo"))

    assert inventory == {
        "main": "/repo",
        "psprowls/bug-x-1a2b3c4d": "/wt/bug-x",
    }


def test_worktree_inventory_drops_a_prunable_record(monkeypatch) -> None:
    """`prunable <reason>` appears *after* the record's `branch` line, keeps
    listing a worktree whose directory has already been deleted, until
    `git worktree prune` runs. Such a record must not be admitted -- adopting
    it would report a gone directory as existing."""
    porcelain = (
        "worktree /repo\n"
        "HEAD 8a1f0000000000000000000000000000000000aa\n"
        "branch refs/heads/main\n"
        "\n"
        "worktree /wt1\n"
        "HEAD 5323a37f00000000000000000000000000000000\n"
        "branch refs/heads/feat1\n"
        "prunable gitdir file points to non-existent location\n"
        "\n"
        "worktree /wt/bug-x\n"
        "HEAD 3c2d0000000000000000000000000000000000bb\n"
        "branch refs/heads/psprowls/bug-x-1a2b3c4d\n"
        "\n"
    )
    monkeypatch.setattr(orchestrate, "run_git", lambda cwd, *args: porcelain)

    inventory = orchestrate._worktree_inventory(Path("/repo"))

    assert inventory == {
        "main": "/repo",
        "psprowls/bug-x-1a2b3c4d": "/wt/bug-x",
    }
    assert "feat1" not in inventory


def test_worktree_inventory_degrades_to_empty(monkeypatch) -> None:
    assert orchestrate._worktree_inventory(None) == {}

    monkeypatch.setattr(orchestrate, "run_git", lambda cwd, *args: None)
    assert orchestrate._worktree_inventory(Path("/repo")) == {}


def test_the_planner_half_does_not_re_export_the_stage_advance_half() -> None:
    """D-001's whole point: a re-export would be a surface both lanes still
    have a reason to edit, putting the collision straight back."""
    for name in ("run_stage_advance", "StageAdvance", "RESULTS_PHASES", "_facts_root"):
        assert not hasattr(orchestrate, name), name
        assert hasattr(stage, name), name


def _code_repo(tmp_path: Path) -> tuple[Path, str]:
    """A repo forked off `main`, with one commit touching `packages/a`."""
    import subprocess

    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)

    repo = tmp_path / "code"
    (repo / "packages/a").mkdir(parents=True)
    run("init", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "T")
    (repo / "packages/a/x.py").write_text("one\n", encoding="utf-8")
    run("add", ".")
    run("commit", "-m", "first")
    fork = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    run("checkout", "-b", "feature/a")
    (repo / "packages/a/x.py").write_text("two\n", encoding="utf-8")
    run("commit", "-am", "second")
    return repo, fork


def _ready(layout, path: str, *, phase: str = "execute", **kwargs) -> None:
    """An item mid-stage, ready to complete `phase` without further gates:
    `work_status="in-progress"` so the execute branch's on_dispatch (mark
    in-progress, requires owner) has already fired and routing reaches the
    on_complete transition this task's derivation logic needs to exercise."""
    from graph_works_core.work import commands as work

    _write(layout, path, phase=phase, work_status="in-progress", **kwargs)
    assert work.run_regen_indexes(layout, dry_run=False).application.ok


def test_an_explicit_start_sha_writes_the_execute_results_stub(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path)
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, start_sha=fork, dry_run=False)
    assert result.results_path is not None and result.results_path.is_file()
    assert "Execute — results" in result.results_path.read_text(encoding="utf-8")


def test_the_execute_stage_derives_a_start_sha_with_no_flag(tmp_path: Path) -> None:
    """D-002's acceptance bar: a plain advance writes the stub."""
    layout = _initialized_workspace(tmp_path)
    repo, _fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path)
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, dry_run=False)
    assert result.results_path is not None and result.results_path.is_file()


def test_the_finish_stage_never_derives_a_start_sha(tmp_path: Path) -> None:
    """A derived range at finish sweeps in the whole execute range and would
    claim work the stage did not do."""
    layout = _initialized_workspace(tmp_path)
    repo, _fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path, phase="finish")
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, resolved_in="pr-1", dry_run=False)
    assert result.results_path is None


def test_an_explicit_start_sha_still_works_at_finish(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path, phase="finish")
    result = stage.run_stage_advance(
        layout, path, today=TODAY, repo=repo, start_sha=fork, resolved_in="pr-1", dry_run=False
    )
    assert result.results_path is not None and result.results_path.is_file()


def _base_branch_repo(tmp_path: Path) -> Path:
    """A repo left checked out on its own base branch -- `HEAD` never moves
    off `main`, so `merge_base(default_base, HEAD)` answers `HEAD` itself: an
    empty range, the shape a `gw work advance` from the main checkout has."""
    import subprocess

    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)

    repo = tmp_path / "code"
    (repo / "packages/a").mkdir(parents=True)
    run("init", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "T")
    (repo / "packages/a/x.py").write_text("one\n", encoding="utf-8")
    run("add", ".")
    run("commit", "-m", "first")
    return repo


def test_a_derived_start_sha_equal_to_head_still_writes_a_stub_carrying_the_empty_range_warning(
    tmp_path: Path,
) -> None:
    """Spec's own Testing bar: a merge-base equal to `HEAD` (main-mode, no
    `--start-sha`) produces an empty range whose stub still carries
    `render()`'s existing "Range is empty" warning -- the controller ruling is
    to keep writing the stub, not decline it."""
    layout = _initialized_workspace(tmp_path)
    repo = _base_branch_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path)
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, dry_run=False)
    assert result.results_path is not None and result.results_path.is_file()
    text = result.results_path.read_text(encoding="utf-8")
    assert "Range is empty: the stage recorded no commits in this scope" in text


def test_a_derivation_that_finds_nothing_writes_no_stub(tmp_path: Path, monkeypatch) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, _fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path)
    monkeypatch.setattr(stage.anchor, "phase_start_sha", lambda *args: None)
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, dry_run=False)
    assert result.results_path is None
    assert result.outcome.changed


# --- the execute -> finish commit gate ------------------------------------


def _dirty(repo: Path, member: str = "packages/a/left-behind.py") -> None:
    """Leave one uncommitted file in *repo* -- the shape all three recorded
    reproductions took."""
    target = repo / member
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("uncommitted\n", encoding="utf-8")


def test_a_dirty_execute_stage_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, _fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path)
    _dirty(repo)
    page = layout.bundle_dir / f"{path}.md"
    before = page.read_bytes()
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, dry_run=False)
    assert result.outcome.plan.refusal == "uncommitted-work"
    assert "packages/a/left-behind.py" in result.outcome.plan.detail
    assert result.outcome.plan.trigger is None
    assert page.read_bytes() == before
    assert result.results_path is None and result.pointer_path is None
    assert result.application is None


def test_dirt_outside_the_declared_affects_does_not_refuse(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, _fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path)
    _dirty(repo, "elsewhere/unrelated.py")
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, dry_run=False)
    assert result.outcome.plan.refusal is None


def test_an_execute_stage_with_no_commits_in_an_explicit_range_is_refused(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, _fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path)
    head = stage.provenance.head_sha(repo)
    assert head is not None
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, start_sha=head, dry_run=False)
    assert result.outcome.plan.refusal == "no-commits"


def test_a_clean_execute_stage_with_commits_advances_unchanged(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path)
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, start_sha=fork, dry_run=False)
    assert result.outcome.plan.refusal is None
    assert result.outcome.written


def test_an_unevaluable_gate_proceeds_and_names_the_missing_input(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    path = "work/feature-a"
    _write(layout, path, phase="execute", work_status="in-progress")
    result = stage.run_stage_advance(layout, path, today=TODAY, dry_run=False)
    assert result.outcome.plan.refusal is None
    assert any("no code repository" in warning for warning in result.warnings)


def test_an_empty_affects_leaves_the_gate_unevaluable_and_warns(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, _fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path, affects=())
    _dirty(repo)
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, dry_run=False)
    assert result.outcome.plan.refusal is None
    assert any("affects" in warning for warning in result.warnings)


def test_the_gate_does_not_fire_on_a_dry_run(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, _fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path)
    _dirty(repo)
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, dry_run=True)
    assert result.outcome.plan.refusal is None


@pytest.mark.parametrize("phase", ["plan", "finish"])
def test_the_gate_fires_only_at_the_execute_boundary(tmp_path: Path, phase: str) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, _fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path, phase=phase)
    _dirty(repo)
    result = stage.run_stage_advance(
        layout, path, today=TODAY, repo=repo, resolved_in="pr-1" if phase == "finish" else None, dry_run=False
    )
    assert result.outcome.plan.refusal is None


def test_returning_an_item_at_finish_moves_it_back_to_execute(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, _fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path, phase="finish")
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, return_=True, dry_run=False)
    assert result.outcome.plan.refusal is None
    assert result.outcome.written
    assert "phase: execute" in (layout.bundle_dir / f"{path}.md").read_text(encoding="utf-8")
    assert result.results_path is None


def test_returning_an_item_that_is_not_at_finish_is_refused(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, _fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path)
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, return_=True, dry_run=False)
    assert result.outcome.plan.refusal == "return-not-available"


def test_a_worktree_that_is_not_a_repo_leaves_the_gate_unevaluable(tmp_path: Path) -> None:
    """`git status` itself failing is the third fail-open case: the gate says
    so rather than passing silently."""
    layout = _initialized_workspace(tmp_path)
    not_a_repo = tmp_path / "loose"
    (not_a_repo / "packages/a").mkdir(parents=True)
    path = "work/feature-a"
    _ready(layout, path)
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=not_a_repo, dry_run=False)
    assert result.outcome.plan.refusal is None
    assert any("git status" in warning for warning in result.warnings)


def test_an_unreadable_commit_range_leaves_the_zero_commits_signal_unevaluable(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, _fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path)
    fabricated = "0123456789abcdef0123456789abcdef01234567"
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, start_sha=fabricated, dry_run=False)
    assert result.outcome.plan.refusal is None
    assert any(fabricated in warning for warning in result.warnings)


def _net_zero_range(repo: Path) -> str:
    """Commit work under `packages/a` and then revert it, returning the sha the
    range starts from. `git log start..HEAD` reports two commits while
    `git diff start..HEAD` reports no files -- the only shape where the
    `no-commits` and `no-affects-touched` signals genuinely diverge."""
    import subprocess

    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)

    start = stage.provenance.head_sha(repo)
    assert start is not None
    (repo / "packages/a/added.py").write_text("x = 1\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-m", "add x")
    (repo / "packages/a/added.py").unlink()
    run("add", "-A")
    run("commit", "-m", "revert x")
    return start


def test_commits_that_touch_nothing_in_scope_are_refused(tmp_path: Path) -> None:
    """The net-zero case: work committed and then reverted inside the range, so
    `git log` reports commits while `git diff start..HEAD` reports no files.
    This is the only shape where the two signals genuinely diverge, which is
    why it is the signal's central test rather than an edge case."""
    layout = _initialized_workspace(tmp_path)
    repo, _fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path)
    start = _net_zero_range(repo)
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, start_sha=start, dry_run=False)
    assert result.outcome.plan.refusal == "no-affects-touched"
    assert "packages/a" in result.outcome.plan.detail
    assert result.outcome.plan.changes == ()


def test_zero_commits_reports_no_commits_not_no_affects_touched(tmp_path: Path) -> None:
    """Precedence: an item that committed nothing should be told it committed
    nothing. Most-specific diagnosis last -- an empty range has an empty file
    list too, so the ordering is what keeps the diagnosis honest."""
    layout = _initialized_workspace(tmp_path)
    repo, _fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path)
    start = stage.provenance.head_sha(repo)
    assert start is not None
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, start_sha=start, dry_run=False)
    assert result.outcome.plan.refusal == "no-commits"


def test_commits_touching_the_declared_scope_advance(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path)
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, start_sha=fork, dry_run=False)
    assert result.outcome.plan.refusal is None
    assert result.outcome.plan.transition is not None
    assert result.outcome.plan.transition.phase == "finish"


def test_an_unevaluable_signal_advances_and_names_the_missing_input(tmp_path: Path) -> None:
    """Fail-open, loudly. `gw work advance` runs against workspaces with no code
    repo at all; a fail-closed unevaluable gate would break the pipeline
    everywhere for a condition it cannot even observe."""
    layout = _initialized_workspace(tmp_path)
    repo, _fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path)
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, start_sha=None, dry_run=False)
    assert result.outcome.plan.refusal is None
    assert any("start_sha" in warning for warning in result.warnings)


def test_an_item_with_no_declared_surface_advances_and_warns(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path, affects=())
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, start_sha=fork, dry_run=False)
    assert result.outcome.plan.refusal is None
    assert any("affects" in warning for warning in result.warnings)


@pytest.mark.parametrize("phase", ["plan", "finish"])
def test_the_signal_does_not_fire_outside_execute_to_finish(tmp_path: Path, phase: str) -> None:
    layout = _initialized_workspace(tmp_path / phase)
    repo = tmp_path / phase / "c"
    repo, _fork = _code_repo(repo)
    path = "work/feature-a"
    _ready(layout, path, phase=phase)
    start = _net_zero_range(repo)
    result = stage.run_stage_advance(
        layout, path, today=TODAY, repo=repo, start_sha=start, resolved_in="pr-1", dry_run=False
    )
    assert result.outcome.plan.refusal != "no-affects-touched"


def test_the_signal_does_not_fire_on_a_dry_run(tmp_path: Path) -> None:
    """A dry run inspects no worktree and refuses nothing -- the same rule the
    other two signals are bound by."""
    layout = _initialized_workspace(tmp_path)
    repo, _fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path)
    start = _net_zero_range(repo)
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, start_sha=start, dry_run=True)
    assert result.outcome.plan.refusal is None


def test_a_refusal_writes_nothing(tmp_path: Path) -> None:
    """Refusal is inert: no page write, no results stub, no coverage source, no
    active-work pointer. The gate runs after the advance is planned and before
    the mutation is composed, so this is structural -- but it is the property
    the whole design rests on, so it is asserted, not assumed."""
    layout = _initialized_workspace(tmp_path)
    repo, _fork = _code_repo(tmp_path / "c")
    path = "work/feature-a"
    _ready(layout, path)
    start = _net_zero_range(repo)
    page = layout.bundle_dir / f"{path}.md"
    before = page.read_bytes()
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, start_sha=start, dry_run=False)
    assert result.outcome.plan.refusal == "no-affects-touched"
    assert page.read_bytes() == before
    assert result.results_path is None
    assert result.pointer_path is None
    assert result.application is None
    assert not (layout.bundle_dir / path / "references/03-execute-results.md").exists()


def test_an_execute_advance_registers_a_present_coverage_file(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    path = "work/feature-a"
    _ready(layout, path, phase="execute")
    coverage = layout.bundle_dir / path / "references/03-execute-coverage.md"
    coverage.parent.mkdir(parents=True, exist_ok=True)
    coverage.write_text("- [x] one\n- [ ] two\n", encoding="utf-8")

    stage.run_stage_advance(layout, path, today=TODAY, dry_run=False)

    page = load(layout.bundle_dir / f"{path}.md")
    ids = {source.id: source.resource for source in page.fm.sources}
    assert ids["execute-coverage"] == f"/{path}/references/03-execute-coverage.md"


def test_an_execute_advance_without_a_coverage_file_is_unchanged(tmp_path: Path) -> None:
    # The obligation is unenforced: a missing file is a normal outcome, not a
    # refusal and not a warning-shaped page edit.
    layout = _initialized_workspace(tmp_path)
    path = "work/feature-b"
    _ready(layout, path, phase="execute")

    stage.run_stage_advance(layout, path, today=TODAY, dry_run=False)

    page = load(layout.bundle_dir / f"{path}.md")
    assert "execute-coverage" not in {source.id for source in page.fm.sources}


def test_the_coverage_registration_rides_the_page_write(tmp_path: Path) -> None:
    # One atomic WorkMutationPlan: the source entry cannot land without the
    # phase change, and vice versa.
    layout = _initialized_workspace(tmp_path)
    path = "work/feature-c"
    _ready(layout, path, phase="execute")
    coverage = layout.bundle_dir / path / "references/03-execute-coverage.md"
    coverage.parent.mkdir(parents=True, exist_ok=True)
    coverage.write_text("- [x] one\n", encoding="utf-8")

    result = stage.run_stage_advance(layout, path, today=TODAY, dry_run=False)

    assert result.application is not None and result.application.ok
    assert result.application.written == (f"{path}.md",)


def test_run_orchestrate_reads_supervise_merges_from_the_manifest(tmp_path: Path, monkeypatch) -> None:
    _declare_repo(monkeypatch, tmp_path)
    layout = _workspace(tmp_path)
    _write(layout, "work/feature-a", phase="design")
    assert orchestrate.run_orchestrate(layout, "work/feature-a").supervise_merges is False

    supervised = _workspace(
        tmp_path / "supervised",
        manifest="version: 1\nworkflow:\n  auto_drive:\n    supervise_merges: true\n",
    )
    _write(supervised, "work/feature-a", phase="design")
    assert orchestrate.run_orchestrate(supervised, "work/feature-a").supervise_merges is True


def _stamping_workspace(tmp_path: Path, epic_phase: str = "execute"):
    """An initialized workspace holding `work/epic-a` and one child, with
    indexes reconciled so an advance is not blocked on lint."""
    from graph_works_core.work import commands as work

    layout = _initialized_workspace(tmp_path)
    _write(layout, "work/epic-a", type="Epic", phase=epic_phase)
    _write(layout, "work/epic-a/children/feature-a", phase="plan")
    _write(layout, "work/feature-solo", phase="plan")
    assert work.run_regen_indexes(layout, dry_run=False).application.ok
    return layout


def _detects(monkeypatch, pair=("/wt/detected", "detected/branch")) -> None:
    monkeypatch.setattr(stage.provenance, "worktree_state", lambda cwd, repo: pair)


def _captured_repo_roots(monkeypatch) -> list[Path | None]:
    """Records the `repo_root` every `apply_mutation` call inside an advance
    receives, without changing its behaviour -- this is the value postcondition
    validation actually treats as the resolved repo, which nothing on
    `StageAdvance` otherwise exposes."""
    real = stage.apply_mutation
    calls: list[Path | None] = []

    def _capture(*args, **kwargs):
        calls.append(kwargs.get("repo_root"))
        return real(*args, **kwargs)

    monkeypatch.setattr(stage, "apply_mutation", _capture)
    return calls


def _stamped(result) -> dict[str, object]:
    return {change.key: change.after for change in result.outcome.plan.changes}


def _git_repo(path: Path) -> Path:
    """A real repository with one commit on `main`."""
    import subprocess

    (path / "packages/a").mkdir(parents=True)
    (path / "packages/a/x.py").write_text("one\n", encoding="utf-8")
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "T"),
        ("add", "."),
        ("commit", "-m", "first"),
    ):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True)
    return path


def _two_repo_stamping_workspace(tmp_path: Path):
    """`_stamping_workspace`, declaring two real code repositories."""
    layout = _stamping_workspace(tmp_path)
    code, ui = _git_repo(tmp_path / "code"), _git_repo(tmp_path / "ui")
    text = layout.manifest_path.read_text(encoding="utf-8")
    seeded = 'repositories:\n  "repo":\n    path: ".."\n'
    assert seeded in text
    declared = f"repositories:\n  code:\n    path: {json.dumps(str(code))}\n  ui:\n    path: {json.dumps(str(ui))}\n"
    layout.manifest_path.write_text(text.replace(seeded, declared), encoding="utf-8")
    return layout, code, ui


def _tag(layout, path: str, repo: str) -> None:
    document = load(layout.bundle_dir / f"{path}.md")
    document.set("repo", repo)
    document.save()


def _kinds(result) -> dict[str, str]:
    return {blocked.path: blocked.kind for blocked in result.blocked}


def test_a_tagged_epic_plans_without_a_repo_name(tmp_path: Path) -> None:
    layout, _code, ui = _two_repo_stamping_workspace(tmp_path)
    _tag(layout, "work/epic-a", "ui")

    result = orchestrate.run_orchestrate(layout, "work/epic-a")

    assert result.code_repo is not None and Path(result.code_repo).resolve() == ui.resolve()
    assert (result.code_repo_name, result.code_repo_source) == ("ui", "frontmatter")
    assert _kinds(result).get("work/epic-a/children/feature-a") != "cross-repo-child"


def test_an_untagged_child_inherits_the_root_s_flag_repository(tmp_path: Path) -> None:
    layout, _code, _ui = _two_repo_stamping_workspace(tmp_path)

    result = orchestrate.run_orchestrate(layout, "work/epic-a", repo_name="ui")

    assert (result.code_repo_name, result.code_repo_source) == ("ui", "flag")
    assert "cross-repo-child" not in _kinds(result).values()


def test_a_cross_repo_child_requires_an_owner_anchor(tmp_path: Path) -> None:
    layout, _code, _ui = _two_repo_stamping_workspace(tmp_path)
    _tag(layout, "work/epic-a", "ui")
    _tag(layout, "work/epic-a/children/feature-a", "code")

    child = load(layout.bundle_dir / "work/epic-a/children/feature-a.md")
    child.set("phase", "execute")
    child.save()
    result = orchestrate.run_orchestrate(layout, "work/epic-a")

    (preparation,) = result.plan.preparations
    assert preparation.owner_path == "work/epic-a"
    assert preparation.repo.name == "code"
    assert preparation.worktree.action == "create-top-level"
    assert preparation.worktree.parent_path is None
    (blocked,) = [b for b in result.blocked if b.path == "work/epic-a/children/feature-a"]
    assert blocked.kind == "worktree-pending"
    assert all(d.slug != blocked.path for d in result.dispatches)


def test_dirty_foreign_checkout_does_not_withhold_clean_repository_dispatch(tmp_path: Path, monkeypatch) -> None:
    layout, code, ui = _two_repo_stamping_workspace(tmp_path)
    _tag(layout, "work/epic-a", "code")
    _tag(layout, "work/epic-a/children/feature-a", "ui")
    (ui / "dirty.txt").write_text("local edit\n", encoding="utf-8")
    sibling = "work/epic-a/children/feature-clean"
    _write(layout, sibling, phase="plan", affects=("packages/b",))
    from graph_works_core.work import commands as work

    assert work.run_regen_indexes(layout, dry_run=False).application.ok
    observed = {}
    real_plan = orchestrate.plan

    def capture(*args, **kwargs):
        observed.update(kwargs["repo_contexts"])
        return real_plan(*args, **kwargs)

    monkeypatch.setattr(orchestrate, "plan", capture)
    result = orchestrate.run_orchestrate(layout, "work/epic-a")
    dispatch = next(d for d in result.dispatches if d.slug == "work/epic-a/children/feature-a")
    assert dispatch.worktree.action == "create-top-level"
    assert dispatch.worktree.parent_path is None
    assert result.dispatch_repos[dispatch.key].path == ui.resolve()
    assert not result.preparations
    clean = next(d for d in result.dispatches if d.slug == sibling)
    assert clean.worktree.path == str(code.resolve())
    assert result.dispatch_repos[clean.key].path == code.resolve()
    assert len(observed) == 2
    by_path = {context.path: context for context in observed.values()}
    assert by_path[str(code.resolve())].checkout_usable
    assert not by_path[str(ui.resolve())].checkout_usable
    assert all(
        context.inventory_known and context.inventory["main"] == (context.path,) for context in observed.values()
    )


def test_linked_declared_checkouts_keep_separate_eligibility(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    layout, code, ui = _two_repo_stamping_workspace(tmp_path)
    linked = tmp_path / "code-linked"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature/linked", str(linked)],
        cwd=code,
        check=True,
        capture_output=True,
        text=True,
    )
    manifest = layout.manifest_path.read_text(encoding="utf-8")
    layout.manifest_path.write_text(manifest.replace(str(ui), str(linked)), encoding="utf-8")
    _tag(layout, "work/epic-a", "code")
    child = "work/epic-a/children/feature-a"
    _tag(layout, child, "ui")
    (linked / "dirty.txt").write_text("local\n", encoding="utf-8")
    captured = {}
    real_plan = orchestrate.plan

    def capture(*args, **kwargs):
        captured.update(kwargs)
        return real_plan(*args, **kwargs)

    monkeypatch.setattr(orchestrate, "plan", capture)
    result = orchestrate.run_orchestrate(layout, "work/epic-a")
    (dispatch,) = result.dispatches
    assert dispatch.worktree.action == "create-top-level"
    assert result.dispatch_repos[dispatch.key].path == linked.resolve()
    selected = captured["item_repos"]
    assert selected["work/epic-a"].path == code.resolve()
    assert selected[child].path == linked.resolve()
    (context,) = captured["repo_contexts"].values()
    assert context.checkout_usable_by_path[str(code.resolve())] is True
    assert context.checkout_usable_by_path[str(linked.resolve())] is False


def test_non_git_declared_repository_refuses_fresh_root(tmp_path: Path, monkeypatch) -> None:
    layout = _workspace(tmp_path / "workspace")
    path = "work/feature-a"
    _write(layout, path, phase="design")
    code = tmp_path / "plain-directory"
    code.mkdir()
    monkeypatch.setattr(orchestrate, "resolve_item_repo", lambda *args, **kwargs: ItemRepo("code", code, "sole"))
    result = orchestrate.run_orchestrate(layout, path)
    assert result.dispatches == ()
    assert [(b.path, b.kind) for b in result.blocked] == [(path, "worktree-unprovable")]


def test_a_descendant_with_a_malformed_repo_surfaces_a_warning_and_is_not_blocked(tmp_path: Path) -> None:
    layout, _code, _ui = _two_repo_stamping_workspace(tmp_path)
    _tag(layout, "work/epic-a", "ui")
    document = load(layout.bundle_dir / "work/epic-a/children/feature-a.md")
    document.set("repo", 3)
    document.save()

    result = orchestrate.run_orchestrate(layout, "work/epic-a")

    kinds = _kinds(result)
    assert kinds.get("work/epic-a/children/feature-a") not in ("invalid", "cross-repo-child")
    assert any("work/epic-a/children/feature-a" in warning for warning in result.warnings)


def test_a_descendant_naming_an_undeclared_repo_blocks_only_itself(tmp_path: Path) -> None:
    from graph_works_core.work import commands as work

    layout, _code, _ui = _two_repo_stamping_workspace(tmp_path)
    _tag(layout, "work/epic-a", "ui")
    _write(layout, "work/epic-a/children/feature-b", phase="plan", affects=("packages/b",))
    _tag(layout, "work/epic-a/children/feature-b", "nope")
    assert work.run_regen_indexes(layout, dry_run=False).application.ok

    result = orchestrate.run_orchestrate(layout, "work/epic-a")

    kinds = _kinds(result)
    assert kinds["work/epic-a/children/feature-b"] == "invalid"
    (invalid,) = [b for b in result.blocked if b.path == "work/epic-a/children/feature-b"]
    assert "work/epic-a/children/feature-b" in invalid.reason
    assert "'nope'" in invalid.reason
    sibling_kind = kinds.get("work/epic-a/children/feature-a")
    sibling_dispatched = any(d.slug == "work/epic-a/children/feature-a" for d in result.dispatches)
    assert sibling_dispatched or (sibling_kind is not None and sibling_kind not in ("invalid", "cross-repo-child"))


def test_an_untagged_root_still_refuses_in_a_two_repo_workspace(tmp_path: Path) -> None:
    layout, _code, _ui = _two_repo_stamping_workspace(tmp_path)
    with pytest.raises(WorkspaceError, match="repo_name"):
        orchestrate.run_orchestrate(layout, "work/epic-a")


def test_a_two_repo_advance_infers_from_the_declared_repo_holding_the_cwd(tmp_path: Path) -> None:
    """Several declared repos and no name: the one whose repository the cwd
    belongs to -- here through a linked worktree of it -- is the repo."""
    import subprocess

    layout, _code, ui = _two_repo_stamping_workspace(tmp_path)
    linked = tmp_path / "ui-linked"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature/ui", str(linked)], cwd=ui, check=True, capture_output=True, text=True
    )

    result = stage.run_stage_advance(layout, "work/feature-solo", today=TODAY, cwd=linked, dry_run=False)

    assert result.outcome.plan.refusal is None
    assert result.repo_note is None
    changes = _stamped(result)
    assert Path(str(changes["worktree"])).resolve() == linked.resolve()
    assert changes["branch"] == "feature/ui"
    assert result.application is not None and result.application.ok, result.application.failures


def test_a_two_repo_advance_from_a_declared_main_checkout_resolves_that_repo(tmp_path: Path, monkeypatch) -> None:
    layout, code, _ui = _two_repo_stamping_workspace(tmp_path)
    calls = _captured_repo_roots(monkeypatch)

    result = stage.run_stage_advance(layout, "work/feature-solo", today=TODAY, cwd=code / "packages", dry_run=False)

    assert result.outcome.plan.refusal is None
    assert result.repo_note is None
    assert "worktree" not in _stamped(result)  # a main checkout is not a linked worktree
    assert result.outcome.written
    # The repo actually used for postcondition validation is the declared
    # repo cwd is in -- `code`, not `ui` -- not merely "a repo, any repo".
    assert calls and calls[-1] is not None and calls[-1].resolve() == code.resolve()


def test_a_two_repo_advance_outside_every_declared_repo_skips_inference(tmp_path: Path) -> None:
    """No declared repo holds the cwd: inference is skipped, never refused."""
    layout, _code, _ui = _two_repo_stamping_workspace(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = stage.run_stage_advance(layout, "work/feature-solo", today=TODAY, cwd=elsewhere, dry_run=False)

    assert result.outcome.plan.refusal is None
    changes = _stamped(result)
    assert "worktree" not in changes and "branch" not in changes
    assert result.repo_note is not None and "2 repositories declared" in result.repo_note
    assert result.outcome.written


def test_a_two_repo_advance_with_a_repo_name_uses_that_repo(tmp_path: Path, monkeypatch) -> None:
    layout, _code, ui = _two_repo_stamping_workspace(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    calls = _captured_repo_roots(monkeypatch)

    result = stage.run_stage_advance(
        layout, "work/feature-solo", today=TODAY, cwd=elsewhere, repo_name="ui", dry_run=False
    )

    assert result.outcome.plan.refusal is None
    assert result.repo_note is None
    assert result.outcome.written
    # `repo_name="ui"` must select `ui`, not merely "some repo" -- the named
    # repo is what postcondition validation actually receives.
    assert calls and calls[-1] is not None and calls[-1].resolve() == ui.resolve()


def test_a_tagged_advance_from_another_repo_skips_inference_with_a_warning(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    layout, code, ui = _two_repo_stamping_workspace(tmp_path)
    _tag(layout, "work/feature-solo", "ui")
    linked = tmp_path / "code-linked"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature/code", str(linked)],
        cwd=code,
        check=True,
        capture_output=True,
        text=True,
    )
    calls = _captured_repo_roots(monkeypatch)

    result = stage.run_stage_advance(layout, "work/feature-solo", today=TODAY, cwd=linked, dry_run=False)

    assert result.outcome.plan.refusal is None
    changes = _stamped(result)
    assert "worktree" not in changes and "branch" not in changes
    assert any("worktree inference skipped" in warning for warning in result.warnings)
    assert calls and calls[-1] is not None and calls[-1].resolve() == ui.resolve()


def test_a_tagged_advance_from_its_own_repo_still_infers(tmp_path: Path) -> None:
    import subprocess

    layout, _code, ui = _two_repo_stamping_workspace(tmp_path)
    _tag(layout, "work/feature-solo", "ui")
    linked = tmp_path / "ui-linked"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature/ui", str(linked)], cwd=ui, check=True, capture_output=True, text=True
    )

    result = stage.run_stage_advance(layout, "work/feature-solo", today=TODAY, cwd=linked, dry_run=False)

    assert Path(str(_stamped(result)["worktree"])).resolve() == linked.resolve()
    assert not any("worktree inference skipped" in warning for warning in result.warnings)


def test_a_tagged_advance_refuses_a_conflicting_repo_name(tmp_path: Path) -> None:
    layout, _code, _ui = _two_repo_stamping_workspace(tmp_path)
    _tag(layout, "work/feature-solo", "ui")
    with pytest.raises(WorkspaceError, match="conflicts"):
        stage.run_stage_advance(layout, "work/feature-solo", today=TODAY, repo_name="code", dry_run=False)


def test_a_descendant_read_only_advance_does_not_stamp_from_cwd(tmp_path: Path, monkeypatch) -> None:
    """The reproduction: a `plan` stage writes only into the vault, so the
    directory it happened to run in must not become the item's placement."""
    layout = _stamping_workspace(tmp_path)
    _detects(monkeypatch)

    result = stage.run_stage_advance(
        layout, "work/epic-a/children/feature-a", today=TODAY, repo=tmp_path / "repo", dry_run=False
    )

    assert result.outcome.plan.refusal is None
    changes = _stamped(result)
    assert "worktree" not in changes
    assert "branch" not in changes
    written = load(layout.bundle_dir / "work/epic-a/children/feature-a.md").fm_data()
    assert written.get("worktree") is None
    assert written.get("branch") is None


def test_a_subtree_root_read_only_advance_still_stamps_from_cwd(tmp_path: Path, monkeypatch) -> None:
    """The root carve-out: its stamp is the epic anchor every descendant
    resolves against, and nothing else ever mints it."""
    layout = _stamping_workspace(tmp_path)
    _detects(monkeypatch)

    result = stage.run_stage_advance(layout, "work/feature-solo", today=TODAY, repo=tmp_path / "repo", dry_run=False)

    assert result.outcome.plan.refusal is None
    changes = _stamped(result)
    assert changes["worktree"] == "/wt/detected"
    assert changes["branch"] == "detected/branch"


@pytest.mark.parametrize("phase", ["execute", "finish"])
def test_a_descendant_code_phase_advance_never_stamps_from_cwd(tmp_path: Path, monkeypatch, phase: str) -> None:
    """D-006: the coordinator records a descendant's observed placement; a
    worker's cwd never does, at any phase."""
    layout = _stamping_workspace(tmp_path)
    _write(layout, "work/epic-a/children/feature-a", phase=phase)
    _detects(monkeypatch)

    result = stage.run_stage_advance(
        layout,
        "work/epic-a/children/feature-a",
        today=TODAY,
        repo=tmp_path / "repo",
        owner="pat",
        resolved_in="pr-1" if phase == "finish" else None,
        dry_run=False,
    )

    assert result.outcome.plan.refusal is None, result.outcome.plan.detail
    assert "worktree" not in _stamped(result) and "branch" not in _stamped(result)
    written = load(layout.bundle_dir / "work/epic-a/children/feature-a.md").fm_data()
    assert written.get("worktree") is None


def test_an_explicit_pair_overrides_the_read_only_suppression(tmp_path: Path, monkeypatch) -> None:
    """A coordinator or a human stating a placement is never a guess."""
    layout = _stamping_workspace(tmp_path)
    _detects(monkeypatch)

    result = stage.run_stage_advance(
        layout,
        "work/epic-a/children/feature-a",
        today=TODAY,
        repo=tmp_path / "repo",
        worktree="/wt/explicit",
        branch="explicit/branch",
        dry_run=False,
    )

    changes = _stamped(result)
    assert changes["worktree"] == "/wt/explicit"
    assert changes["branch"] == "explicit/branch"


def test_only_a_top_level_item_may_infer_from_cwd(tmp_path: Path) -> None:
    """Inference is the attended top-level fallback. A never-entered TestGap
    routed straight to `execute` is a descendant like any other."""
    from okf_io import load_bundle
    from work_tracker_okf.items import IGNORE, load_items

    layout = _stamping_workspace(tmp_path)
    _write(layout, "work/epic-a/children/bug-x", type="Bug", phase=None)
    _write(layout, "work/epic-a/children/gap-x", type="TestGap", phase=None)
    by_path = {item.path: item for item in load_items(load_bundle(layout.bundle_dir, ignore=IGNORE))}

    assert stage._infers_from_cwd(by_path["work/epic-a"])
    assert stage._infers_from_cwd(by_path["work/feature-solo"])
    for path in ("work/epic-a/children/feature-a", "work/epic-a/children/bug-x", "work/epic-a/children/gap-x"):
        assert not stage._infers_from_cwd(by_path[path]), path


def test_a_direct_entry_test_gap_descendant_does_not_stamp_from_cwd(tmp_path: Path, monkeypatch) -> None:
    layout = _stamping_workspace(tmp_path)
    _write(layout, "work/epic-a/children/gap-x", type="TestGap", phase=None)
    _detects(monkeypatch)

    result = stage.run_stage_advance(
        layout,
        "work/epic-a/children/gap-x",
        today=TODAY,
        repo=tmp_path / "repo",
        effort="small",
        owner="pat",
        dry_run=False,
    )

    assert result.outcome.plan.refusal is None, result.outcome.plan.detail
    assert "worktree" not in _stamped(result)


def test_a_supervised_root_advance_with_the_opt_out_infers_nothing(tmp_path: Path, monkeypatch) -> None:
    layout = _stamping_workspace(tmp_path)
    monkeypatch.setattr(stage.provenance, "worktree_state", lambda cwd, repo: pytest.fail("opt-out must not probe"))

    result = stage.run_stage_advance(
        layout, "work/feature-solo", today=TODAY, repo=tmp_path / "repo", infer_worktree=False, dry_run=False
    )

    assert result.outcome.plan.refusal is None
    assert "worktree" not in _stamped(result) and "branch" not in _stamped(result)


def test_the_opt_out_still_applies_a_deliberate_explicit_pair(tmp_path: Path, monkeypatch) -> None:
    layout = _stamping_workspace(tmp_path)
    _detects(monkeypatch)

    result = stage.run_stage_advance(
        layout,
        "work/feature-solo",
        today=TODAY,
        repo=tmp_path / "repo",
        worktree="/wt/explicit",
        branch="explicit/branch",
        infer_worktree=False,
        dry_run=False,
    )

    assert _stamped(result)["worktree"] == "/wt/explicit"


def test_the_read_only_and_results_phases_are_complements() -> None:
    """The two halves of `orchestrate` name this vocabulary separately, by
    design (D-001: no shared module-level symbol). This is what keeps them
    from drifting apart."""
    from work_tracker_okf.vocabulary import PHASES

    assert frozenset() == orchestrate.READ_ONLY_PHASES & stage.RESULTS_PHASES
    assert PHASES - {"done"} == orchestrate.READ_ONLY_PHASES | stage.RESULTS_PHASES


def test_an_open_decision_at_design_stops_advance_stamping_plan(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    path = "work/feature-held"
    _write(layout, path, phase="design")
    spec = layout.bundle_dir / f"{path}/references/01-design.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# Design\n", encoding="utf-8", newline="")
    ledger = spec.parent / "00-decisions.md"
    ledger.write_text(f"## D-001 — contradiction\nstatus: open\naffects: [{path}]\n", encoding="utf-8", newline="")
    result = stage.run_stage_advance(layout, path, today=TODAY, dry_run=False)
    assert result.outcome.plan.refusal == "blocked"
    assert load(layout.bundle_dir / f"{path}.md").fm_data()["phase"] == "design"


def test_foreign_anchor_adoption_stamp_and_replan_use_real_repository(tmp_path: Path) -> None:
    import subprocess

    layout, code, _ui = _two_repo_stamping_workspace(tmp_path)
    owner, child = "work/epic-a", "work/epic-a/children/feature-a"
    _tag(layout, owner, "ui")
    _tag(layout, child, "code")
    page = layout.bundle_dir / f"{child}.md"
    before = page.read_bytes()
    readonly = orchestrate.run_orchestrate(layout, owner)
    assert not readonly.preparations
    assert readonly.dispatches[0].worktree.path == str(code.resolve())
    assert page.read_bytes() == before
    assert not load(layout.bundle_dir / f"{owner}.md").fm_data().get("repo_stamps")
    document = load(page)
    document.set("phase", "execute")
    document.save()
    (prep,) = orchestrate.run_orchestrate(layout, owner).preparations
    anchor = tmp_path / "code-anchor"
    subprocess.run(
        ["git", "worktree", "add", "-b", prep.branch, str(anchor), prep.base_branch],
        cwd=code,
        check=True,
        capture_output=True,
    )
    (adoption,) = orchestrate.run_orchestrate(layout, owner).preparations
    assert adoption.worktree.action == "reuse"
    assert adoption.worktree.path == str(anchor.resolve())
    document = load(layout.bundle_dir / f"{owner}.md")
    document.set("repo_stamps", {"code": {"worktree": str(anchor), "branch": prep.branch}})
    document.save()
    replanned = orchestrate.run_orchestrate(layout, owner)
    assert not replanned.preparations
    (dispatch,) = replanned.dispatches
    assert dispatch.worktree.parent_path == str(anchor.resolve())
    assert dispatch.worktree.base_branch == dispatch.merge_target == prep.branch


def test_foreign_anchor_branch_without_worktree_is_a_repair_case(tmp_path: Path) -> None:
    import subprocess

    layout, code, _ui = _two_repo_stamping_workspace(tmp_path)
    owner, child = "work/epic-a", "work/epic-a/children/feature-a"
    _tag(layout, owner, "ui")
    _tag(layout, child, "code")
    document = load(layout.bundle_dir / f"{child}.md")
    document.set("phase", "execute")
    document.save()
    branch = orchestrate.integration_branch(owner, "Epic")
    subprocess.run(["git", "branch", branch], cwd=code, check=True, capture_output=True)
    result = orchestrate.run_orchestrate(layout, owner)
    assert not result.preparations and not result.dispatches
    assert _kinds(result)[child] == "worktree-unprovable"


@pytest.mark.parametrize("stamped", [False, True])
@pytest.mark.parametrize("state", ["dirty", "unreadable"])
def test_unsafe_undeclared_anchor_worktree_refuses_adoption_and_use(
    tmp_path: Path, stamped: bool, state: str, monkeypatch
) -> None:
    import subprocess

    layout, code, _ui = _two_repo_stamping_workspace(tmp_path)
    owner, child = "work/epic-a", "work/epic-a/children/feature-a"
    _tag(layout, owner, "ui")
    _tag(layout, child, "code")
    document = load(layout.bundle_dir / f"{child}.md")
    document.set("phase", "execute")
    document.save()
    branch = orchestrate.integration_branch(owner, "Epic")
    anchor = tmp_path / "undeclared-anchor"
    subprocess.run(["git", "worktree", "add", "-b", branch, str(anchor)], cwd=code, check=True, capture_output=True)
    if state == "dirty":
        (anchor / "uncommitted.txt").write_text("local work\n", encoding="utf-8")
    else:
        from graph_works_core.workspace import repo_context
        from graph_works_core.workspace.provenance import GitOutcome

        real_probe = repo_context.probe_git

        def unreadable_status(cwd, *args):
            if cwd == anchor.resolve() and args == ("status", "--porcelain"):
                return GitOutcome(None, "", "error")
            return real_probe(cwd, *args)

        monkeypatch.setattr(repo_context, "probe_git", unreadable_status)
    if stamped:
        document = load(layout.bundle_dir / f"{owner}.md")
        document.set("repo_stamps", {"code": {"worktree": str(anchor), "branch": branch}})
        document.save()
    result = orchestrate.run_orchestrate(layout, owner)
    assert not result.preparations and not result.dispatches
    assert _kinds(result)[child] == "worktree-unprovable"


@pytest.mark.parametrize("tagged", [False, True])
def test_explicit_repo_override_projects_dispatch_metadata(tmp_path: Path, tagged: bool) -> None:
    """The singular Python override serializes without resolving foreign assignments."""
    from graph_works_wire.work import orchestrate_payload

    layout = _workspace(tmp_path / "workspace")
    repo = _git_repo(tmp_path / "code")
    path = "work/feature-explicit"
    _write(layout, path, phase="design")
    if tagged:
        document = load(layout.bundle_dir / f"{path}.md")
        document.set("repo", "undeclared-foreign")
        document.save()

    result = orchestrate.run_orchestrate(layout, path, repo=repo)
    [dispatch] = result.dispatches
    payload = orchestrate_payload(result)
    assert result.dispatch_repos[dispatch.key] == ItemRepo(None, repo, "flag")
    assert payload["dispatches"][0]["repo"] == {"name": None, "path": str(repo), "source": "flag"}
    assert payload["dispatches"][0]["worktree"]["action"] == "create-top-level"
    assert payload["dispatches"][0]["worktree"]["base_branch"] == "main"
    assert payload["preparations"] == []
