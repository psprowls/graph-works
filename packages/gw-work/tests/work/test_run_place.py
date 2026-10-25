"""Placement resolves one acting repository and sweeps owner repositories safely."""

from datetime import date

import pytest
from gitrepo import ENV, git
from gw_kernel.workspace import provenance
from gw_kernel.workspace.errors import WorkspaceError
from gw_pipeline.placement import NullNotifier
from gw_work.place import run_place
from workspace_fixture import make_workspace

TODAY = date(2026, 10, 22)
EPIC = "work/epic-family"
LEAF = f"{EPIC}/children/feature-leaf"
OTHER = f"{EPIC}/children/feature-other"


def _workspace(tmp_path, repos=("code",)):
    layout = make_workspace(tmp_path)
    entries = ""
    for name in repos:
        repo = tmp_path / name
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "t@example.com")
        git(repo, "config", "user.name", "t")
        git(repo, "commit", "-q", "--allow-empty", "-m", "Initial code")
        entries += f"  {name}:\n    path: {repo.as_posix()}\n"
    head = layout.manifest_path.read_text(encoding="utf-8").split("repositories:")[0]
    layout.manifest_path.write_text(head + "repositories:\n" + entries, encoding="utf-8", newline="\n")
    git(layout.root, "add", "-A")
    git(layout.root, "commit", "-q", "-m", "Declare repositories")
    return layout


def _item(layout, path=LEAF, *, repo="code", affects=("src",), phase="execute", resolved=False):
    kind = "Epic" if path.rsplit("/", 1)[-1].startswith("epic-") else "Feature"
    repo_line = f"repo: {repo}\n" if repo else ""
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: {kind}\ntitle: Placement\ndescription: Placement test\nstatus: stable\n"
        f"work_status: {'resolved' if resolved else 'open'}\nphase: {phase}\neffort: medium\n"
        "opened: 2026-10-22\nupdated: 2026-10-22\n"
        + repo_line
        + "affects: ["
        + ", ".join(affects)
        + "]\n---\n\n## Summary\nPlacement test\n",
        encoding="utf-8",
        newline="\n",
    )


def _run(layout, path=LEAF, **kwargs):
    return run_place(
        layout,
        path,
        **{
            "mode": "ensure",
            "dry_run": False,
            "repo_name": None,
            "today": TODAY,
            "notifier": NullNotifier(),
            **kwargs,
        },
    )


def _receipt(layout, placement):
    tip = git(placement.repo_root, "rev-parse", f"refs/heads/{placement.branch}")
    target = git(placement.repo_root, "rev-parse", "main")
    page = layout.bundle_dir / placement.path / "references" / "04-finish-receipt.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    existing = (
        page.read_text(encoding="utf-8")
        if page.exists()
        else (f"---\ntype: Explanation\nreceipt_version: 2\nowner: {placement.path}\nintegrations:\n---\n")
    )
    row = (
        f"  - repo: {placement.repo_name}\n    source_branch: {placement.branch}\n    source_commit: {tip}\n"
        f"    target_branch: main\n    result_commit: {target}\n    strategy: attested\n    evidence: accepted\n"
        "    accepted_by: Reviewer\n    reason: Reviewed integration\n"
    )
    page.write_text(existing.removesuffix("---\n") + row + "---\n", encoding="utf-8", newline="\n")


def test_unknown_item(tmp_path):
    result = _run(_workspace(tmp_path), "work/feature-missing")
    assert result.refusal.kind == "unknown-item"
    assert result.refusal.detail == "no work item at work/feature-missing"


@pytest.mark.parametrize("mode", ["ensure", "start", "remove", "prune"])
def test_workspace_only_item_is_a_note_with_declared_code(tmp_path, mode):
    layout = _workspace(tmp_path)
    _item(layout, repo=None, affects=("gw:workspace",))
    result = _run(layout, mode=mode)
    assert result.ok and result.placement is None
    assert any(warning.startswith("no-repo: ") for warning in result.warnings)
    assert not (layout.worktrees_dir / "code").exists()


def test_no_declared_repository_is_a_note(tmp_path):
    layout = _workspace(tmp_path, ())
    _item(layout, repo=None)
    result = _run(layout)
    assert result.ok and result.placement is None
    assert result.warnings[0].startswith("no-repo: ")


@pytest.mark.parametrize("affects", [(), ("gw:workspace", "src"), ("unknown:path",)])
def test_empty_mixed_or_unknown_affects_still_place_code(tmp_path, affects):
    layout = _workspace(tmp_path)
    _item(layout, affects=affects)
    result = _run(layout)
    assert result.ok and result.placement.repo_name == "code"
    assert result.placement.worktree.is_dir()


def test_ensure_end_to_end(tmp_path):
    layout = _workspace(tmp_path)
    _item(layout)
    result = _run(layout)
    assert result.ok and result.placement.path == LEAF
    assert result.placement.worktree == layout.worktrees_dir / "code" / result.placement.branch
    assert git(result.placement.worktree, "branch", "--show-current") == result.placement.branch
    assert result.workspace_commit.status == "committed"


