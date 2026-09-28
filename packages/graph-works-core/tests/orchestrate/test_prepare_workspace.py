"""`gw work prepare-workspace` (feature-workspace-branch-placement §4.2)."""

import subprocess
from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate.commands import branch_name
from graph_works_core.orchestrate.workspace_prepare import run_prepare_workspace
from graph_works_core.workspace.layout import WorkspaceLayout

TODAY = date(2026, 9, 26)
EPIC = "work/epic-a"
CHILD = f"{EPIC}/children/feature-a"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def ws(tmp_path: Path) -> WorkspaceLayout:
    code = tmp_path / "code"
    code.mkdir()
    git(code, "init", "-b", "main")
    layout = apply_init(plan_init(tmp_path / "workspace", today=TODAY, topic="Prepare")).layout
    layout.manifest_path.write_text(
        layout.manifest_path.read_text(encoding="utf-8").replace(
            "repositories: {}", f"repositories:\n  code:\n    path: {code.as_posix()}"
        ),
        encoding="utf-8",
        newline="",
    )
    for path, kind, status in ((EPIC, "Epic", "in-progress"), (CHILD, "Feature", "accepted")):
        page = layout.bundle_dir / f"{path}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(
            f"---\ntype: {kind}\ntitle: Prepare\ndescription: d\nstatus: stable\n"
            f"work_status: {status}\nphase: execute\neffort: medium\nowner: pat\n"
            "opened: 2026-09-26\nupdated: 2026-09-26\naffects: []\n---\n\n## Summary\nd\n",
            encoding="utf-8",
            newline="",
        )
    git(layout.root, "init", "-b", "main")
    git(layout.root, "config", "user.name", "Test")
    git(layout.root, "config", "user.email", "test@example.com")
    git(layout.root, "add", ".")
    git(layout.root, "commit", "-m", "Initial workspace")
    return layout


@pytest.fixture
def ws_design(ws):
    page = ws.bundle_dir / f"{CHILD}.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace("phase: execute", "phase: design"), encoding="utf-8", newline=""
    )
    return ws


@pytest.fixture
def embedded_ws(tmp_path):
    return apply_init(plan_init(tmp_path / "embedded", today=TODAY, topic="Disabled")).layout


def test_plans_outer_first_without_touching_git(ws):
    result = run_prepare_workspace(ws, CHILD, today=TODAY)
    assert result.refusal is None and not result.applied
    assert [(s.owner_path, s.action) for s in result.steps] == [(EPIC, "create"), (CHILD, "create")]
    assert result.steps[1].base_branch == branch_name(EPIC, "Epic")
    assert branch_name(EPIC, "Epic") not in git(ws.root, "branch", "--list")


def test_apply_creates_outer_first_and_stamps(ws):
    result = run_prepare_workspace(ws, CHILD, today=TODAY, apply=True)
    assert result.refusal is None and result.applied
    assert all(s.recorded for s in result.steps)
    child_wt = result.steps[1].worktree
    assert git(child_wt, "branch", "--show-current") == branch_name(CHILD, "Feature")
    assert git(ws.root, "merge-base", "--is-ancestor", branch_name(EPIC, "Epic"), branch_name(CHILD, "Feature")) == ""
    page = (ws.bundle_dir / f"{CHILD}.md").read_text(encoding="utf-8")
    assert "_workspace:" in page and child_wt in page
    assert git(ws.root, "status", "--porcelain") == ""  # the stamp was committed (commit child)


def test_replay_is_idempotent(ws):
    run_prepare_workspace(ws, CHILD, today=TODAY, apply=True)
    again = run_prepare_workspace(ws, CHILD, today=TODAY, apply=True)
    assert again.refusal is None and not again.applied
    assert [s.action for s in again.steps] == ["verified", "verified"]


def test_crash_after_worktree_add_is_adopted(ws):
    plan = run_prepare_workspace(ws, EPIC, today=TODAY)
    step = plan.steps[0]
    git(ws.root, "worktree", "add", "-b", step.branch, step.worktree, "main")  # the crash: no stamp
    result = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert result.refusal is None and result.steps[0].action == "adopt" and result.steps[0].recorded


