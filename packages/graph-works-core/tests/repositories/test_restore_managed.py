from __future__ import annotations

from pathlib import Path

import pytest
from gitrepo import Upstream, git, make_upstream
from graph_works_core.repositories import commands
from graph_works_core.repositories.commands import run_repo_add, run_repo_restore
from graph_works_core.workspace.layout import WorkspaceLayout
from repositories_okf.git import GitFailure, remove_tree
from workspace_fixture import NOW, make_workspace, porcelain


@pytest.fixture
def upstream(tmp_path: Path) -> Upstream:
    (tmp_path / "up").mkdir()
    up = make_upstream(tmp_path / "up")
    up.commit({"README.md": "# demo\n"}, "c1")
    return up


@pytest.fixture
def layout(tmp_path: Path, upstream: Upstream) -> WorkspaceLayout:
    layout = make_workspace(tmp_path)
    assert run_repo_add(layout, upstream.url, name="demo", managed=True, now=NOW).ok
    return layout


def _clone(layout: WorkspaceLayout) -> Path:
    return layout.bundle_dir / "repositories" / "demo" / "references" / "git"


def _checkout(layout: WorkspaceLayout) -> Path:
    return layout.worktrees_dir / "demo" / "main"


def _summary(result: object) -> list[tuple[str, str, str | None, str | None]]:
    return [
        (o.name, o.outcome, o.checkout, o.refusal.code if o.refusal else None)
        for o in result.outcomes  # type: ignore[attr-defined]
    ]


def test_both_deleted_are_rebuilt_at_pin_and_track(layout: WorkspaceLayout) -> None:
    pinned = git(_clone(layout), "rev-parse", "HEAD")
    remove_tree(_checkout(layout))
    remove_tree(_clone(layout))
    commits = git(layout.root, "rev-list", "--count", "HEAD")
    result = run_repo_restore(layout)
    assert result.ok and _summary(result) == [("demo", "cloned", "checkout-created", None)]
    assert git(_clone(layout), "rev-parse", "HEAD") == pinned
    assert git(_checkout(layout), "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(layout.root, "rev-list", "--count", "HEAD") == commits
    assert porcelain(layout) == ""
    assert _summary(run_repo_restore(layout)) == [("demo", "present", "checkout-present", None)]


def test_a_missing_checkout_alone_is_recreated(layout: WorkspaceLayout) -> None:
    git(_clone(layout), "worktree", "remove", "--force", str(_checkout(layout)))
    assert _summary(run_repo_restore(layout)) == [("demo", "present", "checkout-created", None)]


def test_directly_deleted_checkout_is_recreated_despite_stale_registration(layout: WorkspaceLayout) -> None:
    remove_tree(_checkout(layout))
    result = run_repo_restore(layout)
    assert result.ok and _summary(result) == [("demo", "present", "checkout-created", None)]
    assert git(_checkout(layout), "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_recreating_checkout_preserves_other_missing_worktree_registration(layout: WorkspaceLayout) -> None:
    other = layout.worktrees_dir / "other"
    git(_clone(layout), "branch", "other", "HEAD")
    git(_clone(layout), "worktree", "add", "--quiet", str(other), "other")
    remove_tree(_checkout(layout))
    remove_tree(other)
    result = run_repo_restore(layout)
    assert result.ok and _summary(result) == [("demo", "present", "checkout-created", None)]
    assert str(other) in git(_clone(layout), "worktree", "list", "--porcelain")


def test_a_foreign_checkout_is_refused_and_left_alone(layout: WorkspaceLayout) -> None:
    git(_clone(layout), "worktree", "remove", "--force", str(_checkout(layout)))
    _checkout(layout).mkdir(parents=True)
    (_checkout(layout) / "mine.txt").write_text("keep\n", encoding="utf-8", newline="")
    result = run_repo_restore(layout)
    assert not result.ok and _summary(result) == [("demo", "present", "refused", "checkout-foreign")]
    assert (_checkout(layout) / "mine.txt").read_text(encoding="utf-8") == "keep\n"


def test_stale_registration_does_not_claim_a_foreign_replacement(layout: WorkspaceLayout) -> None:
    remove_tree(_checkout(layout))
    _checkout(layout).mkdir(parents=True)
    (_checkout(layout) / "mine.txt").write_text("keep\n", encoding="utf-8", newline="")
    result = run_repo_restore(layout)
    assert not result.ok and _summary(result) == [("demo", "present", "refused", "checkout-foreign")]
    assert (_checkout(layout) / "mine.txt").read_text(encoding="utf-8") == "keep\n"


def test_checkout_probe_failure_is_reported_as_git_failure(
    layout: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_probe(_git: object, _directory: Path) -> GitFailure:
        return GitFailure("nonzero", "git rev-parse --show-toplevel", "probe failed")

    monkeypatch.setattr(commands, "worktree_root", fail_probe)
    result = run_repo_restore(layout)
    assert not result.ok and _summary(result) == [("demo", "present", "refused", "git-failed")]
    assert "probe failed" in result.outcomes[0].refusal.detail  # type: ignore[union-attr]
    assert git(_checkout(layout), "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_no_declared_checkout_is_refused(layout: WorkspaceLayout) -> None:
    text = layout.manifest_path.read_text(encoding="utf-8")
    layout.manifest_path.write_text(
        "\n".join(line for line in text.splitlines() if "checkout:" not in line) + "\n",
        encoding="utf-8",
        newline="",
    )
    result = run_repo_restore(layout)
    assert _summary(result) == [("demo", "present", "refused", "no-checkout")]


def test_dry_run_reports_the_checkout_and_creates_nothing(layout: WorkspaceLayout) -> None:
    git(_clone(layout), "worktree", "remove", "--force", str(_checkout(layout)))
    assert _summary(run_repo_restore(layout, dry_run=True)) == [("demo", "present", "checkout-created", None)]
    assert not _checkout(layout).exists()


def test_dry_run_with_missing_clone_predicts_foreign_checkout(layout: WorkspaceLayout) -> None:
    remove_tree(_clone(layout))
    result = run_repo_restore(layout, dry_run=True)
    assert not result.ok and _summary(result) == [("demo", "cloned", "refused", "checkout-foreign")]
    assert _checkout(layout).exists() and not _clone(layout).exists()


def test_missing_clone_and_foreign_checkout_refuse_without_cloning(layout: WorkspaceLayout) -> None:
    remove_tree(_clone(layout))
    remove_tree(_checkout(layout))
    _checkout(layout).mkdir(parents=True)
    marker = _checkout(layout) / "mine.txt"
    marker.write_bytes(b"keep\n")
    result = run_repo_restore(layout)
    assert not result.ok and result.outcomes[0].refusal.code == "checkout-foreign"
    assert not _clone(layout).exists()
    assert marker.read_bytes() == b"keep\n"


def test_no_checkout_refuses_before_redetaching_clone(layout: WorkspaceLayout) -> None:
    git(_clone(layout), "switch", "-q", "-c", "reader-change")
    git(_clone(layout), "commit", "-q", "--allow-empty", "-m", "different tip")
    head = git(_clone(layout), "rev-parse", "HEAD")
    text = layout.manifest_path.read_text(encoding="utf-8")
    layout.manifest_path.write_text(
        "\n".join(line for line in text.splitlines() if "checkout:" not in line) + "\n",
        encoding="utf-8",
        newline="",
    )
    result = run_repo_restore(layout)
    assert not result.ok and result.outcomes[0].refusal.code == "no-checkout"
    assert git(_clone(layout), "rev-parse", "HEAD") == head
    assert git(_clone(layout), "branch", "--show-current") == "reader-change"