def test_owner_chain_in_leaf_foreign_repo(tmp_path):
    layout = _workspace(tmp_path, ("code", "other"))
    _item(layout, EPIC)
    _item(layout, repo="other")
    result = _run(layout)
    assert result.ok
    assert [step.placement.path for step in result.chain] == [EPIC, LEAF]
    assert all(step.placement.repo_name == "other" for step in result.chain)
    assert all(step.placement.worktree.is_dir() for step in result.chain)
    assert git(tmp_path / "other", "rev-parse", result.chain[0].placement.branch)
    assert not (layout.worktrees_dir / "code").exists()


def test_workspace_only_owner_is_still_placed_in_leaf_repository(tmp_path):
    layout = _workspace(tmp_path)
    _item(layout, EPIC, repo=None, affects=("gw:workspace",))
    _item(layout)
    result = _run(layout)
    assert result.ok and len(result.chain) == 2
    assert result.chain[0].placement.worktree.is_dir()


def test_repo_name_on_owner(tmp_path):
    layout = _workspace(tmp_path, ("code", "other"))
    _item(layout, EPIC, repo=None)
    result = _run(layout, EPIC, repo_name="other")
    assert result.ok and result.placement.repo_name == "other"
    assert result.placement.worktree.is_dir()


def test_repository_resolution_errors_are_not_placement_refusals(tmp_path):
    layout = _workspace(tmp_path, ("code", "other"))
    _item(layout)
    with pytest.raises(WorkspaceError, match="conflicts"):
        _run(layout, repo_name="other")


def test_git_unavailable_retains_cause_and_detail(tmp_path):
    layout = _workspace(tmp_path)
    _item(layout)
    result = _run(layout, environ={"GW_GIT": str(tmp_path / "missing-git")})
    assert result.refusal.kind == "git-unavailable"
    assert "missing" in result.refusal.detail
    assert not (layout.worktrees_dir / "code").exists()


def test_default_base_uses_gate_resolved_executable(tmp_path, monkeypatch):
    layout = _workspace(tmp_path)
    _item(layout)
    repo = tmp_path / "code"
    git(repo, "branch", "trunk")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    executable = provenance.gate_git(layout)
    original = provenance.probe_git

    def gated(cwd, *args, **kwargs):
        if args == ("symbolic-ref", "refs/remotes/origin/HEAD"):
            assert kwargs.get("executable") == executable.path
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(provenance, "probe_git", gated)
    result = _run(layout)
    assert result.ok and result.placement.parent_branch == "trunk"


def test_start_records_baseline_and_remove_requires_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", ENV["GIT_AUTHOR_NAME"])
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", ENV["GIT_AUTHOR_EMAIL"])
    layout = _workspace(tmp_path)
    _item(layout)
    result = _run(layout, mode="start")
    assert result.ok and result.start_sha == (None, git(tmp_path / "code", "rev-parse", "main"))
    refused = _run(layout, mode="remove")
    assert refused.refusal.kind == "not-finished"
    _receipt(layout, result.placement)
    removed = _run(layout, mode="remove")
    assert removed.ok and removed.chain[0].action == "removed"
    assert not result.placement.worktree.exists()


def _placed_family(tmp_path):
    layout = _workspace(tmp_path)
    _item(layout, EPIC)
    _item(layout)
    placed = _run(layout)
    assert placed.ok
    return layout, placed.chain[0].placement, placed.chain[1].placement


def test_remove_owner_removes_own_worktree(tmp_path):
    layout, owner, _ = _placed_family(tmp_path)
    _receipt(layout, owner)
    removed = _run(layout, EPIC, mode="remove")
    assert removed.ok and removed.chain[0].action == "removed"
    assert not owner.worktree.exists()
    assert git(owner.repo_root, "rev-parse", f"refs/heads/{owner.branch}")


def test_remove_owner_keeps_children(tmp_path):
    layout, owner, child = _placed_family(tmp_path)
    _receipt(layout, owner)
    _receipt(layout, child)
    removed = _run(layout, EPIC, mode="remove")
    assert removed.ok and not owner.worktree.exists()
    assert child.worktree.is_dir()
    assert str(child.worktree) in git(owner.repo_root, "worktree", "list", "--porcelain")


def test_remove_owner_without_receipt_is_not_finished(tmp_path):
    layout, owner, _ = _placed_family(tmp_path)
    refused = _run(layout, EPIC, mode="remove")
    assert refused.refusal.kind == "not-finished"
    assert owner.worktree.is_dir()


def test_dry_run_does_not_create_checkout_or_commit_item(tmp_path):
    layout = _workspace(tmp_path)
    _item(layout)
    before = git(layout.root, "rev-parse", "HEAD")
    result = _run(layout, dry_run=True)
    assert result.ok and result.chain[-1].action == "created"
    assert not result.placement.worktree.exists()
    assert git(layout.root, "rev-parse", "HEAD") == before


