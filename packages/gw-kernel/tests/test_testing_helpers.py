"""Direct contracts for installed seed-copy and transaction test support."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from gw_kernel.testing import transactions
from gw_kernel.testing.seed_copy import copy_tree_into, files_containing, rebase_layout
from gw_kernel.workspace.layout import layout_for


def test_seed_copy_preserves_bytes_and_rebases_every_layout_path(tmp_path: Path) -> None:
    old = tmp_path / "seed"
    old.mkdir()
    layout = layout_for(old, repo_root=old / "repo")
    layout.bundle_dir.mkdir()
    (layout.bundle_dir / "page.md").write_bytes(b"line\r\n" + str(old).encode("utf-8"))
    (old / "other.bin").write_bytes(b"\x00\xff")
    new = tmp_path / "copied"
    assert copy_tree_into(old, new) == new
    moved = rebase_layout(layout, old, new)
    for attribute in ("root", "config_dir", "cache_dir", "bundle_dir", "worktrees_dir", "repo_root"):
        assert getattr(moved, attribute) == new / getattr(layout, attribute).relative_to(old)
    assert (moved.bundle_dir / "page.md").read_bytes() == (layout.bundle_dir / "page.md").read_bytes()
    assert files_containing(new, str(old)) == [new / "okf/page.md"]
    assert files_containing(new, "absent") == []
    assert rebase_layout(layout_for(old), old, new).repo_root is None


def test_seed_substring_search_skips_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"needle")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"host cannot create a symlink: {exc}")
    assert files_containing(tmp_path, "needle") == [target]


def test_transaction_snapshot_tracks_bytes_modes_and_links_with_root_only_lock_exclusion(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    (root / transactions.BUNDLE_LOCK_NAME).write_bytes(b"control lock")
    directory = root / "nested"
    directory.mkdir()
    page = directory / transactions.BUNDLE_LOCK_NAME
    page.write_bytes(b"nested content")
    link = root / "link"
    try:
        link.symlink_to(Path("nested") / transactions.BUNDLE_LOCK_NAME)
    except OSError as exc:
        pytest.skip(f"host cannot create a symlink: {exc}")
    found = transactions.snapshot(root)
    assert set(found) == {"nested", f"nested/{transactions.BUNDLE_LOCK_NAME}", "link"}
    assert found["nested"] == ("directory", stat.S_IMODE(directory.lstat().st_mode), None)
    assert found[f"nested/{transactions.BUNDLE_LOCK_NAME}"] == (
        "file",
        stat.S_IMODE(page.lstat().st_mode),
        b"nested content",
    )
    assert found["link"] == (
        "symlink",
        stat.S_IMODE(link.lstat().st_mode),
        str(Path("nested") / transactions.BUNDLE_LOCK_NAME),
    )
    page.write_bytes(b"changed")
    assert transactions.snapshot(root) != found


@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("mode", [0o444, 0o666, 0o751])
def test_representable_mode_models_posix_and_windows(monkeypatch, directory: bool, mode: int) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(transactions.sys, "platform", "linux")
        assert transactions.representable_mode(mode, directory=directory) == mode
        patch.setattr(transactions.sys, "platform", "win32")
        expected = (
            (0o777 if mode & stat.S_IWRITE else 0o555) if directory else (0o666 if mode & stat.S_IWRITE else 0o444)
        )
        assert transactions.representable_mode(mode, directory=directory) == expected


def test_workspace_commit_assertion_requires_exact_subject_empty_body_and_clean_work(tmp_path: Path) -> None:
    layout = transactions.workspace(tmp_path)
    transactions.init_git(layout.root)
    transactions.assert_workspace_commit(layout.root, "workspace: seed")
    with pytest.raises(AssertionError):
        transactions.assert_workspace_commit(layout.root, "wrong subject")
    (layout.bundle_dir / "work").mkdir()
    (layout.bundle_dir / "work/untracked.md").write_bytes(b"uncommitted")
    with pytest.raises(AssertionError):
        transactions.assert_workspace_commit(layout.root, "workspace: seed")
    transactions.git(layout.root, "add", "-A")
    transactions.git(layout.root, "commit", "-q", "-m", "subject", "-m", "body")
    with pytest.raises(AssertionError):
        transactions.assert_workspace_commit(layout.root, "subject")


def test_init_git_without_commit_has_no_head(tmp_path: Path) -> None:
    root = transactions.init_git(tmp_path, commit=False)
    assert transactions.git(root, "status", "--porcelain") == ""
    assert transactions.git(root, "symbolic-ref", "--short", "HEAD").strip() == "main"
