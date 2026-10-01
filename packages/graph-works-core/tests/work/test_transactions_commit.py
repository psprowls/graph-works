"""`apply_mutation(commit=...)`: one commit, inside the bundle lock, only on success."""

from __future__ import annotations

import hashlib
import sys

import pytest
from _transaction_helpers import _git, _init_git, _plan, _workspace
from graph_works_core.workspace import transactions
from graph_works_core.workspace.anchors import open_anchor
from graph_works_core.workspace.commits import WorkspaceCommit
from graph_works_core.workspace.transactions import LOCK_TIMEOUT_FAILURE, apply_mutation, commit_pending
from work_tracker_okf.mutation import PlannedWrite

ITEM = "work/feature-a"


def _page(layout):
    member = f"{ITEM}.md"
    target = layout.bundle_dir / member
    target.parent.mkdir(parents=True, exist_ok=True)
    return member, target


def _seed(tmp_path):
    layout = _workspace(tmp_path)
    member, target = _page(layout)
    target.write_text(
        "---\ntype: Feature\ntitle: A\ndescription: d\nstatus: draft\nwork_status: open\n"
        "opened: 2026-08-01\nupdated: 2026-08-01\n---\n",
        encoding="utf-8",
        newline="\n",
    )
    _init_git(layout.root)
    return layout, member, target


def _rewrite(layout, member, target):
    before = target.read_bytes()
    after = before.replace(b"updated: 2026-08-01", b"updated: 2026-08-02")
    return _plan(
        layout, writes=(PlannedWrite(member, hashlib.sha256(before).hexdigest(), after),), validate_paths=(ITEM,)
    )


def test_success_commits_once_with_the_subject(tmp_path) -> None:
    layout, member, target = _seed(tmp_path)
    (layout.bundle_dir / ITEM / "references").mkdir(parents=True)
    (layout.bundle_dir / ITEM / "references" / "guidance-plan.md").write_text("g\n", encoding="utf-8", newline="\n")
    result = apply_mutation(
        layout,
        _rewrite(layout, member, target),
        commit=WorkspaceCommit("workspace: advance feature-a design -> plan", items=(ITEM,)),
    )
    assert result.ok and result.commit is not None and result.commit.status == "committed", result
    assert _git(layout.root, "log", "--format=%s").splitlines()[0] == "workspace: advance feature-a design -> plan"
    assert _git(layout.root, "status", "--porcelain", "--", "okf") == ""


def test_commit_none_never_commits(tmp_path) -> None:
    layout, member, target = _seed(tmp_path)
    result = apply_mutation(layout, _rewrite(layout, member, target), commit=None)
    assert result.ok and result.commit is None
    assert len(_git(layout.root, "log", "--format=%s").splitlines()) == 1


def test_failed_apply_does_not_commit(tmp_path) -> None:
    layout, member, _target = _seed(tmp_path)
    plan = _plan(layout, writes=(PlannedWrite(member, "0" * 64, b"stale\n"),), validate_paths=(ITEM,))
    result = apply_mutation(layout, plan, commit=WorkspaceCommit("workspace: t", items=(ITEM,)))
    assert not result.ok and result.commit is None
    assert len(_git(layout.root, "log", "--format=%s").splitlines()) == 1


def test_empty_plan_still_commits_dirty_item_references(tmp_path) -> None:
    layout, _member, _target = _seed(tmp_path)
    placement = layout.bundle_dir / ITEM / "references" / "orca-placement" / "k.json"
    placement.parent.mkdir(parents=True)
    placement.write_text("{}\n", encoding="utf-8", newline="\n")
    result = apply_mutation(
        layout, _plan(layout), commit=WorkspaceCommit("workspace: record feature-a execute placement", items=(ITEM,))
    )
    assert result.ok and result.commit is not None and result.commit.status == "committed"