def test_branch_without_checkout_refuses(ws):
    git(ws.root, "branch", branch_name(EPIC, "Epic"))
    result = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert result.refusal == "workspace-unprovable" and not result.applied


def test_directory_under_another_branch_refuses(ws):
    step = run_prepare_workspace(ws, EPIC, today=TODAY).steps[0]
    git(ws.root, "worktree", "add", "-b", "someone/else", step.worktree, "main")
    assert run_prepare_workspace(ws, EPIC, today=TODAY, apply=True).refusal == "workspace-ambiguous"


def test_design_phase_is_not_entitled(ws_design):  # CHILD at design
    assert run_prepare_workspace(ws_design, CHILD, today=TODAY).refusal == "not-entitled"


def test_disabled_workspace_is_a_note_not_a_refusal(embedded_ws):
    result = run_prepare_workspace(embedded_ws, CHILD, today=TODAY, apply=True)
    assert result.refusal is None and result.note and result.steps == ()


def test_unknown_item_refuses(ws):
    result = run_prepare_workspace(ws, "work/missing", today=TODAY, apply=True)
    assert result.refusal == "unknown-path" and not result.applied and result.placement is None


@pytest.mark.parametrize("raw", ["null", "[]", "{_workspace: null}", "{_workspace: {branch: bad}}"])
def test_malformed_stamps_refuse_without_creating(ws, raw):
    page = ws.bundle_dir / f"{EPIC}.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace("phase: execute", f"phase: execute\nrepo_stamps: {raw}"),
        encoding="utf-8",
        newline="",
    )
    result = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert result.refusal == "workspace-unprovable" and not result.applied
    assert branch_name(EPIC, "Epic") not in git(ws.root, "branch", "--list")


@pytest.mark.parametrize("field", ["inventory_known", "branches_known", "identity_known"])
def test_unknown_repository_evidence_refuses(ws, monkeypatch, field):
    from dataclasses import replace

    from graph_works_core.orchestrate import workspace_prepare as prepare

    observe = prepare.observe_repository
    monkeypatch.setattr(prepare, "observe_repository", lambda *a, **kw: replace(observe(*a, **kw), **{field: False}))
    result = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert result.refusal == "workspace-unprovable" and not result.applied


@pytest.mark.parametrize("stamped", [False, True])
def test_dirty_checkout_refuses(ws, stamped):
    step = run_prepare_workspace(ws, EPIC, today=TODAY).steps[0]
    if stamped:
        assert run_prepare_workspace(ws, EPIC, today=TODAY, apply=True).refusal is None
    else:
        git(ws.root, "worktree", "add", "-b", step.branch, step.worktree, "main")
    (Path(step.worktree) / "dirty").write_text("uncommitted", encoding="utf-8", newline="")
    result = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert result.refusal == "workspace-unprovable" and not result.applied


@pytest.mark.parametrize("prepared", [False, True])
def test_later_refusal_reports_only_actual_effects(ws, prepared):
    if prepared:
        assert run_prepare_workspace(ws, EPIC, today=TODAY, apply=True).refusal is None
    git(ws.root, "branch", branch_name(CHILD, "Feature"))
    result = run_prepare_workspace(ws, CHILD, today=TODAY, apply=True)
    assert result.refusal == "workspace-unprovable"
    assert result.applied is (not prepared)
    assert result.placement is None


@pytest.mark.parametrize("adopt", [False, True])
def test_guard_refusal_retains_creation_effect_only(ws, monkeypatch, adopt):
    from graph_works_core.orchestrate import workspace_prepare as prepare
    from graph_works_core.workspace.errors import WorkspaceError

    step = run_prepare_workspace(ws, EPIC, today=TODAY).steps[0]
    if adopt:
        git(ws.root, "worktree", "add", "-b", step.branch, step.worktree, "main")
    record = prepare.run_record_placement

    def refuse(*a, **kw):
        if kw.get("dry_run") is False:
            raise WorkspaceError("preparation changed")
        return record(*a, **kw)

    monkeypatch.setattr(prepare, "run_record_placement", refuse)
    result = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert result.refusal == "stamp-refused" and result.applied is (not adopt)
    assert "preparation changed" in result.detail