def test_occupied_predicate_reaches_legacy_adoption(tmp_path):
    layout = _workspace(tmp_path)
    _item(layout)
    placement = _run(layout, dry_run=True).placement
    legacy = tmp_path / "legacy"
    git(tmp_path / "code", "worktree", "add", "-b", placement.branch, str(legacy), "main")
    result = _run(layout, occupied=lambda path: "live worker" if path == legacy else None)
    assert result.refusal.kind == "occupied" and "live worker" in result.refusal.detail
    assert legacy.is_dir() and not placement.worktree.exists()


@pytest.mark.parametrize("repo_name", [None, "code"])
def test_prune_walks_subtree_and_repos_descendants_before_owners(tmp_path, repo_name):
    layout = _workspace(tmp_path, ("code", "other"))
    _item(layout, EPIC, resolved=True)
    _item(layout, repo="other", resolved=True)
    _item(layout, OTHER, resolved=True)
    placements = (*[s.placement for s in _run(layout).chain], *[s.placement for s in _run(layout, OTHER).chain])
    for placement in placements:
        _receipt(layout, placement)
    result = _run(layout, EPIC, mode="prune", repo_name=repo_name)
    assert result.ok and result.placement.repo_name == "code"
    assert len(result.pruned.removed_worktrees) == 4
    for repo in ("code", "other"):
        rows = [row for row in result.pruned.kept if row.repo == repo]
        assert [row.path for row in rows] == [OTHER if repo == "code" else LEAF, EPIC]
    assert not any(placement.worktree.exists() for placement in placements)


def test_prune_includes_archived_descendants_and_limits_subtree(tmp_path):
    layout = _workspace(tmp_path)
    _item(layout, EPIC)
    archived = "work/_archive/epic-family/children/feature-old"
    outside = "work/feature-outside"
    _item(layout, archived)
    _item(layout, outside)
    for path in (EPIC, archived, outside):
        assert _run(layout, path).ok
    result = _run(layout, EPIC, mode="prune")
    assert result.ok
    assert [row.path for row in result.pruned.kept] == [archived, EPIC]
    assert outside not in [row.path for row in result.pruned.kept]


def test_prune_top_level_placement_uses_acting_repo_not_first_sorted_candidate(tmp_path):
    layout = _workspace(tmp_path, ("code", "other"))
    _item(layout, EPIC, repo="other")
    _item(layout)
    result = _run(layout, EPIC, mode="prune")
    assert result.ok and result.placement.repo_name == "other"
    assert result.placement.repo_root == tmp_path / "other"


def test_prune_without_path_sweeps_lane(tmp_path):
    layout = _workspace(tmp_path)
    for path in (LEAF, "work/feature-outside"):
        _item(layout, path)
        assert _run(layout, path).ok
    result = _run(layout, None, mode="prune")
    assert result.ok and result.path is None and result.placement is None
    assert {row.path for row in result.pruned.kept} == {LEAF, "work/feature-outside"}


def test_prune_owner_inherits_archived_descendant_repository(tmp_path):
    layout = _workspace(tmp_path, ("code", "other"))
    _item(layout, EPIC)
    _item(layout, repo="other")
    assert _run(layout).ok
    archived = "work/_archive/epic-family/children/feature-leaf"
    page = layout.bundle_dir / f"{archived}.md"
    page.parent.mkdir(parents=True)
    (layout.bundle_dir / f"{LEAF}.md").rename(page)
    result = _run(layout, EPIC, mode="prune")
    assert result.ok
    assert [(row.path, row.repo) for row in result.pruned.kept] == [(archived, "other"), (EPIC, "other")]


def test_prune_skips_unresolvable_items_but_keeps_descendant_owner_placements(tmp_path):
    layout = _workspace(tmp_path, ("code", "other"))
    _item(layout, EPIC, repo="missing")
    _item(layout)
    assert _run(layout).ok
    result = _run(layout, EPIC, mode="prune")
    assert result.ok and result.placement is None
    assert any(EPIC in warning and "missing" in warning for warning in result.warnings)
    assert [row.path for row in result.pruned.kept] == [LEAF, EPIC]


def test_prune_precommits_only_called_subtree(tmp_path):
    layout = _workspace(tmp_path)
    _item(layout, EPIC)
    _item(layout)
    _item(layout, "work/feature-outside")
    result = _run(layout, EPIC, mode="prune")
    assert result.ok and result.workspace_commit.status == "committed"
    assert git(layout.root, "status", "--porcelain").endswith("okf/work/feature-outside.md")
    assert "okf/work/feature-outside.md" in result.dirty_workspace_paths


@pytest.mark.parametrize("mode", ["ensure", "start", "remove"])
def test_path_required_for_non_prune(tmp_path, mode):
    with pytest.raises(ValueError, match="path"):
        _run(_workspace(tmp_path), None, mode=mode)