def test_commit_failure_keeps_mutation_and_warns(tmp_path, monkeypatch) -> None:
    layout, member, target = _seed(tmp_path)
    (layout.root / ".git" / "index.lock").write_text("", encoding="utf-8", newline="\n")
    monkeypatch.setattr("graph_works_core.workspace.commits._sleep", lambda _s: None)
    result = apply_mutation(
        layout, _rewrite(layout, member, target), commit=WorkspaceCommit("workspace: t", items=(ITEM,))
    )
    assert result.ok and not result.rolled_back
    assert result.commit is not None and result.commit.status == "failed"
    assert any(w.startswith("workspace commit failed: ") for w in result.warnings)
    assert b"updated: 2026-08-02" in target.read_bytes()


def test_invalid_mode_raises_before_any_effect(tmp_path) -> None:
    layout, member, target = _seed(tmp_path)
    layout.local_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    layout.local_manifest_path.write_text("workflow:\n  workspace_commits: nope\n", encoding="utf-8", newline="\n")
    before = target.read_bytes()
    with pytest.raises(Exception, match="workspace_commits"):
        apply_mutation(layout, _rewrite(layout, member, target), commit=WorkspaceCommit("workspace: t"))
    assert target.read_bytes() == before


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock tier")
def test_lock_timeout_returns_failure_without_writing(tmp_path) -> None:
    layout, member, target = _seed(tmp_path)
    before = target.read_bytes()
    holder = open_anchor(layout.bundle_dir)
    try:
        with holder.exclusive_lock():
            result = apply_mutation(
                layout, _rewrite(layout, member, target), commit=WorkspaceCommit("workspace: t"), lock_timeout=0.2
            )
    finally:
        holder.close()
    assert result.failures == (LOCK_TIMEOUT_FAILURE,) and result.commit is None
    assert target.read_bytes() == before


def test_commit_pending_commits_under_the_lock(tmp_path) -> None:
    layout, _member, _target = _seed(tmp_path)
    extra = layout.bundle_dir / ITEM / "references" / "x.md"
    extra.parent.mkdir(parents=True)
    extra.write_text("x\n", encoding="utf-8", newline="\n")
    outcome = commit_pending(layout, WorkspaceCommit("workspace: t", items=(ITEM,)))
    assert outcome.status == "committed"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock tier")
def test_commit_pending_holds_bundle_lock(tmp_path, monkeypatch) -> None:
    layout, _member, _target = _seed(tmp_path)
    held: list[bool] = []
    real = transactions.commit_workspace

    def spy(*args, **kwargs):
        probe = open_anchor(layout.bundle_dir)
        try:
            from graph_works_core.workspace.anchors import LockTimeout

            try:
                with probe.exclusive_lock(timeout=0.05):
                    held.append(False)
            except LockTimeout:
                held.append(True)
        finally:
            probe.close()
        return real(*args, **kwargs)

    monkeypatch.setattr(transactions, "commit_workspace", spy)
    outcome = commit_pending(layout, WorkspaceCommit("workspace: t", items=(ITEM,)))
    assert outcome.status == "skipped"
    assert held == [True]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock tier")
def test_commit_pending_lock_timeout_returns_failure(tmp_path) -> None:
    layout, _member, _target = _seed(tmp_path)
    holder = open_anchor(layout.bundle_dir)
    try:
        with holder.exclusive_lock():
            outcome = commit_pending(layout, WorkspaceCommit("workspace: t", items=(ITEM,)), lock_timeout=0.2)
    finally:
        holder.close()
    assert outcome.status == "failed"
    assert outcome.reason == LOCK_TIMEOUT_FAILURE


def test_commit_runs_while_bundle_lock_is_held(tmp_path, monkeypatch) -> None:
    layout, member, target = _seed(tmp_path)
    held: list[bool] = []
    real = transactions.commit_workspace

    def spy(*args, **kwargs):
        probe = open_anchor(layout.bundle_dir)
        try:
            from graph_works_core.workspace.anchors import LockTimeout

            try:
                with probe.exclusive_lock(timeout=0.05):
                    held.append(False)
            except LockTimeout:
                held.append(True)
        finally:
            probe.close()
        return real(*args, **kwargs)

    monkeypatch.setattr(transactions, "commit_workspace", spy)
    if sys.platform == "win32":
        pytest.skip("POSIX flock tier")
    apply_mutation(layout, _rewrite(layout, member, target), commit=WorkspaceCommit("workspace: t"))
    assert held == [True]
