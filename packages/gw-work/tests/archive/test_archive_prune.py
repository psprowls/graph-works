"""Archive prunes the swept items' worktrees and merged branches, and never fails because of it."""

from datetime import date

import gw_work.archive.commands as archive
import pytest
from gitrepo import git
from gw_pipeline.placement import NullNotifier
from gw_work.place import run_place
from workspace_fixture import make_workspace

TODAY = date(2026, 10, 22)
ITEM = "work/feature-leaf"


def _workspace(tmp_path):
    layout = make_workspace(tmp_path)
    repo = tmp_path / "code"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    git(repo, "commit", "-q", "--allow-empty", "-m", "Initial code")
    head = layout.manifest_path.read_text(encoding="utf-8").split("repositories:")[0]
    layout.manifest_path.write_text(
        head + f"repositories:\n  code:\n    path: {repo.as_posix()}\n", encoding="utf-8", newline="\n"
    )
    git(layout.root, "add", "-A")
    git(layout.root, "commit", "-q", "-m", "Declare repositories")
    return layout, repo


def _item(layout, *, work_status):
    page = layout.bundle_dir / f"{ITEM}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Feature\ntitle: Leaf\ndescription: Prune test\nstatus: stable\n"
        f"work_status: {work_status}\nphase: done\neffort: medium\nopened: 2026-10-22\n"
        "updated: 2026-10-22\nrepo: code\naffects: [src]\n---\n\n## Summary\nPrune test\n",
        encoding="utf-8",
        newline="\n",
    )
    git(layout.root, "add", "-A")
    git(layout.root, "commit", "-q", "-m", "item")


def _place(layout):
    result = run_place(layout, ITEM, mode="ensure", dry_run=False, repo_name=None, today=TODAY, notifier=NullNotifier())
    assert result.ok
    placement = result.placement
    (placement.worktree / "own.txt").write_text("work\n", encoding="utf-8")
    git(placement.worktree, "add", "own.txt")
    git(placement.worktree, "commit", "-q", "-m", "Own work")
    return placement


def _receipt(layout, placement):
    tip = git(placement.repo_root, "rev-parse", f"refs/heads/{placement.branch}")
    target = git(placement.repo_root, "rev-parse", "main")
    page = layout.bundle_dir / placement.path / "references" / "04-finish-receipt.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: Explanation\nreceipt_version: 2\nowner: {placement.path}\nintegrations:\n"
        f"  - repo: {placement.repo_name}\n    source_branch: {placement.branch}\n    source_commit: {tip}\n"
        f"    target_branch: main\n    result_commit: {target}\n    strategy: attested\n    evidence: accepted\n"
        "    accepted_by: Reviewer\n    reason: Reviewed integration\n---\n",
        encoding="utf-8",
        newline="\n",
    )


def _remove(layout):
    assert run_place(
        layout, ITEM, mode="remove", dry_run=False, repo_name=None, today=TODAY, notifier=NullNotifier()
    ).ok


def _branches(repo):
    return git(repo, "branch", "--list", "--format=%(refname:short)").split()


def _archive(layout, **kwargs):
    return archive.run_archive(layout, [ITEM], (), today=TODAY, dry_run=False, **kwargs)


def test_archive_resolved_squash_deletes_branch(tmp_path):
    layout, repo = _workspace(tmp_path)
    _item(layout, work_status="resolved")
    placement = _place(layout)
    _receipt(layout, placement)
    _remove(layout)
    git(layout.root, "add", "-A")
    git(layout.root, "commit", "-q", "--allow-empty", "-m", "receipt")
    run = _archive(layout)
    assert run.ok and run.prune_warnings == ()
    assert run.pruned.deleted_branches == (placement.branch,)
    assert placement.branch not in _branches(repo)


def test_archive_wontfix_keeps_branch(tmp_path):
    layout, repo = _workspace(tmp_path)
    _item(layout, work_status="wontfix")
    placement = _place(layout)
    run = _archive(layout)
    assert run.ok
    assert [(row.branch, row.why) for row in run.pruned.kept] == [(placement.branch, "no-receipt")]
    assert placement.branch in _branches(repo)


def test_archive_dirty_worktree_kept_and_reported(tmp_path):
    layout, _ = _workspace(tmp_path)
    _item(layout, work_status="resolved")
    placement = _place(layout)
    _receipt(layout, placement)
    (placement.worktree / "dirty.txt").write_text("x\n", encoding="utf-8")
    git(layout.root, "add", "-A")
    git(layout.root, "commit", "-q", "--allow-empty", "-m", "receipt")
    run = _archive(layout)
    assert run.ok
    assert [row.why for row in run.pruned.kept] == ["dirty"]
    assert placement.worktree.is_dir()


def test_archive_dry_run_reports_prune(tmp_path):
    layout, repo = _workspace(tmp_path)
    _item(layout, work_status="resolved")
    placement = _place(layout)
    _receipt(layout, placement)
    _remove(layout)
    git(layout.root, "add", "-A")
    git(layout.root, "commit", "-q", "--allow-empty", "-m", "receipt")
    run = archive.run_archive(layout, [ITEM], (), today=TODAY, dry_run=True)
    assert run.pruned is not None
    # Nothing has moved yet, so the preview sees the item un-archived: the branch is kept, not deleted.
    assert run.pruned.deleted_branches == ()
    assert [row.why for row in run.pruned.kept] == ["not-archived"]
    assert placement.branch in _branches(repo)
    assert (layout.bundle_dir / f"{ITEM}.md").exists()


def test_prune_failure_never_fails_archive(tmp_path, monkeypatch):
    layout, _ = _workspace(tmp_path)
    _item(layout, work_status="resolved")

    def boom(*args, **kwargs):
        raise OSError("disk on fire")

    monkeypatch.setattr(archive, "run_place", boom)
    run = _archive(layout)
    assert run.ok and run.result.ok
    assert len(run.prune_warnings) == 1 and "disk on fire" in run.prune_warnings[0]


def test_prune_refusal_is_a_warning(tmp_path, monkeypatch):
    layout, _ = _workspace(tmp_path)
    _item(layout, work_status="resolved")
    real = archive.run_place

    def refuse(*args, **kwargs):
        from dataclasses import replace

        from gw_pipeline.place import PlaceRefusal

        return replace(real(*args, **kwargs), refusal=PlaceRefusal("occupied", "busy"))

    monkeypatch.setattr(archive, "run_place", refuse)
    run = _archive(layout)
    assert run.ok and run.prune_warnings == ("prune work/_archive/feature-leaf: occupied: busy",)


@pytest.mark.parametrize("dry_run", [True, False])
def test_no_roots_means_no_prune(tmp_path, dry_run):
    layout, _ = _workspace(tmp_path)
    run = archive.run_archive(layout, [], (), today=TODAY, dry_run=dry_run)
    assert run.pruned is None and run.prune_warnings == ()