def test_commit_failure_is_not_committed_success_or_successful_replay(ws):
    hook = ws.root / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\necho rejected >&2\nexit 1\n", encoding="utf-8", newline="")
    hook.chmod(0o755)
    result = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert result.refusal == "stamp-refused" and result.applied
    assert "rejected" in result.detail and result.placement is None
    again = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert again.refusal == "stamp-refused" and not again.applied


def test_failed_git_add_with_no_effect_is_unchanged(ws, monkeypatch):
    from graph_works_core.orchestrate import workspace_prepare as prepare
    from graph_works_core.workspace.provenance import GitOutcome

    monkeypatch.setattr(prepare, "probe_git", lambda *a: GitOutcome(1, "", "error", "rejected"))
    result = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert result.refusal == "workspace-unprovable" and not result.applied
    assert not (ws.worktrees_dir / "workspace").exists()


def test_registered_path_replaced_by_other_clean_repository_refuses(ws):
    import shutil

    step = run_prepare_workspace(ws, EPIC, today=TODAY).steps[0]
    git(ws.root, "worktree", "add", "-b", step.branch, step.worktree, "main")
    shutil.rmtree(step.worktree)
    Path(step.worktree).mkdir()
    git(step.worktree, "init", "-b", step.branch)
    result = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert result.refusal == "workspace-unprovable" and not result.applied


def test_failed_git_add_preserves_partial_effect_evidence(ws, monkeypatch):
    from graph_works_core.orchestrate import workspace_prepare as prepare
    from graph_works_core.workspace.provenance import GitOutcome

    probe = prepare.probe_git

    def fail_after_effect(repo, *args):
        if args[:2] == ("worktree", "add"):
            git(repo, "branch", args[3])
            return GitOutcome(1, "", "error", "partial add")
        return probe(repo, *args)

    monkeypatch.setattr(prepare, "probe_git", fail_after_effect)
    result = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert result.refusal == "workspace-unprovable" and result.applied


def test_real_preparation_guard_detects_edit_during_creation(ws, monkeypatch):
    from graph_works_core.orchestrate import workspace_prepare as prepare

    probe = prepare.probe_git
    page = ws.bundle_dir / f"{EPIC}.md"

    def edit_after_create(repo, *args):
        outcome = probe(repo, *args)
        if args[:2] == ("worktree", "add") and outcome.returncode == 0:
            page.write_text(
                page.read_text(encoding="utf-8").replace("phase: execute", "phase: finish"),
                encoding="utf-8",
                newline="",
            )
        return outcome

    monkeypatch.setattr(prepare, "probe_git", edit_after_create)
    result = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert result.refusal == "stamp-refused" and result.applied
    assert "preparation changed" in result.detail
    assert "_workspace:" not in page.read_text(encoding="utf-8")


def test_dirty_created_checkout_is_not_stamped(ws, monkeypatch):
    from graph_works_core.orchestrate import workspace_prepare as prepare

    probe = prepare.probe_git

    def dirty_after_create(repo, *args):
        outcome = probe(repo, *args)
        if args[:2] == ("worktree", "add") and outcome.returncode == 0:
            (Path(args[4]) / "dirty").write_text("changed", encoding="utf-8", newline="")
        return outcome

    monkeypatch.setattr(prepare, "probe_git", dirty_after_create)
    result = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert result.refusal == "workspace-unprovable" and result.applied
    assert "_workspace:" not in (ws.bundle_dir / f"{EPIC}.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("stamped", [False, True])
def test_duplicate_branch_checkouts_refuse(ws, monkeypatch, stamped):
    from dataclasses import replace

    from graph_works_core.orchestrate import workspace_prepare as prepare

    if stamped:
        assert run_prepare_workspace(ws, EPIC, today=TODAY, apply=True).refusal is None
    observe = prepare.observe_repository
    branch = branch_name(EPIC, "Epic")

    def duplicate(*a, **kw):
        context = observe(*a, **kw)
        return replace(context, inventory={**context.inventory, branch: ("/one", "/two")})

    monkeypatch.setattr(prepare, "observe_repository", duplicate)
    result = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert result.refusal == "workspace-ambiguous" and not result.applied


def test_branch_at_other_path_refuses(ws, tmp_path):
    git(ws.root, "worktree", "add", "-b", branch_name(EPIC, "Epic"), str(tmp_path / "other"), "main")
    result = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert result.refusal == "workspace-ambiguous" and not result.applied


def test_unknown_path_availability_refuses(ws, monkeypatch):
    from dataclasses import replace

    from graph_works_core.orchestrate import workspace_prepare as prepare

    observe = prepare.observe_repository
    monkeypatch.setattr(prepare, "observe_repository", lambda *a, **kw: replace(observe(*a, **kw), path_exists={}))
    result = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert result.refusal == "workspace-unprovable" and not result.applied


def test_terminal_item_does_not_create_checkout(ws):
    page = ws.bundle_dir / f"{EPIC}.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace("work_status: in-progress", "work_status: resolved"),
        encoding="utf-8",
        newline="",
    )
    result = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert result.refusal == "stamp-refused" and not result.applied
    assert branch_name(EPIC, "Epic") not in git(ws.root, "branch", "--list")


