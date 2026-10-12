"""A held bundle lock spans Git and journaled mutations without reacquiring."""

import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from gw_kernel.workspace import anchors
from gw_kernel.workspace.bundle_lock import bundle_lock_held, bundle_root_lock
from gw_kernel.workspace.bundle_lock import commit_pending as kernel_commit_pending
from gw_kernel.workspace.bundle_lock import held_bundle_lock as kernel_held_bundle_lock
from gw_kernel.workspace.commits import WorkspaceCommit
from gw_kernel.workspace.layout import layout_for


def kernel_workspace(tmp_path):
    layout = layout_for(tmp_path / "kernel-workspace")
    layout.bundle_dir.mkdir(parents=True)
    layout.manifest_path.write_text("version: 1\n", encoding="utf-8", newline="\n")
    return layout


def kernel_git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def test_bundle_lock_context_is_nested_and_reset(tmp_path):
    layout = kernel_workspace(tmp_path)
    assert not bundle_lock_held(layout)
    with kernel_held_bundle_lock(layout):
        assert bundle_lock_held(layout)
        with kernel_held_bundle_lock(layout):
            assert bundle_lock_held(layout)
        assert bundle_lock_held(layout)
    assert not bundle_lock_held(layout)
    with pytest.raises(RuntimeError, match="stop"), kernel_held_bundle_lock(layout):
        assert bundle_lock_held(layout)
        raise RuntimeError("stop")
    assert not bundle_lock_held(layout)


def test_kernel_commit_pending_under_hold_makes_one_requested_commit(tmp_path):
    layout = kernel_workspace(tmp_path)
    kernel_git(layout.root, "init", "-q", "-b", "main")
    kernel_git(layout.root, "config", "user.name", "gw-test")
    kernel_git(layout.root, "config", "user.email", "gw-test@example.invalid")
    kernel_git(layout.root, "config", "commit.gpgsign", "false")
    hooks = layout.root / ".no-hooks"
    hooks.mkdir()
    kernel_git(layout.root, "config", "core.hooksPath", str(hooks))
    kernel_git(layout.root, "add", "-A")
    kernel_git(layout.root, "commit", "-qm", "workspace: seed")
    (layout.bundle_dir / "a.md").write_text("authored\n", encoding="utf-8", newline="\n")
    (layout.bundle_dir / "unrelated.md").write_text("other\n", encoding="utf-8", newline="\n")
    commit = WorkspaceCommit("workspace: kernel pending", extra_paths=("a.md",))
    with kernel_held_bundle_lock(layout):
        outcome = kernel_commit_pending(layout, commit, lock_timeout=0.1)
        assert outcome.status == "committed"
        assert kernel_commit_pending(layout, commit, lock_timeout=0.1).status == "skipped"
    assert kernel_git(layout.root, "rev-list", "--count", "HEAD") == "2"
    assert kernel_git(layout.root, "log", "-1", "--format=%s") == "workspace: kernel pending"
    assert kernel_git(layout.root, "show", "--format=", "--name-only", "HEAD") == "okf/a.md"
    assert kernel_git(layout.root, "status", "--porcelain", "--", "okf/unrelated.md") == "?? okf/unrelated.md"


def test_kernel_bundle_root_lock_times_out_then_acquires_after_release(tmp_path):
    layout = kernel_workspace(tmp_path)

    def acquire():
        assert not bundle_lock_held(layout)
        root = anchors.open_anchor(layout.bundle_dir)
        try:
            with bundle_root_lock(root, timeout=0.05):
                return "acquired"
        finally:
            root.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with kernel_held_bundle_lock(layout), pytest.raises(anchors.LockTimeout):
            pool.submit(acquire).result(2)
        assert pool.submit(acquire).result(2) == "acquired"