def test_stamp_application_failure_retains_created_checkout(ws, monkeypatch):
    from dataclasses import replace

    from graph_works_core.orchestrate import workspace_prepare as prepare

    record = prepare.run_record_placement

    def refused(*a, **kw):
        if kw.get("dry_run") is False:
            preview = record(*a, **{**kw, "dry_run": True})
            return replace(preview, plan=replace(preview.plan, refusal="phase-mismatch", detail="changed phase"))
        return record(*a, **kw)

    monkeypatch.setattr(prepare, "run_record_placement", refused)
    result = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert result.refusal == "stamp-refused" and result.applied
    assert "changed phase" in result.detail


def test_dangling_destination_symlink_refuses(ws):
    step = run_prepare_workspace(ws, EPIC, today=TODAY).steps[0]
    target = Path(step.worktree)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(target.parent / "missing", target_is_directory=True)
    result = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert result.refusal == "workspace-ambiguous" and not result.applied
    assert target.is_symlink()


def test_workspace_commit_off_is_no_effect_note(ws):
    overlay = ws.manifest_path.with_name("workspace.local.yaml")
    overlay.write_text('workflow:\n  workspace_commits: "off"\n', encoding="utf-8", newline="")
    result = run_prepare_workspace(ws, CHILD, today=TODAY, apply=True)
    assert result.note and result.refusal is None and not result.applied and not result.steps


def test_verified_plan_is_unchanged_and_exposes_placement(ws):
    assert run_prepare_workspace(ws, CHILD, today=TODAY, apply=True).refusal is None
    result = run_prepare_workspace(ws, CHILD, today=TODAY)
    assert result.refusal is None and not result.applied
    assert result.placement is not None and result.placement.owner_path == CHILD
    assert [s.action for s in result.steps] == ["verified", "verified"]


def test_committed_crlf_stamp_replays_without_false_pending_commit(ws):
    (ws.root / ".gitattributes").write_text("*.md -text\n", encoding="utf-8", newline="")
    page = ws.bundle_dir / f"{EPIC}.md"
    page.write_bytes(page.read_bytes().replace(b"\n", b"\r\n"))
    git(ws.root, "add", ".")
    git(ws.root, "commit", "-m", "Use CRLF")
    assert run_prepare_workspace(ws, EPIC, today=TODAY, apply=True).refusal is None
    result = run_prepare_workspace(ws, EPIC, today=TODAY, apply=True)
    assert result.refusal is None and not result.applied


def test_record_workspace_placement_rejects_disabled_workspace(embedded_ws):
    from graph_works_core.orchestrate.placement import run_record_placement
    from graph_works_core.workspace.errors import WorkspaceError

    # A known item is required to enter repository entitlement checking.
    page = embedded_ws.bundle_dir / f"{EPIC}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Epic\ntitle: e\ndescription: d\nstatus: stable\nwork_status: in-progress\nphase: execute\n---\n",
        encoding="utf-8",
        newline="",
    )
    with pytest.raises(WorkspaceError, match="workspace placement disabled"):
        run_record_placement(
            embedded_ws,
            EPIC,
            root=EPIC,
            phase="execute",
            worktree=str(embedded_ws.root / "wt"),
            branch="epic/a",
            today=TODAY,
            repo="_workspace",
        )
